"""Build data/universe.csv: currently listed US healthcare and semiconductor companies.

Membership rules:
- SIC code in one of the peer groups below (SIC is the SEC's industry code).
- Listed on Nasdaq or NYSE today.
- At least MIN_REVENUE of annual revenue in the latest calendar-year XBRL frame, which
  also drops foreign filers that report under IFRS (no us-gaap revenue).

The revenue floor keeps commercial-stage companies. Pre-revenue biotechs are valued on
their pipelines, so comps and DCFs on historical financials say nothing about them.
Health insurers (SIC 6324) are excluded: their margins and leverage aren't comparable
with operating companies.

This is a *current* universe, so it has survivorship bias. The backtest adds acquired
and delisted companies separately (Phase 6).
"""

import csv
from pathlib import Path

from app.edgar.client import EdgarClient

MIN_REVENUE = 200e6
EXCHANGES = {"Nasdaq", "NYSE"}
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")

# sector -> peer group -> SIC codes
PEER_GROUPS: dict[str, dict[str, set[str]]] = {
    "Healthcare": {
        "Pharma & Biotech": {"2834", "2835", "2836"},
        "Medical Devices": {"3841", "3842", "3843", "3844", "3845", "3851"},
        "Healthcare Services": {
            "8011",
            "8050",
            "8051",
            "8060",
            "8062",
            "8071",
            "8082",
            "8090",
            "8093",
        },
    },
    "Semiconductors": {
        "Semiconductors": {"3674"},
    },
}

_EQUIPMENT = ("Semiconductors", "Semiconductor Equipment")
_TOOLS = ("Healthcare", "Life Science Tools")

# Companies whose SIC code doesn't identify them: semiconductor equipment and life
# science tools are filed under instrument/machinery codes shared with unrelated
# industries, and Qualcomm under radio equipment. Also used to reassign a peer group.
MANUAL_MEMBERS: dict[str, tuple[str, str]] = {
    **dict.fromkeys(
        ["AMAT", "LRCX", "KLAC", "TER", "ENTG", "MKSI", "ONTO", "ACLS", "FORM", "KLIC",
         "COHU", "AEIS", "UCTT", "ICHR"],
        _EQUIPMENT,
    ),
    **dict.fromkeys(
        ["TMO", "DHR", "A", "WAT", "MTD", "RVTY", "BIO", "ILMN", "IQV", "CRL", "MEDP",
         "BRKR", "AVTR", "TECH", "WST", "QGEN"],
        _TOOLS,
    ),
    "QCOM": ("Semiconductors", "Semiconductors"),
    "GFS": ("Semiconductors", "Semiconductors"),
}  # fmt: skip

# In a target SIC code but a different business.
EXCLUDED: dict[str, str] = {
    **dict.fromkeys(
        ["ENPH", "FSLR", "SEDG", "ARRY", "SHLS", "NXT", "DQ", "TYGO", "TOYO", "TE"], "solar"
    ),
    **dict.fromkeys(["IPGP", "LASR", "VIAV", "AAOI"], "lasers/optical networking"),
    "MMM": "industrial conglomerate",
    "MSA": "safety equipment",
    "WRBY": "eyewear retail",
    "EYE": "eyewear retail",
    "NATR": "dietary supplements",
    "LFVN": "dietary supplements",
}


FIELDS = ["ticker", "cik", "name", "sector", "peer_group", "sic", "revenue_musd", "active",
          "delisted_date"]  # fmt: skip


def latest_revenue_by_cik(client: EdgarClient, periods: tuple[str, ...]) -> dict[int, float]:
    """Annual revenue per CIK from the most recent calendar-year frame that has it."""
    revenue: dict[int, float] = {}
    for period in periods:  # newest first
        for tag in REVENUE_TAGS:
            for row in client.frame(tag, period):
                revenue.setdefault(row["cik"], row["val"])
    return revenue


def build_universe(client: EdgarClient, periods: tuple[str, ...]) -> list[dict]:
    sic_by_cik: dict[int, tuple[str, str, str]] = {}
    for sector, groups in PEER_GROUPS.items():
        for group, sics in groups.items():
            for sic in sorted(sics):
                for cik in client.ciks_for_sic(sic):
                    sic_by_cik[cik] = (sector, group, sic)

    revenue = latest_revenue_by_cik(client, periods)

    members: dict[int, dict] = {}
    for listing in client.listed_companies():
        cik, ticker = int(listing["cik"]), listing["ticker"].upper()
        if cik in members or listing["exchange"] not in EXCHANGES:
            continue  # first listing per CIK is the primary share class
        if ticker in EXCLUDED:
            continue
        if ticker in MANUAL_MEMBERS:
            sector, group = MANUAL_MEMBERS[ticker]
            sic = ""
        elif cik in sic_by_cik:
            sector, group, sic = sic_by_cik[cik]
        else:
            continue
        if revenue.get(cik, 0) < MIN_REVENUE:
            continue
        members[cik] = {
            "ticker": ticker,
            "cik": cik,
            "name": listing["name"],
            "sector": sector,
            "peer_group": group,
            "sic": sic,
            "revenue_musd": round(revenue[cik] / 1e6),
            "active": "true",
            "delisted_date": "",
        }
    return sorted(members.values(), key=lambda m: (m["sector"], m["peer_group"], m["ticker"]))


def write_universe(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
