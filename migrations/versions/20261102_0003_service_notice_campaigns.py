"""Add auditable operational service notice campaigns.

Revision ID: 20261102_0003
Revises: 20260903_0002
Create Date: 2026-11-02
"""

from alembic import op

revision = "20261102_0003"
down_revision = "20260903_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE service_notice_campaigns (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            message_body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'cancelled')),
            consent_basis TEXT NOT NULL,
            consent_evidence TEXT NOT NULL,
            recipient_count INTEGER NOT NULL CHECK (recipient_count > 0),
            scheduled_at TIMESTAMPTZ NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            cancelled_at TIMESTAMPTZ
        );
        CREATE INDEX service_notice_campaigns_created_idx
            ON service_notice_campaigns (created_at DESC);

        CREATE TABLE service_notice_recipients (
            campaign_id BIGINT NOT NULL
                REFERENCES service_notice_campaigns(id) ON DELETE CASCADE,
            customer_id BIGINT NOT NULL,
            customer_name TEXT NOT NULL,
            phone_e164 TEXT NOT NULL,
            message_job_id BIGINT NOT NULL UNIQUE
                REFERENCES message_jobs(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (campaign_id, phone_e164)
        );
        CREATE INDEX service_notice_recipients_customer_idx
            ON service_notice_recipients (customer_id, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS service_notice_recipients")
    op.execute("DROP TABLE IF EXISTS service_notice_campaigns")
