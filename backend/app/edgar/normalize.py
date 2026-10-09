"""Turn an EDGAR companyfacts payload into standardized annual statement rows.

Output: one row per (period_end, accession), i.e. per annual period per 10-K that
reported it. Key rules:

- Only 10-K / 10-K/A facts. Duration facts must span ~1 year (350-380 days, which
  allows 52/53-week years); 10-Ks also contain quarterly figures that this drops.
- The fact's own start/end dates define its period. The `fy`/`fp` fields describe
  the *filing*, not the fact, so they are ignored.
- A 10-K reports 3 years of income/cash flow but only 2 years of balance sheet. A
  field missing from a later filing is carried forward from the most recent earlier
  filing for the same period. That earlier filing was already public, so this keeps
  point-in-time correctness.
"""

from collections import defaultdict
from datetime import date

from app.edgar.tags import COVER_SHARES_TAG, FIELDS, HELPER_TAGS, FieldSpec

ANNUAL_FORMS = {"10-K", "10-K/A"}
MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS = 350, 380

STANDARD_FIELDS = [*FIELDS, "total_debt"]
BALANCE_SHEET_FIELDS = [f for f, spec in FIELDS.items() if spec.kind == "instant"] + ["total_debt"]

PeriodKey = tuple[str, date]  # (accession, period_end)


def fiscal_year_for(period_end: date) -> int:
    """Label only; period_end is authoritative. 52/53-week years that end in the
    first days of January belong to the prior year."""
    if period_end.month == 1 and period_end.day <= 7:
        return period_end.year - 1
    return period_end.year


def _annual_facts(namespace: dict, tag: str, spec: FieldSpec):
    for fact in namespace.get(tag, {}).get("units", {}).get(spec.unit, []):
        if fact.get("form") not in ANNUAL_FORMS:
            continue
        end = date.fromisoformat(fact["end"])
        start = date.fromisoformat(fact["start"]) if "start" in fact else None
        if spec.kind == "duration":
            if start is None or not MIN_ANNUAL_DAYS <= (end - start).days <= MAX_ANNUAL_DAYS:
                continue
        elif start is not None:
            continue
        yield fact, start, end


class _FactIndex:
    """Lazily built (accession, period_end) -> value lookups per tag."""

    def __init__(self, namespace: dict):
        self._namespace = namespace
        self._cache: dict[tuple[str, str, str], dict[PeriodKey, float]] = {}

    def values(self, tag: str, spec: FieldSpec) -> dict[PeriodKey, float]:
        key = (tag, spec.kind, spec.unit)
        if key not in self._cache:
            self._cache[key] = {
                (fact["accn"], end): fact["val"]
                for fact, _, end in _annual_facts(self._namespace, tag, spec)
            }
        return self._cache[key]

    def lookup(self, spec: FieldSpec, period: PeriodKey) -> tuple[str | None, float | None]:
        found = [
            (tag, value)
            for tag in spec.tags
            if (value := self.values(tag, spec).get(period)) is not None
        ]
        if not found:
            return None, None
        if spec.pick == "max":
            return max(found, key=lambda tv: tv[1])
        return found[0]


def _discover_periods(namespace: dict) -> dict[PeriodKey, dict]:
    periods: dict[PeriodKey, dict] = {}
    # Helper tags (e.g. revenue components) can fill a period but never create one:
    # they show up in footnotes for stray periods.
    for spec in FIELDS.values():
        if spec.kind != "duration":
            continue
        for tag in spec.tags:
            for fact, start, end in _annual_facts(namespace, tag, spec):
                periods.setdefault(
                    (fact["accn"], end),
                    {
                        "period_start": start,
                        "filed_date": date.fromisoformat(fact["filed"]),
                        "form": fact["form"],
                    },
                )
    return periods


def _cover_shares(company_facts: dict) -> dict[str, float]:
    entries = (
        company_facts.get("facts", {})
        .get("dei", {})
        .get(COVER_SHARES_TAG, {})
        .get("units", {})
        .get("shares", [])
    )
    by_accession: dict[str, list[float]] = defaultdict(list)
    for fact in entries:
        if fact.get("form") in ANNUAL_FORMS:
            by_accession[fact["accn"]].append(fact["val"])
    # Multiple values usually means multiple share classes; skip rather than guess.
    return {accn: vals[0] for accn, vals in by_accession.items() if len(set(vals)) == 1}


def _total_debt(h: dict[str, float | None]) -> tuple[float | None, str | None]:
    short_term = [n for n in ("ShortTermBorrowings", "CommercialPaper") if h[n] is not None]
    if h["DebtCurrent"] is not None:
        current = ["DebtCurrent"]
    else:
        current = [n for n in ("LongTermDebtCurrent", *short_term) if h[n] is not None]

    if h["LongTermDebtNoncurrent"] is not None:
        parts = ["LongTermDebtNoncurrent", *current]
    elif h["LongTermDebt"] is not None and h["DebtCurrent"] is not None:
        # LongTermDebt is defined to include current maturities, but companies with a
        # separate "Debt, current" balance sheet line (e.g. ADBE) use it for the
        # non-current line.
        parts = ["LongTermDebt", "DebtCurrent"]
    elif h["LongTermDebt"] is not None:
        # Includes current maturities, so only add short-term borrowings.
        parts = ["LongTermDebt", *short_term]
    elif current:
        parts = current
    else:
        parts = [
            n
            for n in (
                "ConvertibleNotesPayable",
                "ConvertibleNotesPayableCurrent",
                "SecuredLongTermDebt",
            )
            if h[n] is not None
        ]
        if not parts:
            return None, None
    return sum(h[n] for n in parts), "derived:" + "+".join(parts)


def derive(row: dict, helpers: dict[str, float | None], sources: dict[str, str]) -> None:
    """Fill fields that have no direct tag from related ones."""
    components = [
        n for n in ("SalesRevenueGoodsNet", "SalesRevenueServicesNet") if helpers[n] is not None
    ]
    if row["revenue"] is None and components:
        row["revenue"] = sum(helpers[n] for n in components)
        sources["revenue"] = "derived:" + "+".join(components)

    if row["gross_profit"] is None and None not in (row["revenue"], row["cost_of_revenue"]):
        row["gross_profit"] = row["revenue"] - row["cost_of_revenue"]
        sources["gross_profit"] = "derived:revenue-cost_of_revenue"

    if row["d_and_a"] is None and helpers["Depreciation"] is not None:
        amortization = helpers["AmortizationOfIntangibleAssets"]
        row["d_and_a"] = helpers["Depreciation"] + (amortization or 0)
        sources["d_and_a"] = "derived:Depreciation" + (
            "+AmortizationOfIntangibleAssets" if amortization is not None else ""
        )

    total_claims = helpers["LiabilitiesAndStockholdersEquity"]
    if row["total_liabilities"] is None and total_claims is not None:
        nci_equity = helpers[
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        ]
        equity = nci_equity if nci_equity is not None else row["total_equity"]
        if equity is not None:
            row["total_liabilities"] = total_claims - equity
            sources["total_liabilities"] = "derived:LiabilitiesAndStockholdersEquity-equity"

    row["total_debt"], debt_source = _total_debt(helpers)
    if debt_source:
        sources["total_debt"] = debt_source


def _carry_forward(rows: list[dict]) -> None:
    by_period: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        by_period[row["period_end"]].append(row)

    for period_rows in by_period.values():
        period_rows.sort(key=lambda r: (r["filed_date"], r["accession"]))
        for prev, row in zip(period_rows, period_rows[1:], strict=False):
            # A filing without this period's balance sheet can still contain stray
            # instant values, e.g. restated opening equity in the equity statement.
            # Mixing those with carried assets breaks Assets = Liabilities + Equity,
            # so the balance sheet is carried as a block.
            if row["total_assets"] is None and prev["total_assets"] is not None:
                for field in BALANCE_SHEET_FIELDS:
                    row[field] = None
            for field in STANDARD_FIELDS:
                if row[field] is None and prev[field] is not None:
                    row[field] = prev[field]
                    prev_source = prev["tag_sources"][field]
                    row["tag_sources"][field] = (
                        prev_source
                        if prev_source.startswith("carried:")
                        else f"carried:{prev['accession']}"
                    )


def normalize_company_facts(company_facts: dict) -> list[dict]:
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    index = _FactIndex(gaap)
    cover_shares = _cover_shares(company_facts)

    periods = _discover_periods(gaap)
    latest_end_by_accession: dict[str, date] = {}
    for accession, period_end in periods:
        latest_end_by_accession[accession] = max(
            period_end, latest_end_by_accession.get(accession, period_end)
        )

    rows = []
    for (accession, period_end), meta in periods.items():
        period = (accession, period_end)
        row = {
            "accession": accession,
            "period_end": period_end,
            "fiscal_year": fiscal_year_for(period_end),
            **meta,
        }
        sources: dict[str, str] = {}
        for field, spec in FIELDS.items():
            tag, value = index.lookup(spec, period)
            row[field] = value
            if tag:
                sources[field] = tag

        helpers = {name: index.lookup(spec, period)[1] for name, spec in HELPER_TAGS.items()}
        derive(row, helpers, sources)

        # Skip periods that only exist because of a stray footnote fact.
        if row["revenue"] is None and row["net_income"] is None:
            continue

        # The cover-page count only describes the filing's own (latest) period.
        if (
            row["shares_outstanding"] is None
            and accession in cover_shares
            and period_end == latest_end_by_accession[accession]
        ):
            row["shares_outstanding"] = cover_shares[accession]
            sources["shares_outstanding"] = f"dei:{COVER_SHARES_TAG}"

        row["tag_sources"] = sources
        rows.append(row)

    _carry_forward(rows)
    rows.sort(key=lambda r: (r["period_end"], r["filed_date"], r["accession"]))
    return rows
