"""HTTP API. All endpoints accept an optional `as_of` date for point-in-time views."""

import math
import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.comps import MULTIPLES
from app.db import SessionLocal
from app.edgar.normalize import STANDARD_FIELDS
from app.fields import FIELDS, SCREEN_COLUMNS
from app.models import Company, Price
from app.schemas import (
    CompanyDetail,
    CompanySummary,
    CompsResponse,
    Meta,
    ScreenResponse,
    ValuationResponse,
)
from app.screener import PRESETS, Filter, Screen, run_screen
from app.services import company_detail, comps_detail, comps_table, dcf_analysis, find_company
from app.services import football_field as build_football_field
from app.universe import PEER_GROUPS

router = APIRouter(prefix="/api")

MULTIPLE_LABELS = {"ev_revenue": "EV/Revenue", "ev_ebitda": "EV/EBITDA", "ev_ebit": "EV/EBIT",
                   "pe": "P/E"}  # fmt: skip
HISTOGRAM_BINS = 40


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
AsOf = Annotated[date | None, Query(description="Point-in-time date (default: today)")]


def clean(value: Any) -> Any:
    """Make pandas/numpy/Decimal values JSON-safe; NaN and infinities become null."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        return None if not math.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.date()
    if value is pd.NaT:
        return None
    return value


def records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return [clean(r) for r in df.to_dict(orient="records")]


def _company_or_404(session: Session, ticker: str) -> Company:
    company = find_company(session, ticker)
    if company is None:
        raise HTTPException(404, f"Unknown ticker {ticker.upper()}")
    return company


@router.get("/meta", response_model=Meta)
def meta(session: SessionDep):
    groups = dict(
        session.execute(
            select(Company.sector, func.array_agg(func.distinct(Company.peer_group))).group_by(
                Company.sector
            )
        ).all()
    )
    return Meta(
        latest_price_date=session.scalar(select(func.max(Price.trade_date))),
        company_count=session.scalar(select(func.count(Company.id))) or 0,
        sectors=[
            {"name": s, "peer_groups": sorted(g for g in groups.get(s, []) if g)}
            for s in PEER_GROUPS
            if s in groups
        ],
        presets=[
            {
                "name": p.name,
                "description": p.description,
                "filters": [vars(f) for f in p.filters],
                "factors": [vars(f) for f in p.factors],
            }
            for p in PRESETS.values()
        ],
        fields=[
            {"name": n, "label": label, "format": fmt, "description": desc}
            for n, (label, fmt, desc) in FIELDS.items()
        ],
        screen_columns=SCREEN_COLUMNS,
    )


@router.get("/screen", response_model=ScreenResponse)
def screen(
    as_of: AsOf = None,
    preset: str = "target",
    filter: Annotated[list[str], Query(description="e.g. ev_ebitda<15 or market_cap<=20B")] = [],  # noqa: B006
    replace_filters: bool = Query(False, description="Use only `filter`, not the preset's"),
    sector: Annotated[list[str] | None, Query()] = None,
    peer_group: Annotated[list[str] | None, Query()] = None,
):
    if preset not in PRESETS:
        raise HTTPException(422, f"Unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    try:
        extra = [Filter.parse(f) for f in filter]
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    base = PRESETS[preset]
    chosen = Screen(
        name=base.name,
        description=base.description,
        filters=extra if replace_filters else [*base.filters, *extra],
        factors=base.factors,
    )

    as_of = as_of or date.today()
    start = time.perf_counter()
    comps = comps_table(as_of)
    try:
        result = run_screen(comps, chosen, sectors=sector, peer_groups=peer_group)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    elapsed = (time.perf_counter() - start) * 1000

    return ScreenResponse(
        as_of=as_of,
        preset=preset,
        filters=[vars(f) for f in chosen.filters],
        universe=len(comps),
        matched=len(result),
        elapsed_ms=round(elapsed, 1),
        rows=records(result, SCREEN_COLUMNS),
    )


@router.get("/companies", response_model=list[CompanySummary])
def companies(session: SessionDep, q: str = "", limit: int = Query(20, le=500)):
    stmt = select(Company).order_by(Company.ticker).limit(limit)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Company.ticker.ilike(pattern) | Company.name.ilike(pattern))
        # Exact ticker matches first
        stmt = stmt.order_by(None).order_by((Company.ticker != q.upper()), Company.ticker)
    return [
        CompanySummary(ticker=c.ticker, name=c.name, sector=c.sector, peer_group=c.peer_group)
        for c in session.scalars(stmt)
    ]


@router.get("/companies/{ticker}", response_model=CompanyDetail)
def company(ticker: str, session: SessionDep, as_of: AsOf = None):
    as_of = as_of or date.today()
    c = _company_or_404(session, ticker)
    d = company_detail(session, c, as_of)
    statements = [
        clean(
            {
                "fiscal_year": s.fiscal_year,
                "period_end": s.period_end,
                "filed_date": s.filed_date,
                "form": s.form,
                **{f: getattr(s, f) for f in STANDARD_FIELDS},
            }
        )
        for s in d["statements"]
    ]
    snapshot = d["snapshot"]
    return CompanyDetail(
        ticker=c.ticker,
        name=c.name,
        cik=c.cik,
        sector=c.sector,
        peer_group=c.peer_group,
        industry=c.industry,
        fiscal_year_end=c.fiscal_year_end,
        is_active=c.is_active,
        as_of=as_of,
        snapshot=clean(snapshot.to_dict()) if snapshot is not None else None,
        statements=statements,
        metrics=records(d["metrics"].drop(columns=["company_id", "statement_id"], errors="ignore")),
        flags={k: int(v) for k, v in d["flag_counts"].items()},
    )


@router.get("/companies/{ticker}/comps", response_model=CompsResponse)
def comps(ticker: str, session: SessionDep, as_of: AsOf = None):
    as_of = as_of or date.today()
    c = _company_or_404(session, ticker)
    detail = comps_detail(c.id, as_of)
    if detail is None:
        raise HTTPException(422, f"No valuation for {c.ticker} on {as_of} (missing price?)")
    t, peers = detail
    return CompsResponse(
        as_of=as_of,
        peer_group=t["peer_group"],
        target=clean(t.to_dict()),
        multiples=[
            clean(
                {
                    "multiple": m,
                    "label": MULTIPLE_LABELS[m],
                    "company": t[m],
                    "peer_median": t[f"{m}_peer_median"],
                    "vs_peers": t[f"{m}_vs_peers"],
                    "implied_price": t[f"implied_price_{m}"],
                }
            )
            for m in MULTIPLES
        ],
        implied_price_median=clean(t["implied_price_median"]),
        upside=clean(t["upside_to_comps"]),
        peers=records(
            peers.sort_values("market_cap", ascending=False),
            [
                "ticker",
                "name",
                "price",
                "market_cap",
                "ev",
                "revenue",
                "revenue_growth",
                "ebitda_margin",
                "ev_revenue",
                "ev_ebitda",
                "ev_ebit",
                "pe",
            ],
        ),  # fmt: skip
    )


ASSUMPTION_LABELS = [
    ("growth_start", "Revenue growth, year 1", "pct"),
    ("terminal_growth", "Terminal growth", "pct"),
    ("margin_start", "EBIT margin, year 1", "pct"),
    ("margin_end", "EBIT margin, final year", "pct"),
    ("tax_rate", "Tax rate", "pct"),
    ("d_and_a_pct", "D&A, % of revenue", "pct"),
    ("capex_pct", "Capex, % of revenue", "pct"),
    ("nwc_pct", "NWC, % of incremental revenue", "pct"),
    ("wacc", "WACC", "pct"),
]


@router.get("/companies/{ticker}/valuation", response_model=ValuationResponse)
def valuation(
    ticker: str,
    session: SessionDep,
    as_of: AsOf = None,
    growth: float | None = Query(None, ge=-0.5, le=1.0, description="Year-1 revenue growth"),
    margin: float | None = Query(None, ge=-1.0, le=1.0, description="EBIT margin, all years"),
    wacc: float | None = Query(None, gt=0.0, le=0.3),
    terminal_growth: float | None = Query(None, ge=-0.02, le=0.06),
    erp: float = Query(0.05, ge=0.0, le=0.15, description="Equity risk premium"),
    years: int = Query(10, ge=3, le=20),
    draws: int = Query(10_000, ge=0, le=50_000),
):
    """DCF, sensitivity grid, Monte Carlo and the football field for one company."""
    as_of = as_of or date.today()
    c = _company_or_404(session, ticker)
    overrides = {"growth_start": growth, "margin": margin, "wacc": wacc,
                 "terminal_growth": terminal_growth}  # fmt: skip
    try:
        analysis = dcf_analysis(session, c.id, as_of, overrides, erp, years, draws)
    except LookupError as e:
        raise HTTPException(422, f"{c.ticker}: {e}") from e

    a, result, inputs = analysis.assumptions, analysis.result, analysis.assumptions.inputs
    assumptions = [
        {
            "key": key,
            "label": label,
            "value": getattr(inputs, key),
            "format": fmt,
            "note": a.notes.get(key, ""),
            "overridden": a.notes.get(key) == "override",
        }
        for key, label, fmt in ASSUMPTION_LABELS
    ]
    assumptions += [
        {"key": "beta", "label": "Beta (relevered)", "value": a.wacc.beta, "format": "number",
         "note": a.notes["beta"], "overridden": False},
        {"key": "cost_of_equity", "label": "Cost of equity", "value": a.wacc.cost_of_equity,
         "format": "pct", "note": "CAPM", "overridden": False},
        {"key": "cost_of_debt", "label": "Pre-tax cost of debt",
         "value": a.wacc.pre_tax_cost_of_debt, "format": "pct", "note": a.notes["cost_of_debt"],
         "overridden": False},
    ]  # fmt: skip
    wacc_row = next(r for r in assumptions if r["key"] == "wacc")
    if not wacc_row["overridden"]:
        wacc_row["note"] = a.notes["wacc"]

    projection = result.projection.T.reset_index(names="year")
    grid = analysis.sensitivity

    mc = analysis.monte_carlo
    monte_carlo = None
    if mc is not None:
        lo, hi = np.percentile(mc.values, [1, 99])
        counts, edges = np.histogram(np.clip(mc.values, lo, hi), bins=HISTOGRAM_BINS)
        monte_carlo = {
            "draws": mc.draws,
            "discarded": mc.discarded,
            "percentiles": {f"p{q}": v for q, v in mc.percentiles.items()},
            "prob_above_price": mc.prob_above_price,
            "growth_volatility": a.growth_volatility,
            "margin_volatility": a.margin_volatility,
            "histogram": [
                {"x0": float(edges[i]), "x1": float(edges[i + 1]), "count": int(counts[i])}
                for i in range(len(counts))
            ],
        }

    return ValuationResponse(
        as_of=as_of,
        ticker=c.ticker,
        fiscal_year=int(a.history["fiscal_year"].iloc[-1]),
        assumptions=clean(assumptions),
        projection=records(projection),
        bridge=clean(
            {
                "pv_explicit": result.pv_explicit,
                "terminal_value": result.terminal_value,
                "pv_terminal": result.pv_terminal,
                "terminal_share": result.terminal_share,
                "enterprise_value": result.enterprise_value,
                "net_debt": inputs.net_debt,
                "other_claims": inputs.other_claims,
                "equity_value": result.equity_value,
                "shares": inputs.shares,
                "value_per_share": result.value_per_share,
                "price": a.price,
                "upside": result.value_per_share / a.price - 1,
            }
        ),
        sensitivity={
            "waccs": grid.index.tolist(),
            "growths": grid.columns.tolist(),
            "values": clean(grid.to_numpy().tolist()),
        },
        monte_carlo=monte_carlo,
        football_field=clean(build_football_field(session, c.id, as_of, analysis)),
    )
