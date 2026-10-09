from datetime import date

from app.edgar.normalize import fiscal_year_for, normalize_company_facts

FY23 = ("2022-07-01", "2023-06-30")
FY24 = ("2023-07-01", "2024-06-30")
FILING_23 = ("0000000001-23-000001", "2023-07-27")
FILING_24 = ("0000000001-24-000001", "2024-07-30")


def fact(val, period, filing, form="10-K"):
    """A companyfacts entry. `period` is (start, end) for durations or an end date."""
    accn, filed = filing
    entry = {"val": val, "accn": accn, "filed": filed, "form": form, "fy": 2099, "fp": "FY"}
    if isinstance(period, tuple):
        entry["start"], entry["end"] = period
    else:
        entry["end"] = period
    return entry


def payload(**tags):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": facts}} for tag, facts in tags.items()}}}


def by_key(rows):
    return {(r["accession"], r["period_end"].isoformat()): r for r in rows}


def test_quarterly_facts_and_10q_filings_are_ignored():
    rows = normalize_company_facts(
        payload(
            Revenues=[
                fact(100, FY24, FILING_24),
                fact(30, ("2024-04-01", "2024-06-30"), FILING_24),  # Q4 inside the 10-K
                fact(25, ("2023-07-01", "2023-09-30"), FILING_24, form="10-Q"),
            ]
        )
    )
    assert [(r["period_end"], r["revenue"]) for r in rows] == [(date(2024, 6, 30), 100)]


def test_tag_priority_and_fallback():
    rows = by_key(
        normalize_company_facts(
            payload(
                Revenues=[fact(100, FY24, FILING_24)],
                RevenueFromContractWithCustomerExcludingAssessedTax=[
                    fact(90, FY24, FILING_24),
                    fact(80, FY23, FILING_23),
                ],
            )
        )
    )
    fy24 = rows[(FILING_24[0], "2024-06-30")]
    fy23 = rows[(FILING_23[0], "2023-06-30")]
    assert (fy24["revenue"], fy24["tag_sources"]["revenue"]) == (100, "Revenues")
    assert fy23["revenue"] == 80


def test_restatement_keeps_one_row_per_filing():
    rows = by_key(
        normalize_company_facts(
            payload(Revenues=[fact(80, FY23, FILING_23), fact(82, FY23, FILING_24)])
        )
    )
    assert rows[(FILING_23[0], "2023-06-30")]["revenue"] == 80
    assert rows[(FILING_24[0], "2023-06-30")]["revenue"] == 82
    assert rows[(FILING_24[0], "2023-06-30")]["filed_date"] == date(2024, 7, 30)


def test_balance_sheet_carried_forward_from_earlier_filing():
    # The FY24 10-K repeats FY23 revenue but (here) not FY23 assets.
    rows = by_key(
        normalize_company_facts(
            payload(
                Revenues=[fact(80, FY23, FILING_23), fact(80, FY23, FILING_24)],
                Assets=[fact(500, "2023-06-30", FILING_23)],
            )
        )
    )
    later = rows[(FILING_24[0], "2023-06-30")]
    assert later["total_assets"] == 500
    assert later["tag_sources"]["total_assets"] == f"carried:{FILING_23[0]}"


def test_d_and_a_takes_largest_candidate():
    # A depreciation-only footnote tagged with one candidate, cash flow total with another.
    rows = normalize_company_facts(
        payload(
            Revenues=[fact(100, FY24, FILING_24)],
            DepreciationDepletionAndAmortization=[fact(10, FY24, FILING_24)],
            DepreciationAndAmortization=[fact(35, FY24, FILING_24)],
        )
    )
    assert rows[0]["d_and_a"] == 35


def test_derived_fields():
    end = "2024-06-30"
    rows = normalize_company_facts(
        payload(
            Revenues=[fact(100, FY24, FILING_24)],
            CostOfRevenue=[fact(40, FY24, FILING_24)],
            LiabilitiesAndStockholdersEquity=[fact(1000, end, FILING_24)],
            StockholdersEquity=[fact(600, end, FILING_24)],
            LongTermDebtNoncurrent=[fact(200, end, FILING_24)],
            LongTermDebtCurrent=[fact(20, end, FILING_24)],
            CommercialPaper=[fact(5, end, FILING_24)],
        )
    )
    row = rows[0]
    assert row["gross_profit"] == 60
    assert row["total_liabilities"] == 400
    assert row["total_debt"] == 225
    assert row["tag_sources"]["total_debt"] == (
        "derived:LongTermDebtNoncurrent+LongTermDebtCurrent+CommercialPaper"
    )


def test_long_term_debt_as_noncurrent_line_when_debt_current_reported():
    end = "2024-06-30"
    rows = normalize_company_facts(
        payload(
            Revenues=[fact(100, FY24, FILING_24)],
            LongTermDebt=[fact(4129, end, FILING_24)],
            DebtCurrent=[fact(1499, end, FILING_24)],
        )
    )
    assert rows[0]["total_debt"] == 5628


def test_period_without_revenue_or_net_income_is_dropped():
    rows = normalize_company_facts(
        payload(PaymentsToAcquirePropertyPlantAndEquipment=[fact(7, FY24, FILING_24)])
    )
    assert rows == []


def test_fiscal_year_label():
    assert fiscal_year_for(date(2024, 6, 30)) == 2024
    assert fiscal_year_for(date(2025, 1, 31)) == 2025  # Jan fiscal year end (e.g. CRM)
    assert fiscal_year_for(date(2022, 1, 1)) == 2021  # 53-week year spilling into January
