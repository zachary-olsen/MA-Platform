"""Load the hand-compiled deals list: acquired targets become (inactive) companies with
their EDGAR filings ingested, and each acquisition becomes a row in `deals`.

EDGAR keeps filings for companies that were acquired and delisted, which is what lets
the backtest see a target's financials as they stood before the announcement.
"""

import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.edgar.client import EdgarClient
from app.ingest import ingest_company, validate_stored
from app.models import Company, Deal

DEALS_CSV = BACKEND_DIR / "data" / "deals_candidates.csv"


def read_deals(path: Path = DEALS_CSV) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _date(text: str) -> date | None:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def load_deal(session: Session, client: EdgarClient, row: dict, refresh: bool = False) -> str:
    """Ingest one target and upsert its deal. Returns a short status."""
    rows = ingest_company(
        session,
        client,
        {
            "ticker": row["target_ticker"],
            "cik": row["target_cik"],
            "sector": row["sector"],
            "peer_group": row["peer_group"],
            "active": "false",
        },
        refresh,
    )
    if rows == 0:
        return "skipped: no us-gaap 10-K data"
    company = session.scalar(select(Company).where(Company.cik == int(row["target_cik"])))
    session.flush()
    validate_stored(session, company)

    announced = date.fromisoformat(row["announced"])
    deal = session.scalar(
        select(Deal).where(
            Deal.target_company_id == company.id, Deal.announcement_date == announced
        )
    ) or Deal(target_company_id=company.id, announcement_date=announced)
    deal.acquirer = row["acquirer"]
    deal.price_per_share = float(row["price_per_share"])
    deal.consideration = row["consideration"] or None
    deal.consideration_detail = row["consideration_detail"] or None
    deal.value_as_announced = row["value_as_announced"] or None
    deal.unaffected_date = _date(row["unaffected_date"])
    deal.verified = row["verified"].strip().lower() in {"y", "yes", "true", "1"}
    deal.source_url = row["source_url"]
    deal.notes = row["notes"] or None
    session.add(deal)
    return f"{rows} statement rows" + ("" if deal.verified else " (unverified)")
