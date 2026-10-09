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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest import (
    acquisitions_by_company,
    load_cases,
    ranking_backtest,
    valuation_backtest,
)
from app.backtest_report import render, save
from app.comps import MULTIPLES, add_peer_comparison, valuation_snapshot
from app.config import BACKEND_DIR, settings
from app.db import SessionLocal
from app.dcf import run_dcf, sensitivity, with_overrides
from app.dcf_inputs import build_assumptions
from app.deals import load_deal, read_deals
from app.edgar.client import EdgarClient
from app.edgar.normalize import STANDARD_FIELDS
from app.ingest import ingest_company, validate_stored
from app.market import update_market_series, update_prices
from app.models import Company, DataQualityFlag, Deal, FinancialStatement, Price
from app.monte_carlo import simulate
from app.queries import metrics_as_of, statements_as_of
from app.screener import PRESETS, Filter, Screen, run_screen
from app.universe import build_universe, write_universe

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
            # Acquired deal targets aren't in the universe file but must be kept.
            deal_targets = select(Deal.target_company_id)
            stale = session.scalars(
                select(Company).where(Company.cik.not_in(keep), Company.id.not_in(deal_targets))
            ).all()
            for company in stale:
                session.delete(company)
        if stale:
            print(f"Pruned (not in universe): {', '.join(c.ticker for c in stale)}")


def cmd_deals(args: argparse.Namespace) -> None:
    rows = read_deals()
    with EdgarClient(settings.sec_user_agent) as client:
        for row in rows:
            try:
                with SessionLocal.begin() as session:
                    status = load_deal(session, client, row, args.refresh)
            except httpx.HTTPError as e:
                status = f"FAILED: {e}"
            print(f"{row['target_ticker']:<6} {row['announced']}  {status}")
    verified = sum(r["verified"].strip().lower() in {"y", "yes", "true", "1"} for r in rows)
    print(f"\n{len(rows)} deals loaded, {verified} marked verified")


def cmd_backtest(args: argparse.Namespace) -> None:
    with SessionLocal() as session:
        cases = load_cases(session, args.include_unverified, cash_only=False)
        acquisitions = acquisitions_by_company(session)
        if not cases:
            raise SystemExit(
                "No verified deals. Mark rows verified in deals_candidates.csv and run "
                "`python -m app.cli deals`, or pass --include-unverified for a provisional run."
            )
        verified = sum(c.verified for c in cases)
        provisional = verified < len(cases)
        valuation_cases = [c for c in cases if c.consideration == "cash" or args.all_consideration]

        start = time.perf_counter()
        ranking = ranking_backtest(cases, args.lookback, all_acquisitions=acquisitions)
        valuation = valuation_backtest(session, valuation_cases, draws=args.draws)
        elapsed = time.perf_counter() - start

    report = render(
        ranking, valuation, provisional=provisional, cases=len(cases), verified=verified
    )
    path = save(ranking, valuation, report)
    print(report)
    skipped = ranking["table"][ranking["table"]["status"] != "ok"]
    if not skipped.empty:
        print("\nNot ranked:")
        for row in skipped.itertuples():
            print(f"  {row.ticker:<6} {row.status}")
    print(f"\nRan in {elapsed:.1f}s. Per-deal tables and report in {path.parent}")


def cmd_prices(args: argparse.Namespace) -> None:
    with SessionLocal.begin() as session:
        companies = _companies(session, args.tickers)
        added = update_prices(session, companies, full=args.full)
        series = update_market_series(session)
    with SessionLocal() as session:
        priced = set(session.scalars(select(Price.company_id).distinct()))
    missing = sorted(c.ticker for c in companies if c.id not in priced)
    print(f"Added {sum(added.values()):,} daily closes for {len(added)} companies")
    print("Market series: " + ", ".join(f"{k} {v:,} obs" for k, v in series.items()))
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


def cmd_dcf(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    with SessionLocal() as session:
        company = _company(session, args.ticker)
        try:
            a = build_assumptions(
                session, company.id, as_of, equity_risk_premium=args.erp, years=args.years
            )
        except LookupError as e:
            raise SystemExit(f"{company.ticker}: {e}") from e

    overrides = {
        "growth_start": args.growth,
        "margin_start": args.margin,
        "margin_end": args.margin,
        "wacc": args.wacc,
        "terminal_growth": args.terminal_growth,
    }
    inputs = with_overrides(a.inputs, **overrides)
    a.inputs = inputs
    for key, value in overrides.items():
        if value is not None:
            a.notes[key] = "override"
    result = run_dcf(inputs)

    print(f"{a.name} ({a.ticker}) DCF as of {as_of}, from FY{a.history['fiscal_year'].iloc[-1]}")
    print("\nAssumptions")
    rows = [
        ("Revenue growth, year 1", f"{inputs.growth_start:.1%}", a.notes["growth_start"]),
        ("Terminal growth", f"{inputs.terminal_growth:.2%}", a.notes["terminal_growth"]),
        ("EBIT margin, year 1", f"{inputs.margin_start:.1%}", a.notes["margin_start"]),
        (f"EBIT margin, year {inputs.years}", f"{inputs.margin_end:.1%}", a.notes["margin_end"]),
        ("Tax rate", f"{inputs.tax_rate:.1%}", a.notes["tax_rate"]),
        ("D&A % revenue", f"{inputs.d_and_a_pct:.1%}", a.notes["d_and_a_pct"]),
        ("Capex % revenue", f"{inputs.capex_pct:.1%}", a.notes["capex_pct"]),
        ("NWC % incremental rev", f"{inputs.nwc_pct:.0%}", a.notes["nwc_pct"]),
        ("Beta", f"{a.wacc.beta:.2f}", a.notes["beta"]),
        ("Pre-tax cost of debt", f"{a.wacc.pre_tax_cost_of_debt:.2%}", a.notes["cost_of_debt"]),
        ("WACC", f"{inputs.wacc:.2%}", a.notes.get("wacc") if args.wacc is None else "override"),
    ]
    for label, value, note in rows:
        print(f"  {label:<24}{value:>8}   {note}")

    proj = result.projection
    table = pd.DataFrame(
        {
            "growth": proj.loc["growth"].map(lambda x: f"{x:.1%}"),
            "revenue": (proj.loc["revenue"] / 1e6).map(lambda x: f"{x:,.0f}"),
            "EBIT": (proj.loc["ebit"] / 1e6).map(lambda x: f"{x:,.0f}"),
            "FCF": (proj.loc["fcf"] / 1e6).map(lambda x: f"{x:,.0f}"),
            "PV of FCF": (proj.loc["pv_fcf"] / 1e6).map(lambda x: f"{x:,.0f}"),
        }
    ).T
    print("\nProjection (USD millions)")
    print(table.to_string())

    print(
        f"\nPV of FCF ${result.pv_explicit / 1e9:,.2f}B + PV of terminal value "
        f"${result.pv_terminal / 1e9:,.2f}B ({result.terminal_share:.0%} of EV)"
    )
    print(
        f"EV ${result.enterprise_value / 1e9:,.2f}B - net debt ${inputs.net_debt / 1e9:,.2f}B"
        f" - other claims ${inputs.other_claims / 1e9:,.2f}B = equity "
        f"${result.equity_value / 1e9:,.2f}B / {inputs.shares / 1e6:,.1f}M shares"
    )
    print(
        f"Value per share {result.value_per_share:,.2f} vs price {a.price:,.2f} "
        f"({result.value_per_share / a.price - 1:+.0%})"
    )

    grid = sensitivity(inputs)
    grid.index = [f"WACC {w:.2%}" for w in grid.index]
    grid.columns = [f"g {g:.2%}" for g in grid.columns]
    print("\nSensitivity: value per share")
    print(grid.map(lambda x: "—" if pd.isna(x) else f"{x:,.2f}").to_string())

    if args.draws:
        mc = simulate(a, draws=args.draws, seed=args.seed)
        p = mc.percentiles
        print(
            f"\nMonte Carlo ({mc.draws:,} draws, {mc.discarded:,} discarded with WACC - g < 1%;"
            f" growth sd {a.growth_volatility:.1%}, margin sd {a.margin_volatility:.1%})"
        )
        print("  " + "   ".join(f"P{q} {v:,.2f}" for q, v in p.items()))
        print(f"  P(value > current price {a.price:,.2f}) = {mc.prob_above_price:.0%}")


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

    p_deals = sub.add_parser("deals", help="Load deals_candidates.csv and ingest targets")
    p_deals.add_argument("--refresh", action="store_true", help="Ignore cached responses")
    p_deals.set_defaults(func=cmd_deals)

    p_backtest = sub.add_parser("backtest", help="Screener and valuation backtests on deals")
    p_backtest.add_argument(
        "--include-unverified", action="store_true", help="Provisional run on unverified deals"
    )
    p_backtest.add_argument(
        "--lookback", type=int, default=365, help="Days before announcement for the ranking"
    )
    p_backtest.add_argument(
        "--all-consideration",
        action="store_true",
        help="Include stock and mixed deals in the valuation test (default: cash only)",
    )
    p_backtest.add_argument("--draws", type=int, default=10_000, help="Monte Carlo draws")
    p_backtest.set_defaults(func=cmd_backtest)

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

    p_dcf = sub.add_parser("dcf", help="DCF valuation with sensitivity and Monte Carlo")
    p_dcf.add_argument("ticker")
    p_dcf.add_argument("--as-of")
    p_dcf.add_argument("--growth", type=float, help="Year-1 revenue growth, e.g. 0.12")
    p_dcf.add_argument("--margin", type=float, help="EBIT margin for all years")
    p_dcf.add_argument("--wacc", type=float)
    p_dcf.add_argument("--terminal-growth", type=float)
    p_dcf.add_argument("--erp", type=float, default=0.05, help="Equity risk premium")
    p_dcf.add_argument("--years", type=int, default=10)
    p_dcf.add_argument("--draws", type=int, default=10_000, help="Monte Carlo draws (0 = off)")
    p_dcf.add_argument("--seed", type=int, default=0)
    p_dcf.set_defaults(func=cmd_dcf)

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
