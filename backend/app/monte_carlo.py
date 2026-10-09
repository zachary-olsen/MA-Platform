"""Monte Carlo DCF: a distribution of values instead of a point estimate.

Each draw samples the four assumptions that move a DCF most:

    starting revenue growth ~ Normal(base, sd of the company's historical yoy growth)
    EBIT margin shift       ~ Normal(0, sd of the company's historical EBIT margin)
    WACC                    ~ Normal(base, 1%)
    terminal growth         ~ Normal(base, 0.5%), clipped to [0%, 4%]

Volatilities are calibrated to each company's own history (bounded so a short or
erratic history can't produce absurd spreads). Draws where WACC - g < 1% are
discarded: the Gordon formula explodes as the spread approaches zero.

Simplification: the four inputs are drawn independently. In reality growth and
margins tend to move together, so the true spread is likely somewhat wider.

All draws run as one vectorized numpy projection (10,000 draws in milliseconds).
"""

from dataclasses import dataclass

import numpy as np

from app.dcf import equity_per_share, project
from app.dcf_inputs import Assumptions

WACC_SD = 0.01
TERMINAL_GROWTH_SD = 0.005
MIN_SPREAD = 0.01
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)


@dataclass(frozen=True)
class MonteCarloResult:
    values: np.ndarray  # per-share value of each kept draw
    draws: int
    discarded: int
    percentiles: dict[int, float]
    prob_above_price: float

    @property
    def p10_p90(self) -> tuple[float, float]:
        return self.percentiles[10], self.percentiles[90]


def simulate(assumptions: Assumptions, draws: int = 10_000, seed: int = 0) -> MonteCarloResult:
    rng = np.random.default_rng(seed)
    base = assumptions.inputs

    growth = rng.normal(base.growth_start, assumptions.growth_volatility, draws)
    margin_shift = rng.normal(0.0, assumptions.margin_volatility, draws)
    wacc = rng.normal(base.wacc, WACC_SD, draws)
    terminal_growth = np.clip(rng.normal(base.terminal_growth, TERMINAL_GROWTH_SD, draws), 0, 0.04)

    keep = wacc - terminal_growth >= MIN_SPREAD
    p = project(
        base_revenue=base.base_revenue,
        growth_start=growth[keep],
        terminal_growth=terminal_growth[keep],
        margin_start=base.margin_start + margin_shift[keep],
        margin_end=base.margin_end + margin_shift[keep],
        tax_rate=base.tax_rate,
        d_and_a_pct=base.d_and_a_pct,
        capex_pct=base.capex_pct,
        nwc_pct=base.nwc_pct,
        wacc=wacc[keep],
        years=base.years,
        mid_year=base.mid_year,
    )
    values = equity_per_share(base, p["enterprise_value"])
    return MonteCarloResult(
        values=values,
        draws=draws,
        discarded=int((~keep).sum()),
        percentiles={q: float(np.percentile(values, q)) for q in PERCENTILES},
        prob_above_price=float((values > assumptions.price).mean()),
    )
