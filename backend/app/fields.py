"""Catalog of screenable/displayable fields: label, display format, and which direction
is better. The API serves this so the frontend never hard-codes labels or formats."""

from typing import Literal

Format = Literal["usd", "pct", "multiple", "price", "number", "text", "date"]

# name -> (label, format, short description)
FIELDS: dict[str, tuple[str, Format, str]] = {
    "ticker": ("Ticker", "text", ""),
    "name": ("Company", "text", ""),
    "sector": ("Sector", "text", ""),
    "peer_group": ("Peer group", "text", "Sub-industry used for comps"),
    "fiscal_year": ("FY", "number", "Latest fiscal year filed by the as-of date"),
    "period_end": ("Period end", "date", ""),
    "price": ("Price", "price", "Last close on or before the as-of date"),
    "market_cap": ("Market cap", "usd", "Price x shares outstanding"),
    "ev": ("EV", "usd", "Market cap + debt - cash - ST investments + minority interest"),
    "revenue": ("Revenue", "usd", "Latest fiscal year"),
    "ebitda": ("EBITDA", "usd", "Operating income + D&A"),
    "fcf": ("FCF", "usd", "Operating cash flow - capex"),
    "net_debt": ("Net debt", "usd", "Debt - cash - short-term investments"),
    "revenue_growth": ("Rev growth", "pct", "Year-over-year revenue growth"),
    "revenue_cagr_3y": ("3y CAGR", "pct", "Three-year revenue CAGR"),
    "gross_margin": ("Gross mgn", "pct", ""),
    "operating_margin": ("EBIT mgn", "pct", ""),
    "ebitda_margin": ("EBITDA mgn", "pct", ""),
    "net_margin": ("Net mgn", "pct", ""),
    "fcf_margin": ("FCF mgn", "pct", ""),
    "capex_to_revenue": ("Capex/rev", "pct", ""),
    "fcf_conversion": ("FCF conv.", "multiple", "FCF / net income"),
    "roic": ("ROIC", "pct", "NOPAT / (equity + net debt)"),
    "net_debt_to_ebitda": ("ND/EBITDA", "multiple", ""),
    "debt_to_equity": ("D/E", "multiple", "Book debt / book equity"),
    "interest_coverage": ("Int. cover", "multiple", "EBIT / interest expense"),
    "ev_revenue": ("EV/Rev", "multiple", ""),
    "ev_ebitda": ("EV/EBITDA", "multiple", ""),
    "ev_ebit": ("EV/EBIT", "multiple", ""),
    "pe": ("P/E", "multiple", ""),
    "fcf_yield": ("FCF yield", "pct", "FCF / market cap"),
    "ev_ebitda_vs_peers": ("vs peers", "pct", "EV/EBITDA premium to peer median"),
    "pe_vs_peers": ("P/E vs peers", "pct", "P/E premium to peer median"),
    "upside_to_comps": ("Comps upside", "pct", "Median comps-implied price vs current"),
    "score": ("Score", "number", "Mean within-peer-group percentile of ranking factors"),
}

SCREEN_COLUMNS = [
    "ticker", "name", "sector", "peer_group", "fiscal_year", "price", "market_cap", "ev",
    "revenue", "revenue_growth", "revenue_cagr_3y", "gross_margin", "ebitda_margin",
    "fcf_margin", "roic", "net_debt_to_ebitda", "ev_revenue", "ev_ebitda", "pe",
    "fcf_yield", "ev_ebitda_vs_peers", "upside_to_comps", "score",
]  # fmt: skip
