"""Default DCF assumptions for a company, built from what was known on `as_of`.

Operating assumptions come from the company's own last three fiscal years. The
discount rate is a bottom-up WACC:

    beta: 5y weekly regression vs SPY, Blume-adjusted, unlevered (Hamada) for each
          company in the peer group, peer median, relevered at the company's own
          market debt/equity. Single-stock betas are noisy; the peer median isn't.
    cost of equity = 10y Treasury + beta x equity risk premium      (CAPM)
    cost of debt   = interest expense / total debt, bounded to [rf + 0.5%, rf + 6%]
    weights        = market cap and gross debt

Defaults are deliberately simple and every one can be overridden. `notes` records
where each number came from so the output is auditable.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.comps import valuation_snapshot
from app.dcf import WACC, DCFInputs, blume_adjust, relever_beta, unlever_beta
from app.market import betas, risk_free_rate
from app.queries import statements_as_of

EQUITY_RISK_PREMIUM = 0.05
MAX_TERMINAL_GROWTH = 0.025  # and never above the risk-free rate
NWC_PCT = 0.10  # we don't store working capital yet; ~10% of incremental revenue
MARGINAL_TAX = 0.21
BETA_YEARS = 5
HISTORY_YEARS = 3


@dataclass
class Assumptions:
    ticker: str
    name: str
    as_of: date
    inputs: DCFInputs
    wacc: WACC
    price: float
    history: pd.DataFrame
    growth_volatility: float
    margin_volatility: float
    notes: dict[str, str] = field(default_factory=dict)


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(min(max(x, lo), hi))


def _history(session: Session, company_id: int, as_of: date) -> pd.DataFrame:
    cols = ["fiscal_year", "period_end", "revenue", "operating_income", "d_and_a", "capex",
            "pretax_income", "income_tax", "interest_expense", "total_debt"]  # fmt: skip
    rows = statements_as_of(session, as_of, [company_id])
    df = pd.DataFrame([{c: getattr(s, c) for c in cols} for s in rows], columns=cols)
    num = cols[2:]
    df[num] = df[num].astype(float)
    df = df.dropna(subset=["revenue"]).tail(8).reset_index(drop=True)
    gap = pd.to_datetime(df["period_end"]).diff().dt.days
    df["growth"] = np.where(gap.between(350, 380), df["revenue"].pct_change(), np.nan)
    df["ebit_margin"] = df["operating_income"] / df["revenue"]
    return df


def _bottom_up_beta(
    session: Session, snapshot: pd.DataFrame, company: pd.Series, as_of: date
) -> tuple[float, str]:
    group = snapshot[snapshot["peer_group"] == company["peer_group"]]
    raw = betas(session, as_of, group["company_id"].tolist(), years=BETA_YEARS)
    debt_to_equity = (group["total_debt"].fillna(0) / group["market_cap"]).to_numpy()
    unlevered = unlever_beta(
        blume_adjust(raw.reindex(group["company_id"]).to_numpy()), debt_to_equity, MARGINAL_TAX
    )
    median_unlevered = float(np.nanmedian(unlevered))
    own_de = _debt_to_equity(company, group)
    beta = float(relever_beta(median_unlevered, own_de, MARGINAL_TAX))
    n = int(np.isfinite(unlevered).sum())
    return beta, (
        f"peer median unlevered beta {median_unlevered:.2f} ({n} {company['peer_group']} "
        f"companies, {BETA_YEARS}y weekly vs SPY, Blume-adjusted), relevered at D/E {own_de:.2f}"
        + ("" if pd.notna(company["market_cap"]) else " (peer median: no price for company)")
    )


def _debt_to_equity(company: pd.Series, group: pd.DataFrame) -> float:
    """Market D/E; for a company without a price (an acquired target), the peer median."""
    if pd.notna(company["market_cap"]) and company["market_cap"] > 0:
        return (company["total_debt"] if pd.notna(company["total_debt"]) else 0) / company[
            "market_cap"
        ]
    ratios = group["total_debt"].fillna(0) / group["market_cap"]
    return float(np.nanmedian(ratios[np.isfinite(ratios)])) if np.isfinite(ratios).any() else 0.0


def build_assumptions(
    session: Session,
    company_id: int,
    as_of: date,
    snapshot: pd.DataFrame | None = None,
    equity_risk_premium: float = EQUITY_RISK_PREMIUM,
    years: int = 10,
) -> Assumptions:
    snapshot = valuation_snapshot(session, as_of) if snapshot is None else snapshot
    match = snapshot[snapshot["company_id"] == company_id]
    if match.empty or pd.isna(match.iloc[0]["shares"]):
        raise LookupError("No financials or share count for this company on that date")
    company = match.iloc[0]
    has_price = pd.notna(company["market_cap"])

    hist = _history(session, company_id, as_of)
    if hist.empty:
        raise LookupError("No revenue history in filings before this date")
    recent = hist.tail(HISTORY_YEARS)
    notes: dict[str, str] = {}

    if len(hist) > HISTORY_YEARS:
        start, end = hist["revenue"].iloc[-1 - HISTORY_YEARS], hist["revenue"].iloc[-1]
        cagr = (end / start) ** (1 / HISTORY_YEARS) - 1 if start > 0 and end > 0 else np.nan
        growth_note = f"{HISTORY_YEARS}y revenue CAGR {cagr:.1%}"
    else:
        cagr = hist["growth"].dropna().iloc[-1] if hist["growth"].notna().any() else 0.0
        growth_note = f"last year's revenue growth {cagr:.1%} (short history)"
    growth_start = _clamp(np.nan_to_num(cagr), -0.10, 0.40)
    notes["growth_start"] = growth_note + (" (clamped)" if growth_start != cagr else "")

    rf = risk_free_rate(session, as_of)
    terminal_growth = min(MAX_TERMINAL_GROWTH, rf)
    notes["terminal_growth"] = f"min({MAX_TERMINAL_GROWTH:.1%}, 10y Treasury {rf:.2%})"

    margin = recent["ebit_margin"].mean()
    margin_start = _clamp(np.nan_to_num(margin), -0.5, 0.6)
    notes["margin_start"] = f"{HISTORY_YEARS}y average EBIT margin"
    margin_end = margin_start
    notes["margin_end"] = "held at the 3y average"
    if margin_start <= 0:
        peer_margin = snapshot.loc[
            snapshot["peer_group"] == company["peer_group"], "operating_margin"
        ].median()
        margin_end = float(peer_margin)
        notes["margin_end"] = (
            f"loss-making: converges to {company['peer_group']} median EBIT margin "
            f"{peer_margin:.1%}"
        )

    pretax, tax = recent["pretax_income"].sum(), recent["income_tax"].sum()
    if pretax > 0 and recent["income_tax"].notna().any():
        tax_rate = _clamp(tax / pretax, 0.10, 0.30)
        notes["tax_rate"] = f"{HISTORY_YEARS}y effective rate, bounded to 10-30%"
    else:
        tax_rate = MARGINAL_TAX
        notes["tax_rate"] = "US statutory rate (no positive pretax history)"

    revenue = recent["revenue"]
    d_and_a_pct = float((recent["d_and_a"] / revenue).mean())
    if np.isnan(d_and_a_pct):
        d_and_a_pct = 0.03
        notes["d_and_a_pct"] = "no D&A history; assumed 3% of revenue"
    else:
        notes["d_and_a_pct"] = f"{HISTORY_YEARS}y average % of revenue"
    capex_pct = float((recent["capex"] / revenue).mean())
    if np.isnan(capex_pct):
        capex_pct = d_and_a_pct
        notes["capex_pct"] = "no capex history; assumed equal to D&A (maintenance only)"
    else:
        notes["capex_pct"] = f"{HISTORY_YEARS}y average % of revenue"
    notes["nwc_pct"] = "assumed 10% of incremental revenue (working capital not yet stored)"

    beta, beta_note = _bottom_up_beta(session, snapshot, company, as_of)
    last = hist.iloc[-1]
    debt = float(company["total_debt"]) if pd.notna(company["total_debt"]) else 0.0
    if debt > 0 and pd.notna(last["interest_expense"]):
        cost_of_debt = _clamp(last["interest_expense"] / debt, rf + 0.005, rf + 0.06)
        debt_note = "interest expense / debt, bounded to rf + 0.5-6%"
    else:
        cost_of_debt = rf + 0.02
        debt_note = "rf + 2% (no interest/debt data)"
    if has_price:
        equity_value, debt_value = float(company["market_cap"]), debt
    else:
        # Weight capital by the peer median D/E instead of the company's market value.
        group = snapshot[snapshot["peer_group"] == company["peer_group"]]
        de = _debt_to_equity(company, group)
        equity_value, debt_value = 1.0, de
    wacc = WACC(
        risk_free=rf,
        equity_risk_premium=equity_risk_premium,
        beta=beta,
        pre_tax_cost_of_debt=cost_of_debt,
        tax_rate=MARGINAL_TAX,
        equity_value=equity_value,
        debt_value=debt_value,
    )
    notes["beta"] = beta_note
    notes["cost_of_debt"] = debt_note
    notes["wacc"] = (
        f"CAPM: {rf:.2%} + {beta:.2f} x {equity_risk_premium:.1%} = {wacc.cost_of_equity:.2%} "
        f"equity, {wacc.after_tax_cost_of_debt:.2%} after-tax debt, "
        f"{wacc.debt_weight:.0%} debt weight"
    )

    growth_vol = _clamp(np.nan_to_num(hist["growth"].std(), nan=0.05), 0.02, 0.15)
    margin_vol = _clamp(np.nan_to_num(hist["ebit_margin"].std(), nan=0.03), 0.01, 0.08)

    inputs = DCFInputs(
        base_revenue=float(last["revenue"]),
        growth_start=growth_start,
        terminal_growth=terminal_growth,
        margin_start=margin_start,
        margin_end=margin_end,
        tax_rate=tax_rate,
        d_and_a_pct=d_and_a_pct,
        capex_pct=capex_pct,
        nwc_pct=NWC_PCT,
        wacc=wacc.value,
        net_debt=float(company["net_debt"]),
        other_claims=float(company["other_claims"]),
        shares=float(company["shares"]),
        years=years,
    )
    return Assumptions(
        ticker=company["ticker"],
        name=company["name"],
        as_of=as_of,
        inputs=inputs,
        wacc=wacc,
        price=float(company["price"]) if has_price else float("nan"),
        history=hist,
        growth_volatility=growth_vol,
        margin_volatility=margin_vol,
        notes=notes,
    )
