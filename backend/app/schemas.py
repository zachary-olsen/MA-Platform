"""API response models. Tabular rows stay as dicts keyed by field name (see /api/meta
for labels and formats); everything else is typed."""

from datetime import date
from typing import Any

from pydantic import BaseModel

Row = dict[str, Any]


class FieldInfo(BaseModel):
    name: str
    label: str
    format: str
    description: str


class FilterInfo(BaseModel):
    field: str
    op: str
    value: float


class FactorInfo(BaseModel):
    field: str
    higher_is_better: bool


class PresetInfo(BaseModel):
    name: str
    description: str
    filters: list[FilterInfo]
    factors: list[FactorInfo]


class SectorInfo(BaseModel):
    name: str
    peer_groups: list[str]


class Meta(BaseModel):
    latest_price_date: date | None
    company_count: int  # currently listed universe
    acquired_count: int  # deal targets kept for backtests
    sectors: list[SectorInfo]
    presets: list[PresetInfo]
    fields: list[FieldInfo]
    screen_columns: list[str]


class ScreenResponse(BaseModel):
    as_of: date
    preset: str
    filters: list[FilterInfo]
    universe: int
    matched: int
    elapsed_ms: float
    rows: list[Row]


class CompanySummary(BaseModel):
    ticker: str
    name: str
    sector: str | None
    peer_group: str | None


class CompanyDetail(BaseModel):
    ticker: str
    name: str
    cik: int
    sector: str | None
    peer_group: str | None
    industry: str | None
    fiscal_year_end: str | None
    is_active: bool
    as_of: date
    snapshot: Row | None
    statements: list[Row]
    metrics: list[Row]
    flags: dict[str, int]


class MultipleComparison(BaseModel):
    multiple: str
    label: str
    company: float | None
    peer_median: float | None
    vs_peers: float | None
    implied_price: float | None


class CompsResponse(BaseModel):
    as_of: date
    peer_group: str
    target: Row
    multiples: list[MultipleComparison]
    implied_price_median: float | None
    upside: float | None
    peers: list[Row]


class Assumption(BaseModel):
    key: str
    label: str
    value: float
    format: str
    note: str
    overridden: bool


class Bridge(BaseModel):
    pv_explicit: float
    terminal_value: float
    pv_terminal: float
    terminal_share: float
    enterprise_value: float
    net_debt: float
    other_claims: float
    equity_value: float
    shares: float
    value_per_share: float
    price: float
    upside: float


class Sensitivity(BaseModel):
    waccs: list[float]
    growths: list[float]
    values: list[list[float | None]]


class HistogramBin(BaseModel):
    x0: float
    x1: float
    count: int


class MonteCarlo(BaseModel):
    draws: int
    discarded: int
    percentiles: dict[str, float]
    prob_above_price: float
    growth_volatility: float
    margin_volatility: float
    histogram: list[HistogramBin]


class FootballBar(BaseModel):
    method: str
    low: float
    high: float
    mid: float | None
    kind: str
    detail: str | None = None


class FootballField(BaseModel):
    price: float
    bars: list[FootballBar]


class ValuationResponse(BaseModel):
    as_of: date
    ticker: str
    fiscal_year: int
    assumptions: list[Assumption]
    projection: list[Row]
    bridge: Bridge
    sensitivity: Sensitivity
    monte_carlo: MonteCarlo | None
    football_field: FootballField
