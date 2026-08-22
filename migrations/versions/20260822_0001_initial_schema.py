"""Create the durable automation schema.

Revision ID: 20260822_0001
Revises:
Create Date: 2026-08-22
"""

from alembic import op

revision = "20260822_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE automation_state
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

        CREATE TABLE inbound_events (
            id BIGSERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            event_type TEXT NOT NULL,
            external_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'leased', 'retry', 'completed', 'dead')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            locked_at TIMESTAMPTZ,
            locked_by TEXT,
            lease_expires_at TIMESTAMPTZ,
            last_error TEXT,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (provider, external_id)
        );

        CREATE INDEX inbound_events_due_idx
            ON inbound_events (next_attempt_at, id)
            WHERE status IN ('queued', 'retry');
        CREATE INDEX inbound_events_stale_idx
            ON inbound_events (lease_expires_at)
            WHERE status = 'leased';

        CREATE TABLE IF NOT EXISTS processed_jobs (
            record_id BIGINT PRIMARY KEY,
            customer_id BIGINT,
            status TEXT NOT NULL,
            twilio_message_sid TEXT,
            last_error TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE processed_jobs
            ADD COLUMN IF NOT EXISTS last_error TEXT;
        ALTER TABLE processed_jobs
            ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        CREATE INDEX IF NOT EXISTS processed_jobs_customer_idx ON processed_jobs (customer_id);
        CREATE INDEX IF NOT EXISTS processed_jobs_status_idx
            ON processed_jobs (status, processed_at DESC);

        CREATE TABLE IF NOT EXISTS processed_alerts (
            alert_id BIGINT PRIMARY KEY,
            customer_id BIGINT,
            job_id BIGINT,
            alert_type TEXT NOT NULL,
            status TEXT NOT NULL,
            twilio_message_sid TEXT,
            last_error TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE processed_alerts
            ADD COLUMN IF NOT EXISTS last_error TEXT;
        ALTER TABLE processed_alerts
            ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        CREATE INDEX IF NOT EXISTS processed_alerts_customer_idx ON processed_alerts (customer_id);
        CREATE INDEX IF NOT EXISTS processed_alerts_status_idx
            ON processed_alerts (status, processed_at DESC);

        CREATE TABLE review_campaigns (
            id BIGSERIAL PRIMARY KEY,
            source_job_id BIGINT NOT NULL UNIQUE,
            customer_id BIGINT NOT NULL,
            customer_name TEXT,
            phone_e164 TEXT,
            email_normalized TEXT,
            review_token TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'completed', 'confirmed', 'cancelled')),
            clicked_at TIMESTAMPTZ,
            customer_replied_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ,
            google_review_id TEXT,
            google_reviewer_name TEXT,
            cancelled_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX review_campaigns_customer_idx
            ON review_campaigns (customer_id, created_at DESC);
        CREATE INDEX review_campaigns_status_idx
            ON review_campaigns (status, created_at DESC);
        CREATE INDEX review_campaigns_phone_idx
            ON review_campaigns (phone_e164, created_at DESC)
            WHERE phone_e164 IS NOT NULL;

        CREATE TABLE message_jobs (
            id BIGSERIAL PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            campaign_id BIGINT REFERENCES review_campaigns(id) ON DELETE CASCADE,
            inbound_event_id BIGINT REFERENCES inbound_events(id) ON DELETE SET NULL,
            customer_id BIGINT,
            channel TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
            message_kind TEXT NOT NULL,
            destination_normalized TEXT,
            template_key TEXT NOT NULL,
            template_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN (
                    'queued', 'leased', 'retry', 'sending', 'accepted',
                    'sent', 'delivered', 'failed', 'cancelled', 'dead',
                    'delivery_unknown'
                )),
            scheduled_at TIMESTAMPTZ NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
            locked_at TIMESTAMPTZ,
            locked_by TEXT,
            lease_expires_at TIMESTAMPTZ,
            provider TEXT,
            provider_message_id TEXT,
            provider_status TEXT,
            provider_status_rank SMALLINT NOT NULL DEFAULT 0,
            last_error_code TEXT,
            last_error TEXT,
            accepted_at TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            failed_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (campaign_id, message_kind)
        );
        CREATE INDEX message_jobs_due_idx
            ON message_jobs (scheduled_at, id)
            WHERE status IN ('queued', 'retry');
        CREATE INDEX message_jobs_stale_idx
            ON message_jobs (lease_expires_at)
            WHERE status IN ('leased', 'sending');
        CREATE INDEX message_jobs_provider_message_idx
            ON message_jobs (provider, provider_message_id)
            WHERE provider_message_id IS NOT NULL;
        CREATE INDEX message_jobs_campaign_idx ON message_jobs (campaign_id, created_at);

        CREATE TABLE message_attempts (
            id BIGSERIAL PRIMARY KEY,
            message_job_id BIGINT NOT NULL REFERENCES message_jobs(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            worker_id TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            outcome TEXT,
            provider_message_id TEXT,
            error_code TEXT,
            error_message TEXT,
            UNIQUE (message_job_id, attempt_number)
        );

        CREATE TABLE provider_message_events (
            id BIGSERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_message_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_rank SMALLINT NOT NULL DEFAULT 0,
            error_code TEXT,
            destination_normalized TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX provider_events_message_idx
            ON provider_message_events (provider, provider_message_id, received_at DESC);

        CREATE TABLE inbound_messages (
            id BIGSERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_message_id TEXT NOT NULL,
            from_normalized TEXT NOT NULL,
            to_normalized TEXT,
            body_ciphertext TEXT,
            body_preview TEXT,
            customer_id BIGINT,
            campaign_id BIGINT REFERENCES review_campaigns(id) ON DELETE SET NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (provider, provider_message_id)
        );
        CREATE INDEX inbound_messages_phone_idx
            ON inbound_messages (from_normalized, received_at DESC);

        CREATE TABLE communication_preferences (
            channel TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
            destination_normalized TEXT NOT NULL,
            suppressed BOOLEAN NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (channel, destination_normalized)
        );

        CREATE TABLE communication_preference_events (
            id BIGSERIAL PRIMARY KEY,
            channel TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
            destination_normalized TEXT NOT NULL,
            suppressed BOOLEAN NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX communication_preference_events_lookup_idx
            ON communication_preference_events (
                channel, destination_normalized, occurred_at DESC
            );

        CREATE TABLE unsubscribe_tokens (
            token TEXT PRIMARY KEY,
            message_job_id BIGINT NOT NULL UNIQUE
                REFERENCES message_jobs(id) ON DELETE CASCADE,
            channel TEXT NOT NULL CHECK (channel IN ('sms', 'email')),
            destination_normalized TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            used_at TIMESTAMPTZ
        );
        CREATE INDEX unsubscribe_tokens_destination_idx
            ON unsubscribe_tokens (channel, destination_normalized);

        CREATE TABLE oauth_credentials (
            provider TEXT PRIMARY KEY,
            encrypted_refresh_token TEXT NOT NULL,
            scopes TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE oauth_states (
            state_hash TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            initiated_by TEXT,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX oauth_states_expiry_idx ON oauth_states (expires_at);

        DO $$
        BEGIN
            IF to_regclass('public.google_reviews') IS NOT NULL
               AND to_regclass('public.legacy_google_reviews') IS NULL THEN
                ALTER TABLE google_reviews RENAME TO legacy_google_reviews;
            END IF;
        END $$;

        CREATE TABLE google_reviews (
            google_review_id TEXT PRIMARY KEY,
            reviewer_name TEXT,
            star_rating INTEGER CHECK (star_rating BETWEEN 1 AND 5),
            review_comment TEXT,
            review_created_at TIMESTAMPTZ,
            review_updated_at TIMESTAMPTZ,
            campaign_id BIGINT REFERENCES review_campaigns(id) ON DELETE SET NULL,
            match_status TEXT NOT NULL DEFAULT 'unmatched'
                CHECK (match_status IN ('unmatched', 'candidate', 'matched', 'ignored')),
            match_confidence NUMERIC(4, 3),
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX google_reviews_match_idx
            ON google_reviews (match_status, review_created_at DESC);

        CREATE TABLE audit_events (
            id BIGSERIAL PRIMARY KEY,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            before_data JSONB,
            after_data JSONB,
            request_id TEXT,
            ip_address INET,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX audit_events_entity_idx
            ON audit_events (entity_type, entity_id, occurred_at DESC);

        CREATE TABLE worker_heartbeats (
            worker_name TEXT PRIMARY KEY,
            last_started_at TIMESTAMPTZ,
            last_succeeded_at TIMESTAMPTZ,
            last_failed_at TIMESTAMPTZ,
            last_error TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        DO $$
        BEGIN
          IF to_regclass('public.communication_suppressions') IS NOT NULL THEN
            INSERT INTO communication_preferences (
                channel, destination_normalized, suppressed, reason, source,
                changed_at
            )
            SELECT
                channel,
                destination,
                is_suppressed,
                COALESCE(reason, 'legacy_import'),
                COALESCE(source, 'legacy_import'),
                COALESCE(updated_at, created_at, NOW())
            FROM communication_suppressions
            ON CONFLICT (channel, destination_normalized)
            DO UPDATE SET
                suppressed = EXCLUDED.suppressed,
                reason = EXCLUDED.reason,
                source = EXCLUDED.source,
                changed_at = EXCLUDED.changed_at;
          END IF;
        END $$;

        DO $$
        BEGIN
          IF to_regclass('public.review_requests') IS NOT NULL THEN
            INSERT INTO review_campaigns (
            source_job_id, customer_id, customer_name, phone_e164,
            review_token, status, clicked_at, customer_replied_at,
            confirmed_at, google_review_id, google_reviewer_name,
            cancelled_reason, created_at, updated_at
            )
            SELECT
            record_id,
            customer_id,
            customer_name,
            CASE
                WHEN LENGTH(regexp_replace(COALESCE(customer_phone, ''), '[^0-9]', '', 'g')) = 10
                    THEN '+1' || regexp_replace(customer_phone, '[^0-9]', '', 'g')
                WHEN LENGTH(regexp_replace(COALESCE(customer_phone, ''), '[^0-9]', '', 'g')) = 11
                    THEN '+' || regexp_replace(customer_phone, '[^0-9]', '', 'g')
                ELSE NULL
            END,
            COALESCE(
                review_token,
                replace(gen_random_uuid()::TEXT, '-', '') ||
                replace(gen_random_uuid()::TEXT, '-', '')
            ),
            CASE
                WHEN confirmed_review_at IS NOT NULL THEN 'confirmed'
                WHEN status = 'completed' THEN 'completed'
                WHEN status IN ('cancelled', 'send_failed') THEN 'cancelled'
                ELSE 'active'
            END,
            link_clicked_at,
            customer_replied_at,
            confirmed_review_at,
            google_review_id,
            google_reviewer_name,
            cancelled_reason,
            COALESCE(created_at, NOW()),
            COALESCE(updated_at, NOW())
            FROM review_requests
            ON CONFLICT (source_job_id) DO NOTHING;
          END IF;
        END $$;

        DO $$
        BEGIN
          IF to_regclass('public.legacy_google_reviews') IS NOT NULL THEN
            INSERT INTO google_reviews (
            google_review_id, reviewer_name, star_rating, review_comment,
            review_created_at, review_updated_at, campaign_id,
            match_status, imported_at, updated_at
            )
            SELECT
            legacy.google_review_id,
            legacy.reviewer_name,
            CASE
                WHEN legacy.star_rating ~ '^[1-5]$' THEN legacy.star_rating::INTEGER
                WHEN UPPER(legacy.star_rating) = 'ONE' THEN 1
                WHEN UPPER(legacy.star_rating) = 'TWO' THEN 2
                WHEN UPPER(legacy.star_rating) = 'THREE' THEN 3
                WHEN UPPER(legacy.star_rating) = 'FOUR' THEN 4
                WHEN UPPER(legacy.star_rating) = 'FIVE' THEN 5
                ELSE NULL
            END,
            legacy.review_comment,
            legacy.review_created_at,
            legacy.review_updated_at,
            campaign.id,
            CASE WHEN campaign.id IS NOT NULL THEN 'matched' ELSE 'unmatched' END,
            COALESCE(legacy.imported_at, NOW()),
            COALESCE(legacy.updated_at, NOW())
            FROM legacy_google_reviews AS legacy
            LEFT JOIN review_campaigns AS campaign
              ON campaign.source_job_id = legacy.matched_record_id
            ON CONFLICT (google_review_id) DO NOTHING;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS worker_heartbeats;
        DROP TABLE IF EXISTS audit_events;
        DROP TABLE IF EXISTS google_reviews;
        DO $$
        BEGIN
            IF to_regclass('public.legacy_google_reviews') IS NOT NULL
               AND to_regclass('public.google_reviews') IS NULL THEN
                ALTER TABLE legacy_google_reviews RENAME TO google_reviews;
            END IF;
        END $$;
        DROP TABLE IF EXISTS oauth_states;
        DROP TABLE IF EXISTS oauth_credentials;
        DROP TABLE IF EXISTS unsubscribe_tokens;
        DROP TABLE IF EXISTS communication_preference_events;
        DROP TABLE IF EXISTS communication_preferences;
        DROP TABLE IF EXISTS inbound_messages;
        DROP TABLE IF EXISTS provider_message_events;
        DROP TABLE IF EXISTS message_attempts;
        DROP TABLE IF EXISTS message_jobs;
        DROP TABLE IF EXISTS review_campaigns;
        DROP TABLE IF EXISTS inbound_events;
        """
    )
