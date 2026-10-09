"""Valuation snapshot and comparable-company analysis, point in time.

Enterprise value bridge (simplified):

    EV = market cap + total debt - cash - short-term investments
         + minority interest + temporary equity (e.g. redeemable preferred)

Not included: operating leases, long-term investments, equity-method stakes.
Fundamentals are the latest *annual* figures filed by the as-of date (no trailing
twelve months yet), so multiples can lag by up to a year plus filing time.

Comps: each multiple is compared with the median of the company's peer group,
*excluding the company itself*. Applying the peer median to the company's own metric
gives an implied enterprise value, then equity value and price per share.
"""

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market import market_as_of
from app.models import Company, FinancialStatement
from app.queries import metrics_as_of

# A company whose latest annual period ended longer ago than this has stopped filing.
MAX_STALENESS_DAYS = 550

# multiple -> (numerator, denominator); numerator is "ev" or "market_cap"
MULTIPLES: dict[str, tuple[str, str]] = {
    "ev_revenue": ("ev", "revenue"),
    "ev_ebitda": ("ev", "ebitda"),
    "ev_ebit": ("ev", "operating_income"),
    "pe": ("market_cap", "net_income"),
}


def valuation_snapshot(session: Session, as_of: date) -> pd.DataFrame:
    """One row per company: identity, latest fundamentals and metrics, market data,
    EV and multiples, all as known on `as_of`."""
    metrics = metrics_as_of(session, as_of, latest_only=True)
    if metrics.empty:
        return metrics

    statement_ids = metrics["statement_id"].tolist()
    fundamentals = pd.DataFrame(
        session.execute(
            select(
                FinancialStatement.id.label("statement_id"),
                FinancialStatement.operating_income,
                FinancialStatement.net_income,
                FinancialStatement.total_debt,
                FinancialStatement.cash,
                FinancialStatement.short_term_investments,
                FinancialStatement.minority_interest,
                FinancialStatement.temporary_equity,
            ).where(FinancialStatement.id.in_(statement_ids))
        ).all()
    )
    companies = pd.DataFrame(
        session.execute(
            select(
                Company.id.label("company_id"),
                Company.name,
                Company.sector,
                Company.peer_group,
            )
        ).all()
    )

    df = (
        metrics.merge(fundamentals, on="statement_id")
        .merge(companies, on="company_id")
        .merge(market_as_of(session, as_of), on="company_id", how="left")
    )
    numeric = [
        "operating_income",
        "net_income",
        "total_debt",
        "cash",
        "short_term_investments",
        "minority_interest",
        "temporary_equity",
    ]
    df[numeric] = df[numeric].astype(float)

    staleness = (pd.Timestamp(as_of) - pd.to_datetime(df["period_end"])).dt.days
    df = df[staleness <= MAX_STALENESS_DAYS].copy()

    # Claims senior to or alongside common equity that a buyer also takes on.
    df["other_claims"] = df["minority_interest"].fillna(0) + df["temporary_equity"].fillna(0)
    df["ev"] = df["market_cap"] + df["net_debt"] + df["other_claims"]
    for name, (num, den) in MULTIPLES.items():
        # Multiples on negative earnings are meaningless, not "cheap".
        df[name] = np.where(df[den] > 0, df[num] / df[den], np.nan)
    df["fcf_yield"] = np.where(df["market_cap"] > 0, df["fcf"] / df["market_cap"], np.nan)
    return df.reset_index(drop=True)


def _median_excluding_self(values: pd.Series) -> pd.Series:
    arr = values.to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    for i in range(len(arr)):
        others = np.delete(arr, i)
        others = others[~np.isnan(others)]
        if len(others):
            out[i] = np.median(others)
    return pd.Series(out, index=values.index)


def add_peer_comparison(snapshot: pd.DataFrame, group_by: str = "peer_group") -> pd.DataFrame:
    """Add peer medians, premium/discount to peers, and comps-implied share prices."""
    df = snapshot.copy()
    for multiple, (numerator, denominator) in MULTIPLES.items():
        median = df.groupby(group_by)[multiple].transform(_median_excluding_self)
        df[f"{multiple}_peer_median"] = median
        df[f"{multiple}_vs_peers"] = df[multiple] / median - 1

        implied = median * df[denominator].where(df[denominator] > 0)
        equity = implied - df["net_debt"] - df["other_claims"] if numerator == "ev" else implied
        df[f"implied_price_{multiple}"] = equity / df["shares"]

    implied_cols = [f"implied_price_{m}" for m in MULTIPLES]
    df["implied_price_median"] = df[implied_cols].median(axis=1, skipna=True)
    df["upside_to_comps"] = df["implied_price_median"] / df["price"] - 1
    df["peer_count"] = df.groupby(group_by)["company_id"].transform("count") - 1
    return df
