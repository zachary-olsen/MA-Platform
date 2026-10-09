from datetime import date

from app.edgar.normalize import STANDARD_FIELDS
from app.validation import validate_company

GOOD = {
    "revenue": 1000.0,
    "cost_of_revenue": 400.0,
    "gross_profit": 600.0,
    "operating_income": 300.0,
    "interest_expense": 10.0,
    "pretax_income": 290.0,
    "income_tax": 60.0,
    "net_income": 230.0,
    "diluted_shares": 100.0,
    "d_and_a": 50.0,
    "cfo": 320.0,
    "capex": 80.0,
    "cash": 200.0,
    "short_term_investments": 50.0,
    "total_assets": 2000.0,
    "total_liabilities": 1200.0,
    "total_equity": 800.0,
    "total_debt": 500.0,
    "shares_outstanding": 99.0,
    "minority_interest": None,
    "temporary_equity": None,
}


def row(id=1, period_end=date(2024, 12, 31), accession="a-2025", filed=date(2025, 2, 1), **over):
    values = {**GOOD, **over}
    assert set(values) == set(STANDARD_FIELDS)
    return {
        "id": id,
        "period_end": period_end,
        "filed_date": filed,
        "accession": accession,
        "tag_sources": {f: "Tag" for f in STANDARD_FIELDS},
        **values,
    }


def checks(rows):
    return {(f.statement_id, f.check, f.severity) for f in validate_company(rows)}


def test_clean_row_has_no_flags():
    assert checks([row()]) == set()


def test_balance_sheet_identity():
    assert (1, "balance_sheet_identity", "warning") in checks([row(total_equity=700.0)])


def test_identity_counts_minority_interest_and_temporary_equity():
    assert checks([row(total_equity=700.0, minority_interest=60.0, temporary_equity=40.0)]) == set()


def test_identity_skipped_when_liabilities_derived_from_it():
    r = row(total_equity=700.0)
    r["tag_sources"]["total_liabilities"] = "derived:LiabilitiesAndStockholdersEquity-equity"
    assert checks([r]) == set()


def test_errors_for_impossible_values():
    found = checks([row(total_assets=-5.0, cash=3000.0)])
    assert (1, "sign", "error") in found
    assert (1, "cash_exceeds_assets", "error") in found


def test_missing_core_fields():
    flags = validate_company([row(revenue=None, cfo=None)])
    missing = [f for f in flags if f.check == "missing_fields"]
    assert missing and "revenue" in missing[0].message and "cfo" in missing[0].message


def test_restatement_and_scale_jump_across_rows():
    original = row(id=1, period_end=date(2023, 12, 31), accession="a-2024", filed=date(2024, 2, 1))
    restated = row(id=2, period_end=date(2023, 12, 31), revenue=1100.0, gross_profit=700.0)
    jumped = row(id=3, revenue=15000.0, cost_of_revenue=6000.0, gross_profit=9000.0)
    found = checks([original, restated, jumped])
    assert (2, "restated", "info") in found
    assert (3, "scale_jump", "warning") in found
