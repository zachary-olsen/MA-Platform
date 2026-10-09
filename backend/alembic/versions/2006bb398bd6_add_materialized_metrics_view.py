"""add materialized metrics view

Revision ID: 2006bb398bd6
Revises: a0bcbfc4917c
Create Date: 2026-10-09 17:53:22.498365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2006bb398bd6'
down_revision: Union[str, Sequence[str], None] = 'a0bcbfc4917c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# v_financial_metrics recomputes every statement row's metrics (two lateral lookups
# each) on every query. Metrics only change when filings are ingested, so they're
# materialized and refreshed after ingest. Each row is still computed as of its own
# filed_date, so filtering on filed_date <= D stays point-in-time correct.


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE MATERIALIZED VIEW mv_financial_metrics AS SELECT * FROM v_financial_metrics")
    # Unique index lets REFRESH ... CONCURRENTLY run without blocking readers.
    op.execute("CREATE UNIQUE INDEX ux_mv_metrics_statement ON mv_financial_metrics (statement_id)")
    op.execute(
        "CREATE INDEX ix_mv_metrics_pit ON mv_financial_metrics "
        "(company_id, period_end DESC, filed_date DESC, accession DESC)"
    )
    op.execute("CREATE INDEX ix_mv_metrics_filed ON mv_financial_metrics (filed_date)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP MATERIALIZED VIEW mv_financial_metrics")
