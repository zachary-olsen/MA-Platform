"""Fetch, normalize, store and validate one company's EDGAR data (used by the CLI for
the universe and for acquired deal targets)."""

from collections import Counter
from datetime import date

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.edgar.client import FACTS_URL, SUBMISSIONS_URL, EdgarClient
from app.edgar.normalize import STANDARD_FIELDS, extract_share_counts, normalize_company_facts
from app.models import Company, DataQualityFlag, FinancialStatement, RawFiling, ShareCount
from app.validation import validate_company


def cached_fetch(
    session: Session, client: EdgarClient, company: Company, endpoint: str, refresh: bool
) -> dict:
    cached = session.scalar(
        select(RawFiling).where(RawFiling.company_id == company.id, RawFiling.endpoint == endpoint)
    )
    if cached and not refresh:
        return cached.payload

    if endpoint == "companyfacts":
        url, payload = FACTS_URL.format(cik=company.cik), client.company_facts(company.cik)
    else:
        url, payload = SUBMISSIONS_URL.format(cik=company.cik), client.submissions(company.cik)

    if cached:
        session.delete(cached)
        session.flush()
    session.add(RawFiling(company_id=company.id, endpoint=endpoint, url=url, payload=payload))
    return payload


def ingest_company(session: Session, client: EdgarClient, entry: dict, refresh: bool) -> int:
    cik = int(entry["cik"])
    company = session.scalar(select(Company).where(Company.cik == cik))
    if company is None:
        company = Company(cik=cik, ticker=entry["ticker"].upper(), name=entry["ticker"].upper())
        session.add(company)
        session.flush()

    submissions = cached_fetch(session, client, company, "submissions", refresh)
    company.ticker = entry["ticker"].upper()
    company.name = submissions["name"]
    company.sic_code = submissions.get("sic") or None
    company.industry = submissions.get("sicDescription") or None
    company.fiscal_year_end = submissions.get("fiscalYearEnd") or None
    company.sector = entry.get("sector") or None
    company.peer_group = entry.get("peer_group") or None
    company.is_active = entry.get("active", "true").strip().lower() == "true"
    delisted = (entry.get("delisted_date") or "").strip()
    company.delisted_date = date.fromisoformat(delisted) if delisted else None

    facts = cached_fetch(session, client, company, "companyfacts", refresh)
    rows = normalize_company_facts(facts)
    if not rows:
        # No us-gaap 10-K data, e.g. a foreign issuer filing 20-Fs.
        session.delete(company)
        return 0

    # Deleting statements cascades to their data quality flags.
    session.execute(delete(FinancialStatement).where(FinancialStatement.company_id == company.id))
    session.execute(insert(FinancialStatement), [{"company_id": company.id, **row} for row in rows])

    share_counts = extract_share_counts(facts)
    session.execute(delete(ShareCount).where(ShareCount.company_id == company.id))
    if share_counts:
        session.execute(
            insert(ShareCount), [{"company_id": company.id, **row} for row in share_counts]
        )
    return len(rows)


def validate_stored(session: Session, company: Company) -> Counter:
    statements = session.scalars(
        select(FinancialStatement).where(FinancialStatement.company_id == company.id)
    )
    rows = [
        {
            "id": s.id,
            "period_end": s.period_end,
            "filed_date": s.filed_date,
            "accession": s.accession,
            "tag_sources": s.tag_sources,
            **{f: None if getattr(s, f) is None else float(getattr(s, f)) for f in STANDARD_FIELDS},
        }
        for s in statements
    ]
    flags = validate_company(rows)

    session.execute(delete(DataQualityFlag).where(DataQualityFlag.company_id == company.id))
    session.add_all(
        DataQualityFlag(
            company_id=company.id,
            statement_id=f.statement_id,
            check=f.check,
            severity=f.severity,
            message=f.message,
        )
        for f in flags
    )
    return Counter(f.severity for f in flags)
