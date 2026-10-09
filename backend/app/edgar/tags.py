"""Mapping from our standardized fields to us-gaap XBRL tags, in priority order.

Companies label the same concept differently, and switch tags over time (e.g. the
2018 ASC 606 move from SalesRevenueNet to RevenueFromContractWithCustomer...). The
first tag with a value for a given filing+period wins.

Deliberately left out: component tags like SalesRevenueGoodsNet or CostOfServices.
When a company reports goods and services separately without a total, picking one
would silently understate revenue. A missing value gets flagged by validation; a
wrong one doesn't.
"""

from dataclasses import dataclass
from typing import Literal

Kind = Literal["duration", "instant"]
# "first": first tag in priority order with a value. "max": largest value across tags,
# for totals whose candidate tags are also used for components in footnotes.
Pick = Literal["first", "max"]


@dataclass(frozen=True)
class FieldSpec:
    kind: Kind
    tags: tuple[str, ...]
    unit: str = "USD"
    pick: Pick = "first"


FIELDS: dict[str, FieldSpec] = {
    "revenue": FieldSpec(
        "duration",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
    ),
    "cost_of_revenue": FieldSpec(
        "duration", ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")
    ),
    "gross_profit": FieldSpec("duration", ("GrossProfit",)),
    "operating_income": FieldSpec("duration", ("OperatingIncomeLoss",)),
    "interest_expense": FieldSpec(
        "duration", ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt")
    ),
    "pretax_income": FieldSpec(
        "duration",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    "income_tax": FieldSpec("duration", ("IncomeTaxExpenseBenefit",)),
    "net_income": FieldSpec("duration", ("NetIncomeLoss", "ProfitLoss")),
    "diluted_shares": FieldSpec(
        "duration", ("WeightedAverageNumberOfDilutedSharesOutstanding",), unit="shares"
    ),
    # Some companies (e.g. CRM) tag a depreciation-only footnote with one of these and the
    # cash flow statement's total D&A with another; the total is the largest.
    "d_and_a": FieldSpec(
        "duration",
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
        ),
        pick="max",
    ),
    "cfo": FieldSpec(
        "duration",
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    "capex": FieldSpec(
        "duration",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "cash": FieldSpec(
        "instant",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "Cash",
        ),
    ),
    "total_assets": FieldSpec("instant", ("Assets",)),
    "total_liabilities": FieldSpec("instant", ("Liabilities",)),
    "total_equity": FieldSpec(
        "instant",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    "shares_outstanding": FieldSpec("instant", ("CommonStockSharesOutstanding",), unit="shares"),
}

# Tags read only as inputs to derived fields (see normalize.derive).
HELPER_TAGS: dict[str, FieldSpec] = {
    "Depreciation": FieldSpec("duration", ("Depreciation",)),
    "AmortizationOfIntangibleAssets": FieldSpec("duration", ("AmortizationOfIntangibleAssets",)),
    "LiabilitiesAndStockholdersEquity": FieldSpec("instant", ("LiabilitiesAndStockholdersEquity",)),
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": FieldSpec(
        "instant", ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",)
    ),
    "LongTermDebtNoncurrent": FieldSpec(
        "instant", ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations")
    ),
    "DebtCurrent": FieldSpec("instant", ("DebtCurrent",)),
    "LongTermDebtCurrent": FieldSpec(
        "instant", ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent")
    ),
    "ShortTermBorrowings": FieldSpec("instant", ("ShortTermBorrowings",)),
    "CommercialPaper": FieldSpec("instant", ("CommercialPaper",)),
    "LongTermDebt": FieldSpec("instant", ("LongTermDebt",)),
}

# Cover-page share count, used when the balance sheet omits CommonStockSharesOutstanding.
# It is dated after period end, so it is matched by accession rather than date.
COVER_SHARES_TAG = "EntityCommonStockSharesOutstanding"
