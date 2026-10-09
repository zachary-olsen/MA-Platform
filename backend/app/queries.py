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

from sqlalchemy import select
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
