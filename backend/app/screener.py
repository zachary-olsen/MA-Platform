"""Configurable screen over a valuation snapshot.

A screen is a set of filters (hard cutoffs) plus ranking factors. The score is the
average of each factor's percentile rank *within the company's peer group*, so a
semiconductor isn't penalized for having lower margins than a software-like device
maker. The score is computed for every company before filtering, which the backtest
needs: it ranks acquired companies against peers that may not pass the filters.
"""

import operator
import re
from dataclasses import dataclass, field

import pandas as pd

OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
SUFFIXES = {"k": 1e3, "M": 1e6, "B": 1e9, "%": 0.01}


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: float

    @classmethod
    def parse(cls, expression: str) -> "Filter":
        """'market_cap<=20B', 'ebitda_margin>=15%', 'net_debt_to_ebitda<3'"""
        match = re.fullmatch(r"\s*(\w+)\s*(>=|<=|>|<)\s*(-?[\d.]+)\s*([kMB%]?)\s*", expression)
        if not match:
            raise ValueError(f"Can't parse filter {expression!r}; expected e.g. 'fcf_margin>0'")
        name, op, number, suffix = match.groups()
        return cls(name, op, float(number) * SUFFIXES.get(suffix, 1))

    def apply(self, df: pd.DataFrame) -> pd.Series:
        if self.field not in df.columns:
            raise ValueError(f"Unknown field {self.field!r}")
        # Missing values fail the filter.
        return OPERATORS[self.op](df[self.field], self.value).fillna(False)

    def __str__(self) -> str:
        return f"{self.field} {self.op} {self.value:g}"


@dataclass(frozen=True)
class Factor:
    field: str
    higher_is_better: bool = True


@dataclass(frozen=True)
class Screen:
    name: str
    description: str
    filters: list[Filter] = field(default_factory=list)
    factors: list[Factor] = field(default_factory=list)


PRESETS: dict[str, Screen] = {
    "target": Screen(
        name="target",
        description=(
            "Typical acquisition target: mid-cap, growing, profitable, cash generative, "
            "not over-levered, ranked on growth, margins and price relative to peers"
        ),
        filters=[
            Filter("market_cap", ">=", 300e6),
            Filter("market_cap", "<=", 30e9),
            Filter("revenue_growth", ">=", 0.05),
            Filter("ebitda_margin", ">=", 0.10),
            Filter("fcf_margin", ">", 0),
            Filter("net_debt_to_ebitda", "<=", 3),
        ],
        factors=[
            Factor("revenue_growth"),
            Factor("ebitda_margin"),
            Factor("fcf_margin"),
            Factor("ev_ebitda_vs_peers", higher_is_better=False),
        ],
    ),
    "quality": Screen(
        name="quality",
        description="High-return, cash-generative businesses with low leverage",
        filters=[
            Filter("roic", ">=", 0.15),
            Filter("fcf_conversion", ">=", 0.8),
            Filter("net_debt_to_ebitda", "<=", 2),
        ],
        factors=[Factor("roic"), Factor("fcf_margin"), Factor("revenue_cagr_3y")],
    ),
    "value": Screen(
        name="value",
        description="Profitable companies trading below their peer-group multiples",
        filters=[Filter("ebitda_margin", ">", 0), Filter("fcf_margin", ">", 0)],
        factors=[
            Factor("ev_ebitda_vs_peers", higher_is_better=False),
            Factor("pe_vs_peers", higher_is_better=False),
            Factor("fcf_yield"),
        ],
    ),
}


def score(df: pd.DataFrame, factors: list[Factor], group_by: str = "peer_group") -> pd.Series:
    """Mean within-group percentile rank across factors (0 = worst, 1 = best)."""
    if not factors:
        return pd.Series(float("nan"), index=df.index)
    ranks = pd.DataFrame(
        {
            f.field: df.groupby(group_by)[f.field].rank(pct=True, ascending=f.higher_is_better)
            for f in factors
        }
    )
    return ranks.mean(axis=1, skipna=True)


def run_screen(
    snapshot: pd.DataFrame,
    screen: Screen,
    sectors: list[str] | None = None,
    peer_groups: list[str] | None = None,
) -> pd.DataFrame:
    df = snapshot.copy()
    df["score"] = score(df, screen.factors)
    mask = pd.Series(True, index=df.index)
    if sectors:
        mask &= df["sector"].isin(sectors)
    if peer_groups:
        mask &= df["peer_group"].isin(peer_groups)
    for f in screen.filters:
        mask &= f.apply(df)
    return df[mask].sort_values("score", ascending=False).reset_index(drop=True)
