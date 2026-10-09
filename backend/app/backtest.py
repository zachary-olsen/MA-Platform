"""Backtests against real acquisitions.

1. Screener ranking (case-control). Acquisitions are rare, so instead of asking "did
   the screen pick next year's targets?" (a base-rate problem), each acquired company
   is compared with matched peers that were not acquired: same peer group, similar
   revenue, as of a date before the announcement. The target's percentile among its
   peers is that deal's AUC; averaged over deals, 0.5 means no better than chance.
   Significance: bootstrap confidence interval over deals, and a permutation test
   that relabels a random member of each case-control set as the "target".

   The ranking uses fundamentals only (growth, margins, cash conversion): acquired
   companies have no Yahoo price history, so price-based factors would be missing for
   targets but present for peers, which biases the comparison.

2. Valuation accuracy. As of the day before announcement (or the hand-entered
   unaffected date, before any leak), value each target with comps, DCF and a 50/50
   blend, and compare with the actual price per share paid. Deal prices include a
   control premium (typically 20-40%), so trading-multiple comps are expected to come
   in low; the median signed error measures that bias.

3. Monte Carlo calibration: the share of deals whose price falls inside the DCF's
   P10-P90 range. A calibrated 80% interval should capture ~80% of them.

With a few dozen deals these results are suggestive, not conclusive.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.comps import MULTIPLES
from app.models import Company, Deal
from app.screener import Factor, score
from app.services import comps_table, dcf_analysis

RANKING_FACTORS = [
    Factor("revenue_growth"),
    Factor("revenue_cagr_3y"),
    Factor("ebitda_margin"),
    Factor("fcf_margin"),
]
SIZE_BANDS = ((1 / 3, 3.0), (0.1, 10.0))  # revenue ratio vs target; widen if too few peers
MIN_PEERS = 5
BOOTSTRAP_DRAWS = 10_000
# Peers acquired within this window after the ranking date aren't valid controls.
CONTROL_EXCLUSION_DAYS = 730


@dataclass(frozen=True)
class DealCase:
    deal_id: int
    company_id: int
    ticker: str
    name: str
    peer_group: str
    announced: date
    unaffected: date | None
    price_per_share: float
    consideration: str
    verified: bool

    @property
    def valuation_date(self) -> date:
        """Latest date with no deal information in the price or fundamentals."""
        return (self.unaffected or self.announced) - timedelta(days=1)


def load_cases(
    session: Session, include_unverified: bool = False, cash_only: bool = False
) -> list[DealCase]:
    stmt = (
        select(Deal, Company)
        .join(Company, Company.id == Deal.target_company_id)
        .order_by(Deal.announcement_date)
    )
    cases = []
    for deal, company in session.execute(stmt).all():
        if not (deal.verified or include_unverified):
            continue
        if cash_only and deal.consideration != "cash":
            continue
        cases.append(
            DealCase(
                deal_id=deal.id,
                company_id=company.id,
                ticker=company.ticker,
                name=company.name,
                peer_group=company.peer_group or "",
                announced=deal.announcement_date,
                unaffected=deal.unaffected_date,
                price_per_share=float(deal.price_per_share),
                consideration=deal.consideration or "",
                verified=deal.verified,
            )
        )
    return cases


# --- 1. Screener ranking ---------------------------------------------------------


def _percentile(target: float, peers: np.ndarray) -> float:
    """Share of peers the target beats, ties counting half (= AUC of one case-control set)."""
    return float(((peers < target).sum() + 0.5 * (peers == target).sum()) / len(peers))


def rank_case(
    case: DealCase, lookback_days: int, acquisitions: dict[int, list[date]] | None = None
) -> dict:
    as_of = case.announced - timedelta(days=lookback_days)
    window_end = as_of + timedelta(days=CONTROL_EXCLUSION_DAYS)
    acquired_soon = {
        cid
        for cid, dates in (acquisitions or {}).items()
        if any(as_of <= d <= window_end for d in dates)
    }
    snap = comps_table(as_of)
    out = {"ticker": case.ticker, "announced": case.announced, "as_of": as_of,
           "peer_group": case.peer_group}  # fmt: skip
    group = snap[snap["peer_group"] == case.peer_group].copy()
    target = group[group["company_id"] == case.company_id]
    if target.empty:
        return {**out, "status": "no filings before the ranking date"}
    revenue = target.iloc[0]["revenue"]
    if not revenue or revenue <= 0 or np.isnan(revenue):
        return {**out, "status": "no revenue"}

    group["rank_score"] = score(group, RANKING_FACTORS)
    target_score = group.loc[group["company_id"] == case.company_id, "rank_score"].iloc[0]
    if np.isnan(target_score):
        return {**out, "status": "no ranking factors"}

    others = group[
        (group["company_id"] != case.company_id)
        & group["rank_score"].notna()
        & ~group["company_id"].isin(acquired_soon)
    ]
    for lo, hi in SIZE_BANDS:
        peers = others[others["revenue"].between(revenue * lo, revenue * hi)]
        if len(peers) >= MIN_PEERS:
            break
    else:
        return {**out, "status": f"fewer than {MIN_PEERS} size-matched peers"}

    per_factor = {}
    for f in RANKING_FACTORS:
        t = target.iloc[0][f.field]
        p = peers[f.field].dropna().to_numpy()
        if not np.isnan(t) and len(p):
            per_factor[f"pct_{f.field}"] = _percentile(t, p)
    return {
        **out,
        "status": "ok",
        "revenue": revenue,
        "peers": len(peers),
        "size_band": f"{lo:.2g}-{hi:.2g}x",
        "target_score": target_score,
        "peer_median_score": float(peers["rank_score"].median()),
        "percentile": _percentile(target_score, peers["rank_score"].to_numpy()),
        "_peer_scores": peers["rank_score"].to_numpy(),
        **per_factor,
    }


def ranking_backtest(
    cases: list[DealCase],
    lookback_days: int = 365,
    seed: int = 0,
    all_acquisitions: dict[int, list[date]] | None = None,
) -> dict:
    """`all_acquisitions` (company_id -> announcement dates) should cover every loaded
    deal, verified or not, so no acquired company slips in as a control."""
    rows = [rank_case(c, lookback_days, all_acquisitions) for c in cases]
    ok = [r for r in rows if r["status"] == "ok"]
    summary: dict = {"lookback_days": lookback_days, "deals": len(rows), "ranked": len(ok)}
    if ok:
        pct = np.array([r["percentile"] for r in ok])
        rng = np.random.default_rng(seed)
        boot = rng.choice(pct, size=(BOOTSTRAP_DRAWS, len(pct))).mean(axis=1)

        # Permutation null: in each case-control set, a random member plays "target".
        null = np.empty(BOOTSTRAP_DRAWS)
        sets = [np.append(r["_peer_scores"], r["target_score"]) for r in ok]
        for i in range(BOOTSTRAP_DRAWS):
            draws = []
            for s in sets:
                k = rng.integers(len(s))
                draws.append(_percentile(s[k], np.delete(s, k)))
            null[i] = np.mean(draws)

        summary.update(
            mean_percentile=float(pct.mean()),
            ci_low=float(np.percentile(boot, 2.5)),
            ci_high=float(np.percentile(boot, 97.5)),
            p_value=float((null >= pct.mean()).mean()),
            share_above_median=float((pct > 0.5).mean()),
            factor_means={
                f.field: float(np.mean([r[f"pct_{f.field}"] for r in ok if f"pct_{f.field}" in r]))
                for f in RANKING_FACTORS
                if any(f"pct_{f.field}" in r for r in ok)
            },
        )
    table = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    return {"summary": summary, "table": table}


# --- 2 & 3. Valuation accuracy and Monte Carlo calibration ------------------------


def value_case(session: Session, case: DealCase, draws: int = 10_000) -> dict:
    as_of = case.valuation_date
    out = {"ticker": case.ticker, "announced": case.announced, "as_of": as_of,
           "consideration": case.consideration, "deal_price": case.price_per_share}  # fmt: skip
    snap = comps_table(as_of)
    target = snap[snap["company_id"] == case.company_id]
    if target.empty:
        return {**out, "status": "no filings before the valuation date"}
    t = target.iloc[0]
    out["fiscal_year"] = int(t["fiscal_year"])
    for m in MULTIPLES:
        out[f"comps_{m}"] = t[f"implied_price_{m}"]
    out["comps"] = t["implied_price_median"]

    try:
        dcf = dcf_analysis(session, case.company_id, as_of, draws=draws)
        out["dcf"] = dcf.result.value_per_share
        out["terminal_share"] = dcf.result.terminal_share
        if dcf.monte_carlo is not None:
            out["mc_p10"], out["mc_p50"], out["mc_p90"] = (
                dcf.monte_carlo.percentiles[q] for q in (10, 50, 90)
            )
    except (LookupError, ValueError) as e:
        out["dcf_error"] = str(e)

    parts = [v for v in (out.get("comps"), out.get("dcf")) if v is not None and np.isfinite(v)]
    out["blend"] = float(np.mean(parts)) if len(parts) == 2 else np.nan
    for method in ("comps", "dcf", "blend", *(f"comps_{m}" for m in MULTIPLES)):
        v = out.get(method)
        out[f"err_{method}"] = (
            v / case.price_per_share - 1 if v is not None and np.isfinite(v) else np.nan
        )
    if "mc_p10" in out:
        out["in_p10_p90"] = out["mc_p10"] <= case.price_per_share <= out["mc_p90"]
    out["status"] = "ok"
    return out


def _error_summary(errors: pd.Series) -> dict:
    e = errors.dropna()
    if e.empty:
        return {"n": 0}
    return {
        "n": int(len(e)),
        "median_error": float(e.median()),
        "median_abs_error": float(e.abs().median()),
        "within_20pct": float((e.abs() <= 0.20).mean()),
    }


def valuation_backtest(session: Session, cases: list[DealCase], draws: int = 10_000) -> dict:
    table = pd.DataFrame([value_case(session, c, draws) for c in cases])
    ok = table[table["status"] == "ok"] if "status" in table else table
    methods = {"Comps (median of multiples)": "comps", "DCF": "dcf", "Blend (50/50)": "blend"}
    methods.update({f"Comps · {m}": f"comps_{m}" for m in MULTIPLES})
    summary = {label: _error_summary(ok.get(f"err_{key}", pd.Series(dtype=float)))
               for label, key in methods.items()}  # fmt: skip
    coverage = ok["in_p10_p90"].dropna() if "in_p10_p90" in ok else pd.Series(dtype=bool)
    mc = {"n": int(len(coverage))}
    if len(coverage):
        k = int(coverage.sum())
        n = len(coverage)
        se = np.sqrt(0.8 * 0.2 / n)
        mc.update(
            coverage=k / n,
            expected=0.8,
            # Normal approximation for a binomial proportion vs the nominal 80%.
            z=(k / n - 0.8) / se,
            below_p10=float((ok["deal_price"] < ok["mc_p10"]).sum() / n),
            above_p90=float((ok["deal_price"] > ok["mc_p90"]).sum() / n),
        )
    return {"summary": summary, "monte_carlo": mc, "table": table}


def acquisitions_by_company(session: Session) -> dict[int, list[date]]:
    out: dict[int, list[date]] = {}
    for company_id, announced in session.execute(
        select(Deal.target_company_id, Deal.announcement_date)
    ).all():
        out.setdefault(company_id, []).append(announced)
    return out
