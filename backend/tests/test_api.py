"""API tests against the local dev database (skipped when it hasn't been ingested)."""

import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.main import app
from app.models import Company, Price

client = TestClient(app)


def _ready() -> bool:
    try:
        with SessionLocal() as s:
            return bool(s.scalar(select(func.count(Price.company_id)).limit(1))) and bool(
                s.scalar(select(Company.id).where(Company.ticker == "DXCM"))
            )
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="dev database not ingested")


def _no_nan(value):
    if isinstance(value, dict):
        return all(_no_nan(v) for v in value.values())
    if isinstance(value, list):
        return all(_no_nan(v) for v in value)
    return not (isinstance(value, float) and not math.isfinite(value))


def test_meta_lists_presets_and_field_formats():
    body = client.get("/api/meta").json()
    assert {p["name"] for p in body["presets"]} >= {"target", "quality", "value"}
    assert all(f["format"] for f in body["fields"])
    assert set(body["screen_columns"]) <= {f["name"] for f in body["fields"]}


def test_screen_filters_and_ranks():
    body = client.get(
        "/api/screen", params={"filter": "market_cap>=10B", "replace_filters": True}
    ).json()
    rows = body["rows"]
    assert body["matched"] == len(rows) > 0
    assert all(r["market_cap"] >= 10e9 for r in rows)
    scores = [r["score"] for r in rows if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    assert _no_nan(body)


def test_bad_inputs_are_client_errors():
    assert client.get("/api/screen", params={"filter": "nonsense"}).status_code == 422
    assert client.get("/api/screen", params={"filter": "made_up>1"}).status_code == 422
    assert client.get("/api/screen", params={"preset": "nope"}).status_code == 422
    assert client.get("/api/companies/NOTREAL").status_code == 404
    assert client.get("/api/companies/DXCM/valuation", params={"wacc": 2}).status_code == 422


def test_company_and_comps():
    detail = client.get("/api/companies/dxcm").json()
    assert detail["ticker"] == "DXCM" and detail["statements"] and detail["snapshot"]
    comps = client.get("/api/companies/DXCM/comps").json()
    assert {m["multiple"] for m in comps["multiples"]} == {
        "ev_revenue",
        "ev_ebitda",
        "ev_ebit",
        "pe",
    }
    assert all(p["ticker"] != "DXCM" for p in comps["peers"])
    assert _no_nan(detail) and _no_nan(comps)


def test_valuation_overrides_and_football_field():
    base = client.get("/api/companies/DXCM/valuation", params={"draws": 2000}).json()
    cheaper_capital = client.get(
        "/api/companies/DXCM/valuation", params={"draws": 2000, "wacc": 0.07}
    ).json()
    assert cheaper_capital["bridge"]["value_per_share"] > base["bridge"]["value_per_share"]
    wacc = next(a for a in cheaper_capital["assumptions"] if a["key"] == "wacc")
    assert wacc["overridden"] and wacc["value"] == 0.07

    bars = base["football_field"]["bars"]
    assert {b["kind"] for b in bars} >= {"market", "comps", "dcf", "monte_carlo"}
    assert all(b["low"] <= b["high"] for b in bars)
    assert sum(h["count"] for h in base["monte_carlo"]["histogram"]) <= 2000
    assert _no_nan(base)


def test_point_in_time_valuation_uses_older_filings():
    old = client.get(
        "/api/companies/DXCM/valuation", params={"as_of": "2022-06-30", "draws": 0}
    ).json()
    assert old["fiscal_year"] == 2021
    assert old["monte_carlo"] is None
