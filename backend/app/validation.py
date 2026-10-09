"""Automated accounting checks on normalized statement rows.

Severities:
- error:   the data is almost certainly wrong (negative assets, cash > assets, ...)
- warning: worth a look; can be legitimate (identity gap from minority interest, ...)
- info:    context, not a problem (restatements, approximated fields, ...)

Checks run per company because some compare periods or filings against each other.
Each row is a dict with "id", period/filing metadata, standardized fields as floats
(or None), and "tag_sources".
"""

from collections import defaultdict
from dataclasses import dataclass

IDENTITY_TOLERANCE = 0.01  # 1% of the scale item
NET_INCOME_TOLERANCE = 0.05  # equity-method income, discontinued ops, etc. are legitimate gaps
RESTATEMENT_TOLERANCE = 0.01
SCALE_JUMP = 10.0  # a 10x year-over-year change usually means a units error

CORE_FIELDS = (
    "revenue",
    "operating_income",
    "net_income",
    "cfo",
    "capex",
    "cash",
    "total_assets",
    "total_equity",
    "diluted_shares",
)
NON_NEGATIVE = (
    "revenue",
    "capex",
    "cash",
    "short_term_investments",
    "total_debt",
    "cost_of_revenue",
)
POSITIVE = ("total_assets", "shares_outstanding", "diluted_shares")


@dataclass(frozen=True)
class Flag:
    statement_id: int
    check: str
    severity: str
    message: str


def _m(value: float) -> str:
    return f"{value / 1e6:,.0f}M"


def _is_derived(row: dict, field: str) -> bool:
    return row["tag_sources"].get(field, "").startswith("derived:")


def _all_present(row: dict, *fields: str) -> bool:
    return all(row[f] is not None for f in fields)


def check_row(row: dict) -> list[tuple[str, str, str]]:
    """Checks that only need one row. Returns (check, severity, message) tuples."""
    out = []

    missing = [f for f in CORE_FIELDS if row[f] is None]
    if missing:
        out.append(("missing_fields", "warning", "Missing: " + ", ".join(missing)))

    for field in NON_NEGATIVE:
        if row[field] is not None and row[field] < 0:
            out.append(("sign", "error", f"{field} is negative ({_m(row[field])})"))
    for field in POSITIVE:
        if row[field] is not None and row[field] <= 0:
            out.append(("sign", "error", f"{field} is not positive ({row[field]:,.0f})"))

    # Assets = Liabilities + Equity. Skipped when liabilities were derived from it.
    if _all_present(row, "total_assets", "total_liabilities", "total_equity") and not _is_derived(
        row, "total_liabilities"
    ):
        other_claims = (row["minority_interest"] or 0) + (row["temporary_equity"] or 0)
        gap = row["total_assets"] - row["total_liabilities"] - row["total_equity"] - other_claims
        if row["total_assets"] > 0 and abs(gap) / row["total_assets"] > IDENTITY_TOLERANCE:
            out.append(
                (
                    "balance_sheet_identity",
                    "warning",
                    f"Assets - liabilities - equity - minority interest - temporary equity "
                    f"= {_m(gap)} ({gap / row['total_assets']:.1%} of assets)",
                )
            )

    if _all_present(row, "revenue", "cost_of_revenue", "gross_profit") and not _is_derived(
        row, "gross_profit"
    ):
        gap = row["revenue"] - row["cost_of_revenue"] - row["gross_profit"]
        if row["revenue"] > 0 and abs(gap) / row["revenue"] > IDENTITY_TOLERANCE:
            out.append(
                ("gross_profit_identity", "warning", f"Revenue - COGS - gross profit = {_m(gap)}")
            )

    if _all_present(row, "pretax_income", "income_tax", "net_income"):
        gap = row["pretax_income"] - row["income_tax"] - row["net_income"]
        scale = max(abs(row["pretax_income"]), abs(row["net_income"]))
        if scale > 0 and abs(gap) / scale > NET_INCOME_TOLERANCE:
            out.append(
                (
                    "net_income_bridge",
                    "info",
                    f"Pretax - tax - net income = {_m(gap)} (equity-method income, "
                    "discontinued operations or minority interest)",
                )
            )

    if _all_present(row, "cash", "total_assets") and row["cash"] > row["total_assets"]:
        out.append(("cash_exceeds_assets", "error", f"Cash {_m(row['cash'])} > total assets"))

    if (
        _all_present(row, "total_debt", "total_liabilities")
        and row["total_debt"] > row["total_liabilities"]
    ):
        out.append(
            ("debt_exceeds_liabilities", "error", f"Debt {_m(row['total_debt'])} > liabilities")
        )

    if (
        _all_present(row, "operating_income", "revenue")
        and row["revenue"] > 0
        and row["operating_income"] > row["revenue"]
    ):
        out.append(("operating_margin", "warning", "Operating income exceeds revenue"))

    if row["total_debt"] is None:
        out.append(("no_debt_reported", "info", "No debt tags; treated as zero debt"))

    if _is_derived(row, "operating_income"):
        out.append(
            ("approximate_ebit", "info", "Operating income approximated as pretax + interest")
        )

    if _is_derived(row, "d_and_a"):
        out.append(
            ("approximate_d_and_a", "info", f"D&A built from {row['tag_sources']['d_and_a']}")
        )

    return out


def _check_restatements(rows: list[dict]) -> list[Flag]:
    flags = []
    by_period: dict = defaultdict(list)
    for row in rows:
        by_period[row["period_end"]].append(row)

    for versions in by_period.values():
        versions.sort(key=lambda r: (r["filed_date"], r["accession"]))
        original = versions[0]
        for later in versions[1:]:
            changes = []
            for field in ("revenue", "net_income", "total_assets"):
                old, new = original[field], later[field]
                if old is None or new is None or old == 0:
                    continue
                change = (new - old) / abs(old)
                if abs(change) > RESTATEMENT_TOLERANCE:
                    changes.append(f"{field} {_m(old)} -> {_m(new)} ({change:+.1%})")
            if changes:
                flags.append(
                    Flag(
                        later["id"],
                        "restated",
                        "info",
                        f"vs. original filed {original['filed_date']}: " + "; ".join(changes),
                    )
                )
    return flags


def _check_scale_jumps(rows: list[dict]) -> list[Flag]:
    """Compare each row's revenue to the prior year as reported in the same filing."""
    flags = []
    by_filing: dict = defaultdict(list)
    for row in rows:
        by_filing[row["accession"]].append(row)

    for filing_rows in by_filing.values():
        filing_rows.sort(key=lambda r: r["period_end"])
        for prev, row in zip(filing_rows, filing_rows[1:], strict=False):
            if not prev["revenue"] or not row["revenue"] or prev["revenue"] <= 0:
                continue
            ratio = row["revenue"] / prev["revenue"]
            if ratio > SCALE_JUMP or ratio < 1 / SCALE_JUMP:
                flags.append(
                    Flag(
                        row["id"],
                        "scale_jump",
                        "warning",
                        f"Revenue {ratio:.1f}x prior year ({_m(prev['revenue'])} -> "
                        f"{_m(row['revenue'])}); possible units error",
                    )
                )
    return flags


def validate_company(rows: list[dict]) -> list[Flag]:
    flags = [Flag(row["id"], *result) for row in rows for result in check_row(row)]
    flags += _check_restatements(rows)
    flags += _check_scale_jumps(rows)
    return flags
