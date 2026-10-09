"""Discounted cash flow valuation: the math, independent of the database.

Free cash flow to the firm for each projected year t:

    revenue_t = revenue_{t-1} x (1 + growth_t)
    FCF_t     = EBIT_t x (1 - tax) + D&A_t - capex_t - change in NWC_t
    change in NWC_t = nwc_pct x (revenue_t - revenue_{t-1})

Growth fades linearly from `growth_start` in year 1 to `terminal_growth` in the final
year, and EBIT margin moves linearly from `margin_start` to `margin_end`, so the
last explicit year is already at steady state when the terminal value takes over:

    TV_N = FCF_N x (1 + g) / (WACC - g)          (Gordon growth)

With the mid-year convention, cash flows arrive on average halfway through each year,
so year t is discounted t - 0.5 years, and the terminal value at N - 0.5 for
consistency (the perpetuity is also a stream of mid-year flows).

    EV = sum PV(FCF_t) + PV(TV)
    equity value = EV - net debt - other claims (minority interest, preferred)

Every function takes scalars or numpy arrays, so the same code values one scenario,
a sensitivity grid, or 10,000 Monte Carlo draws.
"""

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

BLUME_WEIGHT = 0.67  # adjusted beta = 0.67 x raw + 0.33 x 1


@dataclass(frozen=True)
class DCFInputs:
    base_revenue: float
    growth_start: float
    terminal_growth: float
    margin_start: float
    margin_end: float
    tax_rate: float
    d_and_a_pct: float
    capex_pct: float
    nwc_pct: float
    wacc: float
    net_debt: float
    other_claims: float
    shares: float
    years: int = 10
    mid_year: bool = True


def project(
    base_revenue,
    growth_start,
    terminal_growth,
    margin_start,
    margin_end,
    tax_rate,
    d_and_a_pct,
    capex_pct,
    nwc_pct,
    wacc,
    years: int,
    mid_year: bool,
) -> dict[str, np.ndarray]:
    """Vectorized projection. Array inputs of shape (n,) give outputs of shape (n, years)
    for yearly items and (n,) for totals."""
    col = lambda x: np.atleast_1d(np.asarray(x, dtype=float))[:, None]  # noqa: E731
    g0, gT, m0, mT = col(growth_start), col(terminal_growth), col(margin_start), col(margin_end)
    tax, da, capex, nwc, r = (
        col(tax_rate),
        col(d_and_a_pct),
        col(capex_pct),
        col(nwc_pct),
        col(wacc),
    )

    t = np.arange(1, years + 1, dtype=float)
    fade = (t - 1) / (years - 1) if years > 1 else np.ones(1)
    growth = g0 + (gT - g0) * fade
    margin = m0 + (mT - m0) * fade

    revenue = col(base_revenue) * np.cumprod(1 + growth, axis=1)
    prior_revenue = np.concatenate([np.broadcast_to(col(base_revenue), (len(revenue), 1)),
                                    revenue[:, :-1]], axis=1)  # fmt: skip
    ebit = revenue * margin
    nopat = ebit * (1 - tax)
    d_and_a = revenue * da
    capex_amt = revenue * capex
    delta_nwc = nwc * (revenue - prior_revenue)
    fcf = nopat + d_and_a - capex_amt - delta_nwc

    timing = t - 0.5 if mid_year else t
    discount = (1 + r) ** -timing
    pv_fcf = fcf * discount

    spread = r[:, 0] - gT[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        terminal_value = np.where(spread > 0, fcf[:, -1] * (1 + gT[:, 0]) / spread, np.nan)
    pv_terminal = terminal_value * discount[:, -1]
    enterprise_value = pv_fcf.sum(axis=1) + pv_terminal

    return {
        "growth": growth,
        "margin": margin,
        "revenue": revenue,
        "ebit": ebit,
        "nopat": nopat,
        "d_and_a": d_and_a,
        "capex": capex_amt,
        "delta_nwc": delta_nwc,
        "fcf": fcf,
        "discount_factor": discount,
        "pv_fcf": pv_fcf,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
    }


def _project_inputs(inputs: DCFInputs, **overrides) -> dict[str, np.ndarray]:
    params = {
        "base_revenue": inputs.base_revenue,
        "growth_start": inputs.growth_start,
        "terminal_growth": inputs.terminal_growth,
        "margin_start": inputs.margin_start,
        "margin_end": inputs.margin_end,
        "tax_rate": inputs.tax_rate,
        "d_and_a_pct": inputs.d_and_a_pct,
        "capex_pct": inputs.capex_pct,
        "nwc_pct": inputs.nwc_pct,
        "wacc": inputs.wacc,
    }
    params.update(overrides)
    return project(**params, years=inputs.years, mid_year=inputs.mid_year)


def equity_per_share(inputs: DCFInputs, enterprise_value: np.ndarray) -> np.ndarray:
    return (enterprise_value - inputs.net_debt - inputs.other_claims) / inputs.shares


@dataclass(frozen=True)
class DCFResult:
    inputs: DCFInputs
    projection: pd.DataFrame  # one column per projected year
    pv_explicit: float
    terminal_value: float
    pv_terminal: float
    enterprise_value: float
    equity_value: float
    value_per_share: float

    @property
    def terminal_share(self) -> float:
        """Share of EV coming from the terminal value; above ~75% the valuation is
        mostly an assumption about the far future."""
        return self.pv_terminal / self.enterprise_value


def run_dcf(inputs: DCFInputs) -> DCFResult:
    p = _project_inputs(inputs)
    rows = ["growth", "revenue", "margin", "ebit", "nopat", "d_and_a", "capex", "delta_nwc",
            "fcf", "discount_factor", "pv_fcf"]  # fmt: skip
    projection = pd.DataFrame(
        {name: p[name][0] for name in rows}, index=range(1, inputs.years + 1)
    ).T
    ev = float(p["enterprise_value"][0])
    equity = ev - inputs.net_debt - inputs.other_claims
    return DCFResult(
        inputs=inputs,
        projection=projection,
        pv_explicit=float(p["pv_fcf"][0].sum()),
        terminal_value=float(p["terminal_value"][0]),
        pv_terminal=float(p["pv_terminal"][0]),
        enterprise_value=ev,
        equity_value=equity,
        value_per_share=equity / inputs.shares,
    )


def sensitivity(
    inputs: DCFInputs,
    wacc_steps=(-0.01, -0.005, 0.0, 0.005, 0.01),
    growth_steps=(-0.01, -0.005, 0.0, 0.005, 0.01),
) -> pd.DataFrame:
    """Value per share across WACC (rows) x terminal growth (columns)."""
    waccs = inputs.wacc + np.asarray(wacc_steps)
    growths = inputs.terminal_growth + np.asarray(growth_steps)
    w, g = np.meshgrid(waccs, growths, indexing="ij")
    p = _project_inputs(inputs, wacc=w.ravel(), terminal_growth=g.ravel())
    values = equity_per_share(inputs, p["enterprise_value"]).reshape(w.shape)
    return pd.DataFrame(values, index=pd.Index(waccs, name="wacc"),
                        columns=pd.Index(growths, name="terminal_growth"))  # fmt: skip


# --- Cost of capital -------------------------------------------------------------


def blume_adjust(raw_beta):
    """Shrink a regression beta toward 1, since betas mean-revert over time."""
    return BLUME_WEIGHT * np.asarray(raw_beta) + (1 - BLUME_WEIGHT)


def unlever_beta(levered_beta, debt_to_equity, tax_rate):
    """Hamada: strip out financial risk to get the business (asset) beta."""
    return np.asarray(levered_beta) / (1 + (1 - np.asarray(tax_rate)) * np.asarray(debt_to_equity))


def relever_beta(unlevered_beta, debt_to_equity, tax_rate):
    return np.asarray(unlevered_beta) * (
        1 + (1 - np.asarray(tax_rate)) * np.asarray(debt_to_equity)
    )


@dataclass(frozen=True)
class WACC:
    risk_free: float
    equity_risk_premium: float
    beta: float
    pre_tax_cost_of_debt: float
    tax_rate: float
    equity_value: float
    debt_value: float

    @property
    def cost_of_equity(self) -> float:
        return self.risk_free + self.beta * self.equity_risk_premium  # CAPM

    @property
    def after_tax_cost_of_debt(self) -> float:
        return self.pre_tax_cost_of_debt * (1 - self.tax_rate)

    @property
    def debt_weight(self) -> float:
        return self.debt_value / (self.debt_value + self.equity_value)

    @property
    def value(self) -> float:
        wd = self.debt_weight
        return (1 - wd) * self.cost_of_equity + wd * self.after_tax_cost_of_debt


def with_overrides(inputs: DCFInputs, **overrides) -> DCFInputs:
    return replace(inputs, **{k: v for k, v in overrides.items() if v is not None})
