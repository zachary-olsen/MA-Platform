"""Prices, stock splits and point-in-time market capitalization.

Prices are stored *as traded*, not split-adjusted. Share counts in filings are as
reported at the time, so pairing an adjusted price with them would be off by the split
ratio (NVDA in 2023 would come out 10x too small). Market cap at date D:

    close on D (as traded) x latest share count filed by D x splits between the
    share count's date and D

A side benefit: as-traded history never changes, so updates only append new days.

Source: Yahoo Finance via yfinance, which is unofficial and fine for prototyping. It
only covers currently listed tickers; delisted backtest targets take their
pre-announcement price from deal documents instead.

Market-wide series (market_series table): the 10-year Treasury yield from FRED
(risk-free rate) and SPY (benchmark for beta).
"""

import io
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Company, MarketSeries, Price, StockSplit

DEFAULT_START = date(2012, 1, 1)
BATCH_SIZE = 50
RISK_FREE_SERIES = "DGS10"
BENCHMARK = "SPY"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


def yahoo_symbol(ticker: str) -> str:
    return ticker.replace(".", "-")


def as_traded(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Undo split adjustment: multiply each close by every split ratio after its date."""
    factor = pd.Series(1.0, index=close.index)
    for split_date, ratio in splits.items():
        factor[close.index < split_date] *= ratio
    return close * factor


def fetch_yahoo(tickers: list[str], start: date) -> dict[str, tuple[pd.Series, pd.Series]]:
    """ticker -> (as-traded closes, split ratios), both indexed by date."""
    import yfinance as yf

    out = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        frame = yf.download(
            [yahoo_symbol(t) for t in batch],
            start=start.isoformat(),
            auto_adjust=False,
            actions=True,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        if frame.empty:
            continue
        for ticker in batch:
            symbol = yahoo_symbol(ticker)
            if symbol not in frame.columns.get_level_values(0):
                continue
            data = frame[symbol].dropna(subset=["Close"])
            if data.empty:
                continue
            splits = data["Stock Splits"][data["Stock Splits"] > 0]
            closes = as_traded(data["Close"], splits)
            closes.index = closes.index.date
            splits.index = splits.index.date
            out[ticker] = (closes, splits)
    return out


def update_prices(session: Session, companies: Iterable[Company], full: bool = False) -> dict:
    """Append new daily closes (and splits) for each company. Returns rows added per ticker."""
    companies = list(companies)
    last_dates = dict(
        session.execute(
            select(Price.company_id, func.max(Price.trade_date)).group_by(Price.company_id)
        ).all()
    )

    # One request window per distinct start date keeps batches large.
    by_start: dict[date, list[Company]] = {}
    for company in companies:
        last = None if full else last_dates.get(company.id)
        start = last + timedelta(days=1) if last else DEFAULT_START
        by_start.setdefault(start, []).append(company)

    added = {}
    for start, group in by_start.items():
        if start > date.today():
            continue
        fetched = fetch_yahoo([c.ticker for c in group], start)
        for company in group:
            if company.ticker not in fetched:
                continue
            closes, splits = fetched[company.ticker]
            price_rows = [
                {"company_id": company.id, "trade_date": d, "close": float(c), "source": "yahoo"}
                for d, c in closes.items()
                if d >= start
            ]
            if price_rows:
                session.execute(insert(Price).on_conflict_do_nothing(), price_rows)
            split_rows = [
                {"company_id": company.id, "split_date": d, "ratio": float(r)}
                for d, r in splits.items()
            ]
            if split_rows:
                session.execute(insert(StockSplit).on_conflict_do_nothing(), split_rows)
            added[company.ticker] = len(price_rows)
    return added


_MARKET_AS_OF = text(
    """
    WITH px AS (
        SELECT DISTINCT ON (company_id) company_id, trade_date, close
        FROM prices
        WHERE trade_date <= :as_of AND trade_date > :as_of - 10
        ORDER BY company_id, trade_date DESC
    ),
    sh AS (
        SELECT DISTINCT ON (company_id) company_id, as_of_date, shares
        FROM share_counts
        WHERE filed_date <= :as_of
        ORDER BY company_id, filed_date DESC, as_of_date DESC
    )
    SELECT
        sh.company_id,
        px.trade_date AS price_date,
        px.close::float8 AS price,
        sh.as_of_date AS shares_date,
        (sh.shares * COALESCE(adj.factor, 1))::float8 AS shares,
        (px.close * sh.shares * COALESCE(adj.factor, 1))::float8 AS market_cap
    FROM sh
    LEFT JOIN px USING (company_id)
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stock_splits s
        WHERE s.company_id = sh.company_id
          AND s.split_date > sh.as_of_date
          AND s.split_date <= COALESCE(px.trade_date, :as_of)
    ) adj ON true
    """
)


def market_as_of(session: Session, as_of: date) -> pd.DataFrame:
    """Price, split-consistent share count and market cap per company on `as_of`.

    Uses the last close within 10 days before `as_of` and the latest share count filed
    by then. Companies with a share count but no price (e.g. acquired targets, which
    Yahoo doesn't cover) are included with NULL price and market cap.
    """
    return pd.read_sql(_MARKET_AS_OF, session.connection(), params={"as_of": as_of})


def update_market_series(session: Session) -> dict[str, int]:
    """Refresh the risk-free rate and benchmark series (small; refetched in full)."""
    import yfinance as yf

    response = httpx.get(FRED_CSV_URL.format(series=RISK_FREE_SERIES), timeout=30)
    response.raise_for_status()
    fred = pd.read_csv(io.StringIO(response.text), na_values=".").dropna()
    fred.columns = ["obs_date", "value"]

    spy = yf.download(BENCHMARK, start=DEFAULT_START.isoformat(), auto_adjust=True, progress=False)
    spy_close = spy["Close"].squeeze().dropna()

    series = {
        RISK_FREE_SERIES: [
            {"series": RISK_FREE_SERIES, "obs_date": date.fromisoformat(d), "value": v}
            for d, v in zip(fred["obs_date"], fred["value"], strict=True)
            if date.fromisoformat(d) >= DEFAULT_START
        ],
        BENCHMARK: [
            {"series": BENCHMARK, "obs_date": d.date(), "value": float(v)}
            for d, v in spy_close.items()
        ],
    }
    for rows in series.values():
        stmt = insert(MarketSeries).values(rows)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["series", "obs_date"], set_={"value": stmt.excluded.value}
            )
        )
    return {name: len(rows) for name, rows in series.items()}


def risk_free_rate(session: Session, as_of: date) -> float:
    """10-year Treasury yield on `as_of` (latest observation), as a decimal."""
    value = session.scalar(
        select(MarketSeries.value)
        .where(MarketSeries.series == RISK_FREE_SERIES, MarketSeries.obs_date <= as_of)
        .order_by(MarketSeries.obs_date.desc())
        .limit(1)
    )
    if value is None:
        raise LookupError("No risk-free rate stored; run `python -m app.cli prices`")
    return float(value) / 100


def _split_adjusted_closes(
    session: Session, company_ids: Sequence[int], start: date, end: date
) -> pd.DataFrame:
    """Wide frame of closes (dates x company_id), adjusted so splits aren't returns."""
    prices = pd.read_sql(
        select(Price.company_id, Price.trade_date, Price.close).where(
            Price.company_id.in_(company_ids), Price.trade_date.between(start, end)
        ),
        session.connection(),
    )
    if prices.empty:
        return pd.DataFrame()
    prices["close"] = prices["close"].astype(float)
    splits = pd.read_sql(
        select(StockSplit.company_id, StockSplit.split_date, StockSplit.ratio).where(
            StockSplit.company_id.in_(company_ids),
            StockSplit.split_date.between(start, end),
        ),
        session.connection(),
    )
    for split in splits.itertuples():
        before = (prices["company_id"] == split.company_id) & (
            prices["trade_date"] < split.split_date
        )
        prices.loc[before, "close"] /= float(split.ratio)
    wide = prices.pivot(index="trade_date", columns="company_id", values="close")
    wide.index = pd.to_datetime(wide.index)
    return wide


def betas(session: Session, as_of: date, company_ids: Sequence[int], years: int = 2) -> pd.Series:
    """Beta vs SPY from weekly returns over the `years` before `as_of`.

    Weekly rather than daily returns reduce the downward bias from thinly traded
    stocks reacting to the market with a lag. Companies with fewer than a year of
    returns get NaN.
    """
    start = as_of - timedelta(days=365 * years + 7)
    stocks = _split_adjusted_closes(session, company_ids, start, as_of)
    market = pd.read_sql(
        select(MarketSeries.obs_date, MarketSeries.value).where(
            MarketSeries.series == BENCHMARK, MarketSeries.obs_date.between(start, as_of)
        ),
        session.connection(),
        index_col="obs_date",
        parse_dates=["obs_date"],
    )["value"].astype(float)
    if stocks.empty or market.empty:
        return pd.Series(np.nan, index=list(company_ids), dtype=float)

    weekly = stocks.resample("W-FRI").last().pct_change(fill_method=None)
    market_weekly = market.resample("W-FRI").last().pct_change()
    weekly, market_weekly = weekly.align(market_weekly, join="inner", axis=0)

    out = {}
    for company_id in company_ids:
        if company_id not in weekly:
            out[company_id] = np.nan
            continue
        pair = pd.concat([weekly[company_id], market_weekly], axis=1).dropna()
        if len(pair) < 52:
            out[company_id] = np.nan
            continue
        cov = np.cov(pair.iloc[:, 0], pair.iloc[:, 1])
        out[company_id] = cov[0, 1] / cov[1, 1]
    return pd.Series(out, dtype=float)
