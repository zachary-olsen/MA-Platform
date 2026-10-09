from datetime import date

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Company, FinancialStatement
from app.queries import latest_statement_as_of, metrics_as_of, statements_as_of


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


def test_metrics_view_growth_is_point_in_time(session):
    company = Company(cik=9_999_999_998, ticker="TEST2", name="Test Co 2")
    session.add(company)
    session.flush()
    fy22, fy23 = date(2022, 12, 31), date(2023, 12, 31)
    fy22_original = statement(company, fy22, date(2023, 2, 15), 100)
    fy23_row = statement(company, fy23, date(2024, 2, 15), 120)
    fy22_restated = statement(company, fy22, date(2024, 2, 15), 96)
    fy23_row.operating_income, fy23_row.d_and_a = 30, 6
    fy23_row.cfo, fy23_row.capex = 25, 5
    fy23_row.total_debt, fy23_row.cash, fy23_row.short_term_investments = 50, 10, 4
    session.add_all([fy22_original, fy23_row, fy22_restated])
    session.flush()

    df = metrics_as_of(session, date(2024, 6, 1), [company.id]).set_index("fiscal_year")
    fy23_metrics = df.loc[2023]
    # Growth uses the restated comparative from the same 10-K: 120 / 96 - 1
    assert fy23_metrics["revenue_growth"] == pytest.approx(0.25)
    assert fy23_metrics["ebitda"] == 36
    assert fy23_metrics["ebitda_margin"] == pytest.approx(0.3)
    assert fy23_metrics["fcf"] == 20
    assert fy23_metrics["net_debt"] == 36
    assert fy23_metrics["net_debt_to_ebitda"] == pytest.approx(1.0)

    latest = metrics_as_of(session, date(2024, 6, 1), [company.id], latest_only=True)
    assert latest["fiscal_year"].tolist() == [2023]
