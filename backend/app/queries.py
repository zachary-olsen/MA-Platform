"""Point-in-time queries: what could anyone have known on a given date?

Both use Postgres DISTINCT ON, which keeps the first row of each group under the
ORDER BY. In SQL, statements_as_of is:

    SELECT DISTINCT ON (company_id, period_end) *
    FROM financial_statements
    WHERE filed_date <= :as_of
    ORDER BY company_id, period_end, filed_date DESC, accession DESC;
"""

from collections.abc import Sequence
from datetime import date

import pandas as pd
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.models import FinancialStatement as FS


def statements_as_of(
    session: Session, as_of: date, company_ids: Sequence[int] | None = None
) -> list[FS]:
    """Every annual period per company, using the latest version filed by `as_of`."""
    stmt = (
        select(FS)
        .where(FS.filed_date <= as_of)
        .ext(distinct_on(FS.company_id, FS.period_end))
        .order_by(FS.company_id, FS.period_end, FS.filed_date.desc(), FS.accession.desc())
    )
    if company_ids is not None:
        stmt = stmt.where(FS.company_id.in_(company_ids))
    return list(session.scalars(stmt))


def latest_statement_as_of(
    session: Session, as_of: date, company_ids: Sequence[int] | None = None
) -> list[FS]:
    """The most recent annual period per company known by `as_of` (one row each)."""
    stmt = (
        select(FS)
        .where(FS.filed_date <= as_of)
        .ext(distinct_on(FS.company_id))
        .order_by(FS.company_id, FS.period_end.desc(), FS.filed_date.desc(), FS.accession.desc())
    )
    if company_ids is not None:
        stmt = stmt.where(FS.company_id.in_(company_ids))
    return list(session.scalars(stmt))


def _metrics_sql(source: str, latest_only: bool):
    # Latest version of each (company, period) filed by :as_of; with latest_only, just
    # each company's most recent period, decided in SQL rather than in pandas.
    distinct, order = (
        ("m.company_id", "m.company_id, m.period_end DESC, m.filed_date DESC, m.accession DESC")
        if latest_only
        else (
            "m.company_id, m.period_end",
            "m.company_id, m.period_end, m.filed_date DESC, m.accession DESC",
        )
    )
    return text(
        f"""
        SELECT DISTINCT ON ({distinct}) c.ticker, m.*
        FROM {source} m
        JOIN companies c ON c.id = m.company_id
        WHERE m.filed_date <= :as_of
          AND (:all_companies OR m.company_id IN :company_ids)
        ORDER BY {order}
        """
    ).bindparams(bindparam("company_ids", expanding=True))


_METRICS_QUERIES = {
    (source, latest): _metrics_sql(source, latest)
    for source in ("mv_financial_metrics", "v_financial_metrics")
    for latest in (True, False)
}


def metrics_as_of(
    session: Session,
    as_of: date,
    company_ids: Sequence[int] | None = None,
    latest_only: bool = False,
    materialized: bool = True,
) -> pd.DataFrame:
    """Derived metrics (growth, margins, FCF, leverage) as known on `as_of`.

    One row per company and fiscal period, or with latest_only, just each company's
    most recent period. Reads the materialized view (refreshed after ingest);
    `materialized=False` computes from the live view, for tests and benchmarks.
    """
    source = "mv_financial_metrics" if materialized else "v_financial_metrics"
    df = pd.read_sql(
        _METRICS_QUERIES[(source, latest_only)],
        session.connection(),
        params={
            "as_of": as_of,
            "all_companies": company_ids is None,
            "company_ids": list(company_ids) if company_ids else [-1],
        },
    )
    return df.sort_values(["company_id", "period_end"]).reset_index(drop=True)


def refresh_metrics(session: Session) -> None:
    """Recompute the materialized metrics after statements change (ingest, deals)."""
    session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_financial_metrics"))
