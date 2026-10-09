"""Read-side operations shared by the API and the CLI.

The comps table for a date (valuation snapshot + peer comparison over the whole
universe) is the expensive piece and only changes when data is re-ingested, so it
is cached per as-of date. Callers get a copy and can't corrupt the cache.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.comps import MULTIPLES, add_peer_comparison, valuation_snapshot
from app.db import SessionLocal
from app.dcf import DCFResult, run_dcf, sensitivity, with_overrides
from app.dcf_inputs import Assumptions, build_assumptions
from app.market import _split_adjusted_closes
from app.models import Company, DataQualityFlag
from app.monte_carlo import MonteCarloResult, simulate
from app.queries import metrics_as_of, statements_as_of

FOOTBALL_FIELD_QUANTILES = (0.25, 0.75)


@lru_cache(maxsize=16)
def _comps_cached(as_of: date) -> pd.DataFrame:
    with SessionLocal() as session:
        return add_peer_comparison(valuation_snapshot(session, as_of))


def comps_table(as_of: date) -> pd.DataFrame:
    return _comps_cached(as_of).copy()


def clear_cache() -> None:
    _comps_cached.cache_clear()


def find_company(session: Session, ticker: str) -> Company | None:
    return session.scalar(
        select(Company).where(Company.ticker == ticker.upper()).order_by(Company.is_active.desc())
    )


def company_detail(session: Session, company: Company, as_of: date) -> dict:
    statements = statements_as_of(session, as_of, [company.id])
    metrics = metrics_as_of(session, as_of, [company.id])
    comps = comps_table(as_of)
    row = comps[comps["company_id"] == company.id]
    flag_counts = dict(
        session.execute(
            select(DataQualityFlag.severity, func.count())
            .where(DataQualityFlag.company_id == company.id)
            .group_by(DataQualityFlag.severity)
        ).all()
    )
    return {
        "company": company,
        "statements": statements,
        "metrics": metrics,
        "snapshot": row.iloc[0] if not row.empty else None,
        "flag_counts": flag_counts,
    }


def comps_detail(company_id: int, as_of: date) -> tuple[pd.Series, pd.DataFrame] | None:
    comps = comps_table(as_of)
    target = comps[comps["company_id"] == company_id]
    if target.empty:
        return None
    t = target.iloc[0]
    peers = comps[(comps["peer_group"] == t["peer_group"]) & (comps["company_id"] != company_id)]
    return t, peers


@dataclass
class DCFAnalysis:
    assumptions: Assumptions
    result: DCFResult
    sensitivity: pd.DataFrame
    monte_carlo: MonteCarloResult | None


def dcf_analysis(
    session: Session,
    company_id: int,
    as_of: date,
    overrides: dict[str, float | None] | None = None,
    equity_risk_premium: float = 0.05,
    years: int = 10,
    draws: int = 10_000,
    seed: int = 0,
) -> DCFAnalysis:
    """DCF with optional overrides of growth_start, margin, wacc, terminal_growth.

    `margin` sets both the starting and ending EBIT margin.
    """
    a = build_assumptions(
        session,
        company_id,
        as_of,
        snapshot=comps_table(as_of),
        equity_risk_premium=equity_risk_premium,
        years=years,
    )
    overrides = dict(overrides or {})
    margin = overrides.pop("margin", None)
    if margin is not None:
        overrides["margin_start"] = overrides["margin_end"] = margin
    a.inputs = with_overrides(a.inputs, **overrides)
    for key, value in overrides.items():
        if value is not None:
            a.notes[key] = "override"
    return DCFAnalysis(
        assumptions=a,
        result=run_dcf(a.inputs),
        sensitivity=sensitivity(a.inputs),
        monte_carlo=simulate(a, draws=draws, seed=seed) if draws else None,
    )


def trading_range(session: Session, company_id: int, as_of: date) -> tuple[float, float] | None:
    """52-week low/high close, split-adjusted to be comparable with the as-of price."""
    closes = _split_adjusted_closes(session, [company_id], as_of - timedelta(days=365), as_of)
    if closes.empty:
        return None
    series = closes[company_id].dropna()
    return float(series.min()), float(series.max())


def _implied_price(t: pd.Series, multiple: str, value: float) -> float:
    numerator, denominator = MULTIPLES[multiple]
    metric = t[denominator]
    if pd.isna(metric) or metric <= 0 or pd.isna(value):
        return np.nan
    implied = value * metric
    equity = implied - t["net_debt"] - t["other_claims"] if numerator == "ev" else implied
    return equity / t["shares"]


def football_field(session: Session, company_id: int, as_of: date, dcf: DCFAnalysis) -> dict:
    """Valuation ranges per method, all in price per share.

    - 52-week trading range
    - Comps: peer-group interquartile range of each multiple applied to the company
    - DCF: min/max of the WACC x terminal growth sensitivity grid, with base case
    - Monte Carlo: P10-P90, with median
    """
    detail = comps_detail(company_id, as_of)
    bars = []

    low_high = trading_range(session, company_id, as_of)
    if low_high:
        bars.append({"method": "52-week range", "low": low_high[0], "high": low_high[1],
                     "mid": None, "kind": "market"})  # fmt: skip

    if detail:
        t, peers = detail
        labels = {"ev_revenue": "EV/Revenue", "ev_ebitda": "EV/EBITDA", "ev_ebit": "EV/EBIT",
                  "pe": "P/E"}  # fmt: skip
        for multiple, label in labels.items():
            values = peers[multiple].dropna()
            if len(values) < 3:
                continue
            q_lo, q_hi = values.quantile(list(FOOTBALL_FIELD_QUANTILES))
            low, high = _implied_price(t, multiple, q_lo), _implied_price(t, multiple, q_hi)
            if np.isnan(low) or np.isnan(high):
                continue
            bars.append({
                "method": f"Comps · {label}",
                "low": low,
                "high": high,
                "mid": _implied_price(t, multiple, float(values.median())),
                "kind": "comps",
                "detail": f"peer {label} {q_lo:.1f}x-{q_hi:.1f}x (interquartile, n={len(values)})",
            })  # fmt: skip

    grid = dcf.sensitivity.to_numpy()
    if np.isfinite(grid).any():
        bars.append({
            "method": "DCF",
            "low": float(np.nanmin(grid)),
            "high": float(np.nanmax(grid)),
            "mid": dcf.result.value_per_share,
            "kind": "dcf",
            "detail": "WACC ±1%, terminal growth ±1%",
        })  # fmt: skip
    if dcf.monte_carlo is not None:
        p = dcf.monte_carlo.percentiles
        bars.append({
            "method": "Monte Carlo",
            "low": p[10],
            "high": p[90],
            "mid": p[50],
            "kind": "monte_carlo",
            "detail": f"P10-P90 of {len(dcf.monte_carlo.values):,} draws",
        })  # fmt: skip

    return {"price": dcf.assumptions.price, "bars": bars}
