from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Dollar amounts and share counts. XBRL reports whole units, so 2 decimals is plenty.
Money = Numeric(20, 2)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tickers get reused after delistings, so CIK is the real identity.
    cik: Mapped[int] = mapped_column(BigInteger, unique=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str]
    sector: Mapped[str | None]  # our own grouping, from universe.csv
    peer_group: Mapped[str | None]  # sub-industry used for comps
    sic_code: Mapped[str | None] = mapped_column(String(4))
    industry: Mapped[str | None]  # SEC's SIC description
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))  # MMDD
    is_active: Mapped[bool] = mapped_column(default=True)
    delisted_date: Mapped[date | None]


class RawFiling(Base):
    """Cached raw EDGAR responses, so normalization can be re-run without refetching."""

    __tablename__ = "raw_filings"
    __table_args__ = (UniqueConstraint("company_id", "endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(32))  # "companyfacts" | "submissions"
    url: Mapped[str]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONB)


class FinancialStatement(Base):
    """One annual period as reported in one filing.

    A 10-K repeats prior years as comparatives (sometimes restated), so the same
    period_end appears once per filing that reported it. Point-in-time queries pick
    the latest filed_date <= as-of date for each period.
    """

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("company_id", "period_end", "accession"),
        Index("ix_fs_company_period_filed", "company_id", "period_end", "filed_date"),
        Index("ix_fs_filed", "filed_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    fiscal_year: Mapped[int]
    period_start: Mapped[date | None]
    period_end: Mapped[date]
    accession: Mapped[str] = mapped_column(String(20))
    form: Mapped[str] = mapped_column(String(10))
    filed_date: Mapped[date]

    # Income statement
    revenue: Mapped[Decimal | None] = mapped_column(Money)
    cost_of_revenue: Mapped[Decimal | None] = mapped_column(Money)
    gross_profit: Mapped[Decimal | None] = mapped_column(Money)
    operating_income: Mapped[Decimal | None] = mapped_column(Money)
    interest_expense: Mapped[Decimal | None] = mapped_column(Money)
    pretax_income: Mapped[Decimal | None] = mapped_column(Money)
    income_tax: Mapped[Decimal | None] = mapped_column(Money)
    net_income: Mapped[Decimal | None] = mapped_column(Money)
    diluted_shares: Mapped[Decimal | None] = mapped_column(Money)  # weighted average
    # Cash flow statement (capex stored as a positive outflow)
    d_and_a: Mapped[Decimal | None] = mapped_column(Money)
    cfo: Mapped[Decimal | None] = mapped_column(Money)
    capex: Mapped[Decimal | None] = mapped_column(Money)
    # Balance sheet (as of period_end)
    cash: Mapped[Decimal | None] = mapped_column(Money)
    short_term_investments: Mapped[Decimal | None] = mapped_column(Money)
    total_assets: Mapped[Decimal | None] = mapped_column(Money)
    total_liabilities: Mapped[Decimal | None] = mapped_column(Money)
    total_equity: Mapped[Decimal | None] = mapped_column(Money)
    total_debt: Mapped[Decimal | None] = mapped_column(Money)  # excludes operating leases
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Money)
    minority_interest: Mapped[Decimal | None] = mapped_column(Money)  # incl. redeemable NCI
    temporary_equity: Mapped[Decimal | None] = mapped_column(Money)  # e.g. redeemable preferred

    # field -> XBRL tag used, "derived:..." or "carried:<accession>"
    tag_sources: Mapped[dict] = mapped_column(JSONB, default=dict)


class ShareCount(Base):
    """Shares outstanding as reported on each 10-K/10-Q, for point-in-time market cap.

    Counts are as reported at the time; splits after as_of_date are applied when
    pairing them with a price (see app/market.py).
    """

    __tablename__ = "share_counts"
    __table_args__ = (
        UniqueConstraint("company_id", "accession"),
        Index("ix_share_counts_company_filed", "company_id", "filed_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    accession: Mapped[str] = mapped_column(String(20))
    form: Mapped[str] = mapped_column(String(10))
    as_of_date: Mapped[date]
    filed_date: Mapped[date]
    shares: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[str]


class StockSplit(Base):
    __tablename__ = "stock_splits"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    split_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ratio: Mapped[Decimal] = mapped_column(Numeric(12, 6))  # 10 for a 10-for-1 split


class Price(Base):
    """Daily close as actually traded (NOT split-adjusted), to pair with as-reported
    share counts."""

    __tablename__ = "prices"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    source: Mapped[str] = mapped_column(String(32))


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    acquirer: Mapped[str]
    announcement_date: Mapped[date]
    close_date: Mapped[date | None]
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    equity_value: Mapped[Decimal | None] = mapped_column(Money)
    enterprise_value: Mapped[Decimal | None] = mapped_column(Money)
    consideration: Mapped[str | None] = mapped_column(String(16))  # cash | stock | mixed
    consideration_detail: Mapped[str | None]
    value_as_announced: Mapped[str | None]  # as worded in the press release
    # Last day before leaks/rumors moved the price; backtests use the day before
    # announcement when unset.
    unaffected_date: Mapped[date | None]
    verified: Mapped[bool] = mapped_column(default=False)  # checked by hand against source_url
    source_url: Mapped[str]
    notes: Mapped[str | None]

    __table_args__ = (UniqueConstraint("target_company_id", "announcement_date"),)


class DataQualityFlag(Base):
    """Result of an automated accounting check on one statement row (see app/validation.py)."""

    __tablename__ = "data_quality_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("financial_statements.id", ondelete="CASCADE"), index=True
    )
    check: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # error | warning | info
    message: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketSeries(Base):
    """Daily market-wide series: the risk-free rate (FRED DGS10, in percent) and the
    benchmark index for beta (SPY, total-return adjusted close)."""

    __tablename__ = "market_series"

    series: Mapped[str] = mapped_column(String(16), primary_key=True)
    obs_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4))
