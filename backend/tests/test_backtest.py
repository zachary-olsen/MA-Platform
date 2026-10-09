from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import backtest
from app.backtest import DealCase, _error_summary, _percentile, rank_case, ranking_backtest


def test_percentile_counts_ties_as_half():
    assert _percentile(5.0, np.array([1.0, 2.0, 5.0, 9.0])) == pytest.approx(2.5 / 4)


def case(company_id=1, announced=date(2020, 6, 30)):
    return DealCase(
        deal_id=company_id,
        company_id=company_id,
        ticker=f"T{company_id}",
        name="Target",
        peer_group="G",
        announced=announced,
        unaffected=None,
        price_per_share=10.0,
        consideration="cash",
        verified=True,
    )


def snapshot(n_peers=8, target_growth=0.5):
    """Target (id 1) plus peers with ascending growth and margins, all the same size."""
    peers = pd.DataFrame(
        {
            "company_id": range(2, n_peers + 2),
            "peer_group": "G",
            "revenue": 1e9,
            "revenue_growth": np.linspace(0.0, 0.2, n_peers),
            "revenue_cagr_3y": np.linspace(0.0, 0.2, n_peers),
            "ebitda_margin": np.linspace(0.05, 0.3, n_peers),
            "fcf_margin": np.linspace(0.0, 0.2, n_peers),
        }
    )
    target = pd.DataFrame(
        {
            "company_id": [1],
            "peer_group": ["G"],
            "revenue": [1e9],
            "revenue_growth": [target_growth],
            "revenue_cagr_3y": [target_growth],
            "ebitda_margin": [0.5],
            "fcf_margin": [0.4],
        }
    )
    return pd.concat([target, peers], ignore_index=True)


def test_best_target_ranks_at_the_top(monkeypatch):
    monkeypatch.setattr(backtest, "comps_table", lambda as_of: snapshot())
    row = rank_case(case(), lookback_days=365)
    assert row["status"] == "ok"
    assert row["percentile"] == 1.0
    assert row["as_of"] == date(2019, 7, 1)


def test_soon_acquired_peer_is_not_a_control(monkeypatch):
    monkeypatch.setattr(backtest, "comps_table", lambda as_of: snapshot())
    acquisitions = {5: [date(2020, 1, 15)]}  # within two years of the ranking date
    row = rank_case(case(), 365, acquisitions)
    assert row["peers"] == 7


def test_too_few_size_matched_peers_is_skipped(monkeypatch):
    small = snapshot(n_peers=3)
    monkeypatch.setattr(backtest, "comps_table", lambda as_of: small)
    assert rank_case(case(), 365)["status"].startswith("fewer than")


def test_summary_statistics_detect_a_real_effect(monkeypatch):
    monkeypatch.setattr(backtest, "comps_table", lambda as_of: snapshot())
    cases = [case(1, date(2015 + i, 6, 30)) for i in range(6)]
    result = ranking_backtest(cases, 365)["summary"]
    assert result["mean_percentile"] == 1.0
    assert result["p_value"] < 0.01
    assert result["ci_low"] == result["ci_high"] == 1.0


def test_error_summary():
    s = _error_summary(pd.Series([-0.5, -0.1, 0.1, 0.3, np.nan]))
    assert s["n"] == 4
    assert s["median_error"] == pytest.approx(0.0)
    assert s["median_abs_error"] == pytest.approx(0.2)
    assert s["within_20pct"] == pytest.approx(0.5)
