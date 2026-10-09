"""Command line entry point.

python -m app.cli ingest                   # whole universe, using cached responses
python -m app.cli ingest MSFT AAPL --refresh
python -m app.cli show MSFT                # latest known values per fiscal year
python -m app.cli show MSFT --as-of 2020-01-01
"""

import argparse
import csv
from datetime import date

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, settings
from app.db import SessionLocal
from app.edgar.client import FACTS_URL, SUBMISSIONS_URL, EdgarClient
from app.edgar.normalize import STANDARD_FIELDS, normalize_company_facts
from app.models import Company, FinancialStatement, RawFiling
from app.queries import statements_as_of

UNIVERSE_CSV = BACKEND_DIR / "data" / "universe.csv"


def load_universe(tickers: list[str] | None) -> list[dict]:
    with UNIVERSE_CSV.open() as f:
        rows = list(csv.DictReader(f))
    if tickers:
        wanted = {t.upper() for t in tickers}
        rows = [r for r in rows if r["ticker"].upper() in wanted]
        missing = wanted - {r["ticker"].upper() for r in rows}
        if missing:
            raise SystemExit(f"Not in {UNIVERSE_CSV.name}: {', '.join(sorted(missing))}")
    return rows


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
    company.is_active = entry.get("active", "true").strip().lower() == "true"
    delisted = (entry.get("delisted_date") or "").strip()
    company.delisted_date = date.fromisoformat(delisted) if delisted else None

    facts = cached_fetch(session, client, company, "companyfacts", refresh)
    rows = normalize_company_facts(facts)

    session.execute(delete(FinancialStatement).where(FinancialStatement.company_id == company.id))
    if rows:
        session.execute(
            insert(FinancialStatement), [{"company_id": company.id, **row} for row in rows]
        )
    return len(rows)


def cmd_ingest(args: argparse.Namespace) -> None:
    universe = load_universe(args.tickers)
    with EdgarClient(settings.sec_user_agent) as client:
        missing_cik = [e for e in universe if not e["cik"].strip()]
        if missing_cik:
            ticker_map = client.ticker_map()
            for entry in missing_cik:
                cik = ticker_map.get(entry["ticker"].upper())
                if cik is None:
                    raise SystemExit(f"No CIK for {entry['ticker']}; add it to universe.csv")
                entry["cik"] = str(cik)

        for entry in universe:
            with SessionLocal.begin() as session:
                count = ingest_company(session, client, entry, args.refresh)
            print(f"{entry['ticker']:<6} {count:>4} statement rows")


def cmd_show(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        company = session.scalar(
            select(Company)
            .where(Company.ticker == args.ticker.upper())
            .order_by(Company.is_active.desc())
        )
        if company is None:
            raise SystemExit(f"{args.ticker} not ingested")
        statements = statements_as_of(session, as_of, [company.id])

    if not statements:
        raise SystemExit(f"No filings for {company.ticker} as of {as_of}")

    recent = statements[-args.years :]
    table = pd.DataFrame(
        {
            s.fiscal_year: {
                f: None if getattr(s, f) is None else float(getattr(s, f)) / 1e6
                for f in STANDARD_FIELDS
            }
            for s in recent
        }
    )
    filed = {s.fiscal_year: f"{s.filed_date} ({s.form})" for s in recent}

    print(f"{company.name} (CIK {company.cik}), as known on {as_of}. USD / shares in millions.")
    with pd.option_context("display.float_format", "{:,.0f}".format, "display.width", 200):
        print(table.to_string(na_rep="—"))
    print("\nSource filing per year:")
    for year, info in filed.items():
        print(f"  FY{year}: {info}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch and normalize EDGAR data for the universe")
    p_ingest.add_argument("tickers", nargs="*", help="Subset of universe.csv (default: all)")
    p_ingest.add_argument("--refresh", action="store_true", help="Ignore cached responses")
    p_ingest.set_defaults(func=cmd_ingest)

    p_show = sub.add_parser("show", help="Print a company's annual figures")
    p_show.add_argument("ticker")
    p_show.add_argument("--as-of", help="YYYY-MM-DD; only use filings public by this date")
    p_show.add_argument("--years", type=int, default=5)
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
