from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.dcf import WACC, DCFInputs, run_dcf
from app.dcf_inputs import Assumptions
from app.monte_carlo import simulate


def assumptions(**inputs_over) -> Assumptions:
    defaults = dict(
        base_revenue=1000.0,
        growth_start=0.08,
        terminal_growth=0.025,
        margin_start=0.20,
        margin_end=0.20,
        tax_rate=0.21,
        d_and_a_pct=0.04,
        capex_pct=0.05,
        nwc_pct=0.10,
        wacc=0.09,
        net_debt=100.0,
        other_claims=0.0,
        shares=100.0,
    )
    inputs = DCFInputs(**{**defaults, **inputs_over})
    wacc = WACC(0.04, 0.05, 1.0, 0.06, 0.21, 1000.0, 100.0)
    return Assumptions(
        ticker="TEST",
        name="Test",
        as_of=date(2025, 1, 1),
        inputs=inputs,
        wacc=wacc,
        price=15.0,
        history=pd.DataFrame(),
        growth_volatility=0.04,
        margin_volatility=0.02,
    )


def test_reproducible_with_seed_and_centred_on_base_case():
    a = assumptions()
    first, second = simulate(a, draws=5000, seed=7), simulate(a, draws=5000, seed=7)
    np.testing.assert_array_equal(first.values, second.values)

    p = first.percentiles
    assert p[10] < p[50] < p[90]
    # Symmetric input noise around the base case: the median lands near the point DCF.
    assert p[50] == pytest.approx(run_dcf(a.inputs).value_per_share, rel=0.05)
    assert 0 <= first.prob_above_price <= 1


def test_draws_with_tiny_wacc_growth_spread_are_discarded():
    result = simulate(assumptions(wacc=0.035, terminal_growth=0.025), draws=5000, seed=1)
    assert result.discarded > 0
    assert len(result.values) == result.draws - result.discarded
    assert np.isfinite(result.values).all()
