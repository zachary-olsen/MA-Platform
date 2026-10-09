"""Render backtest results as Markdown and save per-deal tables as CSV."""

from datetime import date
from pathlib import Path

import pandas as pd

from app.config import BACKEND_DIR

OUTPUT_DIR = BACKEND_DIR / "data" / "backtest"

LIMITATIONS = """\
- **Sample size.** A few dozen deals. Confidence intervals are wide; treat results as
  suggestive, not conclusive.
- **Hand-picked deals.** Targets were chosen by hand to fit the universe (US 10-K filers,
  >$200M revenue, completed deals), not drawn at random from all acquisitions, which
  could favor well-known, larger deals.
- **Survivorship in the control group.** Peers come from today's listed universe plus the
  loaded targets. Companies that later failed or were delisted for other reasons are
  missing, so controls skew toward survivors.
- **Fundamentals-only ranking.** Targets have no price history in the data source, so the
  ranking excludes valuation factors; it tests whether acquired companies looked
  different on growth and margins, not whether they were cheap.
- **Control premium.** Deal prices include a premium for control, so comps built from
  trading multiples are expected to undershoot; the signed error measures this bias
  rather than a pure model error.
- **Information leakage.** The valuation date is the day before announcement unless an
  unaffected date is entered. Rumors before announcement can inflate peer prices only
  indirectly (target prices aren't used), but they can matter for peers in active
  consolidation waves.
- **Stock consideration.** For stock and mixed deals the price per share is the value at
  announcement and moves with the acquirer's share price.
- **Annual data.** Fundamentals are the latest 10-K before each date, up to ~15 months old.
- **Default DCF assumptions.** Growth starts at the trailing 3y CAGR, margins are held
  flat, and terminal growth is capped at the 10y Treasury; no deal-specific judgment.
"""


def _pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:+.1%}" if abs(x) < 10 else f"{x:+.0%}"


def _plain_pct(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:.0%}"


def render(ranking: dict, valuation: dict, *, provisional: bool, cases: int, verified: int) -> str:
    r, v, mc = ranking["summary"], valuation["summary"], valuation["monte_carlo"]
    lines = [f"# Backtest results ({date.today().isoformat()})", ""]
    if provisional:
        lines += [
            f"> **PROVISIONAL.** Includes {cases - verified} of {cases} deals not yet verified "
            "by hand against their source documents. Do not cite these numbers until every "
            "deal used is verified.",
            "",
        ]
    lines += [f"Deals used: {cases} ({verified} verified).", "", "## 1. Screener ranking", ""]
    if r.get("ranked"):
        lines += [
            f"Each target ranked against size-matched peers in its peer group "
            f"{r['lookback_days']} days before announcement, on fundamentals only.",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Deals ranked | {r['ranked']} of {r['deals']} |",
            f"| Mean percentile among matched peers (AUC) | {r['mean_percentile']:.3f} |",
            f"| 95% bootstrap CI | {r['ci_low']:.3f} – {r['ci_high']:.3f} |",
            f"| Permutation p-value (vs chance = 0.5) | {r['p_value']:.3f} |",
            f"| Targets ranked above their peer median | {r['share_above_median']:.0%} |",
            "",
            "By factor (mean percentile of the target vs its peers):",
            "",
            "| Factor | Mean percentile |",
            "|---|---|",
            *[f"| {k} | {val:.3f} |" for k, val in r["factor_means"].items()],
            "",
        ]
    else:
        lines += ["No deals could be ranked.", ""]

    lines += [
        "## 2. Valuation accuracy vs deal price",
        "",
        "Signed error = model value / price paid − 1 (negative = model below deal price).",
        "",
        "| Method | Deals | Median signed error | Median absolute error | Within ±20% |",
        "|---|---|---|---|---|",
    ]
    for label, s in v.items():
        if s.get("n"):
            lines.append(
                f"| {label} | {s['n']} | {_pct(s['median_error'])} | "
                f"{_plain_pct(s['median_abs_error'])} | {_plain_pct(s['within_20pct'])} |"
            )
    lines += ["", "## 3. Monte Carlo calibration", ""]
    if mc.get("n"):
        lines += [
            f"Deal price inside the DCF's P10–P90 range in **{mc['coverage']:.0%}** of "
            f"{mc['n']} deals (a calibrated range would capture about 80%; "
            f"z = {mc['z']:+.2f}). Below P10: {mc['below_p10']:.0%}; above P90: "
            f"{mc['above_p90']:.0%}.",
            "",
        ]
    lines += ["## Limitations", "", LIMITATIONS]
    return "\n".join(lines)


def save(ranking: dict, valuation: dict, report: str, directory: Path = OUTPUT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    ranking["table"].to_csv(directory / "ranking.csv", index=False)
    valuation["table"].to_csv(directory / "valuation.csv", index=False)
    path = directory / "results.md"
    path.write_text(report)
    return path
