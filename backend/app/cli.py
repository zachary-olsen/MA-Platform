"""Command line entry point.

python -m app.cli ingest                   # whole universe, using cached responses
python -m app.cli ingest MSFT AAPL --refresh
python -m app.cli show MSFT                # latest known values per fiscal year
python -m app.cli show MSFT --as-of 2020-01-01
"""

import argparse
import csv
import statistics
import time
from collections import Counter
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.comps import MULTIPLES, add_peer_comparison, valuation_snapshot
from app.config import BACKEND_DIR, settings
from app.db import SessionLocal
from app.edgar.client import FACTS_URL, SUBMISSIONS_URL, EdgarClient
from app.edgar.normalize import STANDARD_FIELDS, extract_share_counts, normalize_company_facts
from app.market import update_prices
from app.models import Company, DataQualityFlag, FinancialStatement, RawFiling, ShareCount
from app.queries import metrics_as_of, statements_as_of
from app.screener import PRESETS, Filter, Screen, run_screen
from app.universe import build_universe, write_universe
from app.validation import validate_company

UNIVERSE_CSV = BACKEND_DIR / "data" / "universe.csv"
SEVERITIES = ("error", "warning", "info")


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


def _format_counts(row_count: int, counts: Counter) -> str:
    severities = ", ".join(f"{counts[s]} {s}" for s in SEVERITIES)
    return f"{row_count:>4} statement rows | {severities}"


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

        skipped = []
        for entry in universe:
            try:
                with SessionLocal.begin() as session:
                    count = ingest_company(session, client, entry, args.refresh)
                    if count == 0:
                        skipped.append(entry["ticker"])
                        continue
                    company = session.scalar(
                        select(Company).where(Company.cik == int(entry["cik"]))
                    )
                    session.flush()
                    counts = validate_stored(session, company)
            except httpx.HTTPError as e:
                print(f"{entry['ticker']:<6} FAILED: {e}")
                continue
            print(f"{entry['ticker']:<6} {_format_counts(count, counts)}")

    if skipped:
        print(f"\nSkipped (no us-gaap 10-K data, e.g. 20-F filers): {', '.join(skipped)}")
    if args.prune and not args.tickers:
        keep = {int(e["cik"]) for e in universe}
        with SessionLocal.begin() as session:
            stale = session.scalars(select(Company).where(Company.cik.not_in(keep))).all()
            for company in stale:
                session.delete(company)
        if stale:
            print(f"Pruned (not in universe): {', '.join(c.ticker for c in stale)}")


def cmd_prices(args: argparse.Namespace) -> None:
    with SessionLocal.begin() as session:
        companies = _companies(session, args.tickers)
        added = update_prices(session, companies, full=args.full)
    missing = sorted(c.ticker for c in companies if c.ticker not in added)
    print(f"Added {sum(added.values()):,} daily closes for {len(added)} companies")
    if missing:
        print(f"No price data: {', '.join(missing)}")


def _billions(x) -> str:
    return "—" if pd.isna(x) else f"{x / 1e9:,.1f}"


def _pct(x) -> str:
    return "—" if pd.isna(x) else f"{x:.0%}"


def _multiple(x) -> str:
    return "—" if pd.isna(x) else f"{x:.1f}x"


def build_comps(session: Session, as_of: date) -> pd.DataFrame:
    return add_peer_comparison(valuation_snapshot(session, as_of))


def cmd_screen(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    base = PRESETS[args.preset]
    screen = Screen(
        name=base.name,
        description=base.description,
        filters=[*base.filters, *(Filter.parse(f) for f in args.filter)],
        factors=base.factors,
    )

    timings = []
    for _ in range(max(args.time, 1)):
        start = time.perf_counter()
        with SessionLocal() as session:
            comps = build_comps(session, as_of)
        result = run_screen(comps, screen, sectors=args.sector, peer_groups=args.peer_group)
        timings.append(time.perf_counter() - start)

    print(f"Screen '{screen.name}' as of {as_of}: {screen.description}")
    print("Filters: " + "; ".join(str(f) for f in screen.filters))
    print(f"{len(result)} of {len(comps)} companies pass\n")
    table = pd.DataFrame(
        {
            "ticker": result["ticker"],
            "peer group": result["peer_group"],
            "mkt cap $B": result["market_cap"].map(_billions),
            "EV/EBITDA": result["ev_ebitda"].map(_multiple),
            "vs peers": result["ev_ebitda_vs_peers"].map(_pct),
            "rev growth": result["revenue_growth"].map(_pct),
            "EBITDA mgn": result["ebitda_margin"].map(_pct),
            "FCF mgn": result["fcf_margin"].map(_pct),
            "ND/EBITDA": result["net_debt_to_ebitda"].map(_multiple),
            "score": result["score"].map(lambda x: f"{x:.2f}"),
        }
    )
    print(table.head(args.limit).to_string(index=False))
    if args.time:
        print(
            f"\nFull-universe screen ({len(comps)} companies): median "
            f"{statistics.median(timings) * 1000:,.0f} ms over {len(timings)} runs "
            f"(min {min(timings) * 1000:,.0f} ms)"
        )


def cmd_comps(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        company = _company(session, args.ticker)
        comps = build_comps(session, as_of)
    target = comps[comps["company_id"] == company.id]
    if target.empty:
        raise SystemExit(f"No valuation for {company.ticker} as of {as_of} (missing price?)")
    t = target.iloc[0]
    peers = comps[(comps["peer_group"] == t["peer_group"]) & (comps["company_id"] != company.id)]

    print(f"{t['name']} ({t['ticker']}) vs {len(peers)} {t['peer_group']} peers, as of {as_of}")
    print(
        f"Price {t['price']:,.2f} on {t['price_date']} | market cap ${_billions(t['market_cap'])}B"
        f" | EV ${_billions(t['ev'])}B | FY{t['fiscal_year']} fundamentals (filed "
        f"{t['filed_date']})\n"
    )
    labels = {
        "ev_revenue": "EV/Revenue",
        "ev_ebitda": "EV/EBITDA",
        "ev_ebit": "EV/EBIT",
        "pe": "P/E",
    }
    rows = []
    for m in MULTIPLES:
        rows.append(
            {
                "multiple": labels[m],
                "company": _multiple(t[m]),
                "peer median": _multiple(t[f"{m}_peer_median"]),
                "vs peers": _pct(t[f"{m}_vs_peers"]),
                "implied price": "—"
                if pd.isna(t[f"implied_price_{m}"])
                else f"{t[f'implied_price_{m}']:,.2f}",
            }
        )
    print(pd.DataFrame(rows).to_string(index=False))
    print(
        f"\nMedian implied price {t['implied_price_median']:,.2f} vs current "
        f"{t['price']:,.2f} ({_pct(t['upside_to_comps'])})"
    )
    if args.peers:
        cols = ["ticker", "market_cap", "ev_revenue", "ev_ebitda", "pe", "revenue_growth"]
        view = peers[cols].sort_values("market_cap", ascending=False).copy()
        view["market_cap"] = view["market_cap"].map(_billions)
        for c in ("ev_revenue", "ev_ebitda", "pe"):
            view[c] = view[c].map(_multiple)
        view["revenue_growth"] = view["revenue_growth"].map(_pct)
        print("\n" + view.to_string(index=False))


def cmd_build_universe(args: argparse.Namespace) -> None:
    periods = tuple(args.periods)
    with EdgarClient(settings.sec_user_agent) as client:
        rows = build_universe(client, periods)
    write_universe(rows, UNIVERSE_CSV)
    counts = Counter((r["sector"], r["peer_group"]) for r in rows)
    print(f"Wrote {len(rows)} companies to {UNIVERSE_CSV}")
    for (sector, group), n in sorted(counts.items()):
        print(f"  {sector:<15} {group:<25} {n:>4}")


def _companies(session: Session, tickers: list[str]) -> list[Company]:
    stmt = select(Company).order_by(Company.ticker)
    if tickers:
        stmt = stmt.where(Company.ticker.in_([t.upper() for t in tickers]))
    companies = list(session.scalars(stmt))
    if not companies:
        raise SystemExit("No matching companies ingested")
    return companies


def _company(session: Session, ticker: str) -> Company:
    company = session.scalar(
        select(Company).where(Company.ticker == ticker.upper()).order_by(Company.is_active.desc())
    )
    if company is None:
        raise SystemExit(f"{ticker} not ingested")
    return company


def cmd_validate(args: argparse.Namespace) -> None:
    with SessionLocal.begin() as session:
        for company in _companies(session, args.tickers):
            counts = validate_stored(session, company)
            row_count = session.query(FinancialStatement).filter_by(company_id=company.id).count()
            print(f"{company.ticker:<6} {_format_counts(row_count, counts)}")


def cmd_flags(args: argparse.Namespace) -> None:
    allowed = SEVERITIES[: SEVERITIES.index(args.severity) + 1]
    with SessionLocal() as session:
        stmt = (
            select(Company.ticker, FinancialStatement, DataQualityFlag)
            .join(FinancialStatement, FinancialStatement.id == DataQualityFlag.statement_id)
            .join(Company, Company.id == DataQualityFlag.company_id)
            .where(DataQualityFlag.severity.in_(allowed))
            .order_by(Company.ticker, FinancialStatement.period_end, FinancialStatement.filed_date)
        )
        if args.ticker:
            stmt = stmt.where(Company.id == _company(session, args.ticker).id)
        results = session.execute(stmt).all()

    for ticker, statement, flag in results:
        print(
            f"{ticker:<6} FY{statement.fiscal_year} filed {statement.filed_date}  "
            f"{flag.severity:<7} {flag.check:<24} {flag.message}"
        )
    print(f"\n{len(results)} flags ({' + '.join(allowed)})")


def cmd_show(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        company = _company(session, args.ticker)
        if args.metrics:
            _show_metrics(session, company, as_of, args.years)
            return
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


PERCENT_METRICS = [
    "revenue_growth",
    "revenue_cagr_3y",
    "gross_margin",
    "operating_margin",
    "ebitda_margin",
    "net_margin",
    "fcf_margin",
    "capex_to_revenue",
    "effective_tax_rate",
    "roic",
]
MULTIPLE_METRICS = ["fcf_conversion", "net_debt_to_ebitda", "debt_to_equity", "interest_coverage"]
AMOUNT_METRICS = ["revenue", "ebitda", "fcf", "net_debt"]


def _show_metrics(session: Session, company: Company, as_of: date, years: int) -> None:
    df = metrics_as_of(session, as_of, [company.id]).tail(years).set_index("fiscal_year")
    if df.empty:
        raise SystemExit(f"No filings for {company.ticker} as of {as_of}")

    def fmt(column: str, value) -> str:
        if pd.isna(value):
            return "—"
        if column in AMOUNT_METRICS:
            return f"{value / 1e6:,.0f}"
        if column in PERCENT_METRICS:
            return f"{value:.1%}"
        return f"{value:.2f}x"

    columns = AMOUNT_METRICS + PERCENT_METRICS + MULTIPLE_METRICS
    table = pd.DataFrame({year: {c: fmt(c, df.at[year, c]) for c in columns} for year in df.index})
    print(f"{company.name}, metrics as known on {as_of}. Amounts in USD millions.")
    print(table.to_string())


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch and normalize EDGAR data for the universe")
    p_ingest.add_argument("tickers", nargs="*", help="Subset of universe.csv (default: all)")
    p_ingest.add_argument("--refresh", action="store_true", help="Ignore cached responses")
    p_ingest.add_argument(
        "--prune", action="store_true", help="Delete companies no longer in universe.csv"
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_prices = sub.add_parser("prices", help="Fetch new daily closes and splits (Yahoo)")
    p_prices.add_argument("tickers", nargs="*")
    p_prices.add_argument("--full", action="store_true", help="Refetch full history")
    p_prices.set_defaults(func=cmd_prices)

    p_screen = sub.add_parser("screen", help="Screen the universe")
    p_screen.add_argument("--preset", choices=sorted(PRESETS), default="target")
    p_screen.add_argument(
        "--filter", action="append", default=[], help="Extra filter, e.g. 'ev_ebitda<15'"
    )
    p_screen.add_argument("--sector", action="append", help="Healthcare or Semiconductors")
    p_screen.add_argument("--peer-group", action="append")
    p_screen.add_argument("--as-of", help="YYYY-MM-DD; point-in-time screen")
    p_screen.add_argument("--limit", type=int, default=25)
    p_screen.add_argument("--time", type=int, default=0, metavar="N", help="Time N runs")
    p_screen.set_defaults(func=cmd_screen)

    p_comps = sub.add_parser("comps", help="Comparable-company valuation for one company")
    p_comps.add_argument("ticker")
    p_comps.add_argument("--as-of")
    p_comps.add_argument("--peers", action="store_true", help="List the peers")
    p_comps.set_defaults(func=cmd_comps)

    p_universe = sub.add_parser("build-universe", help="Regenerate universe.csv from EDGAR")
    p_universe.add_argument(
        "--periods",
        nargs="+",
        default=["CY2025", "CY2024"],
        help="Calendar-year XBRL frames for the revenue floor, newest first",
    )
    p_universe.set_defaults(func=cmd_build_universe)

    p_show = sub.add_parser("show", help="Print a company's annual figures")
    p_show.add_argument("ticker")
    p_show.add_argument("--as-of", help="YYYY-MM-DD; only use filings public by this date")
    p_show.add_argument("--years", type=int, default=5)
    p_show.add_argument("--metrics", action="store_true", help="Show derived metrics instead")
    p_show.set_defaults(func=cmd_show)

    p_validate = sub.add_parser("validate", help="Re-run accounting checks on stored data")
    p_validate.add_argument("tickers", nargs="*")
    p_validate.set_defaults(func=cmd_validate)

    p_flags = sub.add_parser("flags", help="List data quality flags")
    p_flags.add_argument("ticker", nargs="?")
    p_flags.add_argument(
        "--severity", choices=SEVERITIES, default="warning", help="Minimum severity to show"
    )
    p_flags.set_defaults(func=cmd_flags)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
