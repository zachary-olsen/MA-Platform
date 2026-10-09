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
"""

from collections.abc import Iterable
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Company, Price, StockSplit

DEFAULT_START = date(2012, 1, 1)
BATCH_SIZE = 50


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
        px.company_id,
        px.trade_date AS price_date,
        px.close::float8 AS price,
        sh.as_of_date AS shares_date,
        (sh.shares * COALESCE(adj.factor, 1))::float8 AS shares,
        (px.close * sh.shares * COALESCE(adj.factor, 1))::float8 AS market_cap
    FROM px
    JOIN sh USING (company_id)
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stock_splits s
        WHERE s.company_id = px.company_id
          AND s.split_date > sh.as_of_date
          AND s.split_date <= px.trade_date
    ) adj ON true
    """
)


def market_as_of(session: Session, as_of: date) -> pd.DataFrame:
    """Price, split-consistent share count and market cap per company on `as_of`.

    Uses the last close within 10 days before `as_of` and the latest share count filed
    by then. Companies missing either are absent.
    """
    return pd.read_sql(_MARKET_AS_OF, session.connection(), params={"as_of": as_of})
