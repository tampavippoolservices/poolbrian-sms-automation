"""Store traceable PoolBrain outcomes for website leads.

Revision ID: 20260903_0002
Revises: 20260822_0001
Create Date: 2026-09-03
"""

from alembic import op

revision = "20260903_0002"
down_revision = "20260822_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE inbound_events
            ADD COLUMN IF NOT EXISTS provider_record_id TEXT;
        ALTER TABLE inbound_events
            ADD COLUMN IF NOT EXISTS result JSONB;
        CREATE INDEX IF NOT EXISTS inbound_events_provider_record_idx
            ON inbound_events (provider, provider_record_id)
            WHERE provider_record_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS inbound_events_provider_record_idx")
    op.execute("ALTER TABLE inbound_events DROP COLUMN IF EXISTS result")
    op.execute("ALTER TABLE inbound_events DROP COLUMN IF EXISTS provider_record_id")
