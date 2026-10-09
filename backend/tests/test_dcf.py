import numpy as np
import pytest

from app.dcf import (
    WACC,
    DCFInputs,
    blume_adjust,
    relever_beta,
    run_dcf,
    sensitivity,
    unlever_beta,
)


def inputs(**over) -> DCFInputs:
    base = dict(
        base_revenue=100.0,
        growth_start=0.10,
        terminal_growth=0.02,
        margin_start=0.20,
        margin_end=0.20,
        tax_rate=0.25,
        d_and_a_pct=0.05,
        capex_pct=0.05,
        nwc_pct=0.0,
        wacc=0.10,
        net_debt=13.75,
        other_claims=0.0,
        shares=10.0,
        years=3,
        mid_year=False,
    )
    return DCFInputs(**{**base, **over})


def test_hand_worked_three_year_example():
    """Worked by hand:

    growth fades 10% -> 6% -> 2%;  revenue 110.0, 116.6, 118.932
    EBIT at 20%: 22.0, 23.32, 23.7864;  NOPAT at 25% tax: 16.5, 17.49, 17.8398
    D&A = capex, no NWC, so FCF = NOPAT
    PV at 10%: 16.5/1.1 = 15.0;  17.49/1.21 = 14.454545;  17.8398/1.331 = 13.403306
    TV = 17.8398 x 1.02 / (0.10 - 0.02) = 227.457450;  PV = 227.457450/1.331 = 170.892149
    EV = 213.750000;  equity = EV - 13.75 = 200.0;  per share = 20.0
    """
    result = run_dcf(inputs())
    assert result.projection.loc["revenue"].tolist() == pytest.approx([110.0, 116.6, 118.932])
    assert result.projection.loc["fcf"].tolist() == pytest.approx([16.5, 17.49, 17.8398])
    assert result.pv_explicit == pytest.approx(15.0 + 14.454545 + 13.403306, abs=1e-5)
    assert result.terminal_value == pytest.approx(227.457450, abs=1e-5)
    assert result.enterprise_value == pytest.approx(213.75, abs=1e-5)
    assert result.value_per_share == pytest.approx(20.0, abs=1e-5)


@pytest.mark.parametrize("mid_year", [False, True])
def test_constant_growth_equals_gordon_closed_form(mid_year):
    """With constant growth, margins and reinvestment, FCF grows at g every year, so the
    whole DCF must collapse to the growing perpetuity FCF_1 / (r - g)."""
    g, r = 0.03, 0.09
    i = inputs(growth_start=g, terminal_growth=g, wacc=r, nwc_pct=0.15, years=7,
               mid_year=mid_year)  # fmt: skip
    revenue_1 = 100 * (1 + g)
    fcf_1 = revenue_1 * 0.20 * 0.75 - 0.15 * (revenue_1 - 100)
    expected = fcf_1 / (r - g) * ((1 + r) ** 0.5 if mid_year else 1)
    assert run_dcf(i).enterprise_value == pytest.approx(expected, rel=1e-12)


def test_value_falls_with_wacc_and_rises_with_terminal_growth():
    grid = sensitivity(inputs(years=10))
    assert (np.diff(grid.to_numpy(), axis=0) < 0).all()  # down the rows: higher WACC
    assert (np.diff(grid.to_numpy(), axis=1) > 0).all()  # across columns: higher g
    assert grid.loc[0.10, 0.02] == pytest.approx(run_dcf(inputs(years=10)).value_per_share)


def test_terminal_value_undefined_when_wacc_not_above_growth():
    result = run_dcf(inputs(wacc=0.02, terminal_growth=0.02))
    assert np.isnan(result.enterprise_value)


def test_beta_and_wacc():
    assert blume_adjust(1.5) == pytest.approx(1.335)
    unlevered = unlever_beta(1.2, debt_to_equity=0.5, tax_rate=0.2)  # 1.2 / 1.4
    assert unlevered == pytest.approx(0.857142857)
    assert relever_beta(unlevered, 0.5, 0.2) == pytest.approx(1.2)

    w = WACC(risk_free=0.04, equity_risk_premium=0.05, beta=1.2, pre_tax_cost_of_debt=0.06,
             tax_rate=0.25, equity_value=750, debt_value=250)  # fmt: skip
    assert w.cost_of_equity == pytest.approx(0.10)
    assert w.value == pytest.approx(0.75 * 0.10 + 0.25 * 0.045)
