import numpy as np
import pandas as pd
import pytest

from app.comps import add_peer_comparison
from app.market import as_traded
from app.screener import Factor, Filter, Screen, run_screen, score


def test_as_traded_undoes_split_adjustment():
    days = pd.to_datetime(["2024-06-06", "2024-06-07", "2024-06-10", "2024-06-11"])
    adjusted = pd.Series([121.0, 120.9, 121.8, 120.9], index=days)
    splits = pd.Series([10.0], index=pd.to_datetime(["2024-06-10"]))
    assert as_traded(adjusted, splits).tolist() == pytest.approx([1210, 1209, 121.8, 120.9])


def test_filter_parse():
    assert Filter.parse("market_cap<=20B") == Filter("market_cap", "<=", 20e9)
    assert Filter.parse("ebitda_margin >= 15%") == Filter("ebitda_margin", ">=", 0.15)
    assert Filter.parse("net_debt_to_ebitda<-1") == Filter("net_debt_to_ebitda", "<", -1)
    with pytest.raises(ValueError):
        Filter.parse("market_cap is big")


def snapshot():
    return pd.DataFrame(
        {
            "company_id": [1, 2, 3, 4],
            "ticker": ["A", "B", "C", "D"],
            "sector": ["S"] * 4,
            "peer_group": ["G1", "G1", "G1", "G2"],
            "price": [10.0, 20.0, 30.0, 40.0],
            "shares": [100.0] * 4,
            "market_cap": [1000.0, 2000.0, 3000.0, 4000.0],
            "net_debt": [100.0, 0.0, -100.0, 0.0],
            "other_claims": [0.0] * 4,
            "revenue": [500.0, 1000.0, 1000.0, 800.0],
            "ebitda": [100.0, 200.0, -50.0, 160.0],
            "operating_income": [80.0, 150.0, -60.0, 120.0],
            "net_income": [50.0, 100.0, -40.0, 90.0],
        }
    ).assign(
        ev=lambda d: d.market_cap + d.net_debt + d.other_claims,
        ev_revenue=lambda d: d.ev / d.revenue,
        ev_ebitda=lambda d: np.where(d.ebitda > 0, d.ev / d.ebitda, np.nan),
        ev_ebit=lambda d: np.where(d.operating_income > 0, d.ev / d.operating_income, np.nan),
        pe=lambda d: np.where(d.net_income > 0, d.market_cap / d.net_income, np.nan),
    )


def test_peer_median_excludes_self_and_negative_earnings():
    df = add_peer_comparison(snapshot()).set_index("ticker")
    # A's peers are B (EV/EBITDA 10x) and C (negative EBITDA, so excluded)
    assert df.at["A", "ev_ebitda_peer_median"] == pytest.approx(10.0)
    # Implied EV 10x * 100 = 1000, minus net debt 100 = 900 equity, / 100 shares
    assert df.at["A", "implied_price_ev_ebitda"] == pytest.approx(9.0)
    assert np.isnan(df.at["C", "implied_price_ev_ebitda"])  # no positive EBITDA
    assert np.isnan(df.at["D", "ev_ebitda_peer_median"])  # alone in its group


def test_score_is_within_peer_group_and_filters_apply():
    df = snapshot().assign(growth=[0.1, 0.3, 0.2, 0.0])
    s = score(df, [Factor("growth")])
    assert s.tolist() == pytest.approx([1 / 3, 1.0, 2 / 3, 1.0])  # D is best of its group of one

    screen = Screen("t", "", filters=[Filter("market_cap", ">=", 2000)], factors=[Factor("growth")])
    assert run_screen(df, screen)["ticker"].tolist() == ["B", "D", "C"]
