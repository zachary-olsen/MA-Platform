# Backtest results (2026-10-09)

Deals used: 55 (55 verified).

## 1. Screener ranking

Each target ranked against size-matched peers in its peer group 365 days before announcement, on fundamentals only.

| Metric | Value |
|---|---|
| Deals ranked | 50 of 55 |
| Mean percentile among matched peers (AUC) | 0.482 |
| 95% bootstrap CI | 0.399 – 0.566 |
| Permutation p-value (vs chance = 0.5) | 0.667 |
| Targets ranked above their peer median | 42% |

By factor (mean percentile of the target vs its peers):

| Factor | Mean percentile |
|---|---|
| revenue_growth | 0.483 |
| revenue_cagr_3y | 0.535 |
| ebitda_margin | 0.428 |
| fcf_margin | 0.464 |

## 2. Valuation accuracy vs deal price

Signed error = model value / price paid − 1 (negative = model below deal price).

| Method | Deals | Median signed error | Median absolute error | Within ±20% |
|---|---|---|---|---|
| Comps (median of multiples) | 43 | -34.6% | 51% | 16% |
| DCF | 41 | -42.2% | 49% | 10% |
| Blend (50/50) | 41 | -35.7% | 44% | 17% |
| Comps · ev_revenue | 42 | -4.1% | 50% | 24% |
| Comps · ev_ebitda | 33 | -50.8% | 57% | 9% |
| Comps · ev_ebit | 29 | -33.8% | 50% | 21% |
| Comps · pe | 25 | -33.7% | 43% | 8% |

## 3. Monte Carlo calibration

Deal price inside the DCF's P10–P90 range in **64%** of 42 deals (a calibrated range would capture about 80%; z = -2.55). Below P10: 5%; above P90: 29%.

## Limitations

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
