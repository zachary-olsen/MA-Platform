from datetime import date

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Company, FinancialStatement
from app.queries import latest_statement_as_of, statements_as_of


@pytest.fixture
def session():
    """A session inside a transaction that is rolled back, so the dev DB is untouched."""
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    transaction = connection.begin()
    yield Session(bind=connection)
    transaction.rollback()
    connection.close()


def statement(company, period_end, filed, revenue):
    return FinancialStatement(
        company_id=company.id,
        fiscal_year=period_end.year,
        period_end=period_end,
        accession=f"test-{filed.isoformat()}",
        form="10-K",
        filed_date=filed,
        revenue=revenue,
        tag_sources={},
    )


def test_point_in_time_uses_only_filings_public_by_as_of(session):
    company = Company(cik=9_999_999_999, ticker="TEST", name="Test Co")
    session.add(company)
    session.flush()
    fy22, fy23 = date(2022, 12, 31), date(2023, 12, 31)
    session.add_all(
        [
            statement(company, fy22, date(2023, 2, 15), 100),  # original
            statement(company, fy23, date(2024, 2, 15), 120),
            statement(company, fy22, date(2024, 2, 15), 95),  # restated in the FY23 10-K
        ]
    )
    session.flush()

    before = statements_as_of(session, date(2023, 6, 1), [company.id])
    assert [(s.period_end, s.revenue) for s in before] == [(fy22, 100)]

    after = statements_as_of(session, date(2024, 6, 1), [company.id])
    assert [(s.period_end, s.revenue) for s in after] == [(fy22, 95), (fy23, 120)]

    latest = latest_statement_as_of(session, date(2024, 6, 1), [company.id])
    assert [(s.period_end, s.revenue) for s in latest] == [(fy23, 120)]

    assert statements_as_of(session, date(2023, 1, 1), [company.id]) == []
