import os

import psycopg

def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise Exception("DATABASE_URL is missing")

    return psycopg.connect(database_url)


def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_jobs (
                    record_id BIGINT PRIMARY KEY,
                    customer_id BIGINT,
                    status TEXT NOT NULL,
                    processed_at TIMESTAMPTZ DEFAULT NOW(),
                    twilio_message_sid TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_alerts (
                alert_id BIGINT PRIMARY KEY,
                customer_id BIGINT,
                job_id BIGINT,
                alert_type TEXT NOT NULL,
                status TEXT NOT NULL,
                processed_at TIMESTAMPTZ DEFAULT NOW(),
                twilio_message_sid TEXT
             )
         """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS automation_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS review_requests (
                    record_id BIGINT PRIMARY KEY,
                    customer_id BIGINT NOT NULL,
                    customer_phone TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    scheduled_for TIMESTAMPTZ NOT NULL,
                    first_message_sid TEXT,
                    first_sent_at TIMESTAMPTZ,
                    link_clicked_at TIMESTAMPTZ,
                    customer_replied_at TIMESTAMPTZ,
                    reminder_message_sid TEXT,
                    reminder_sent_at TIMESTAMPTZ,
                    cancelled_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS review_token TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS confirmed_review_at TIMESTAMPTZ
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS google_review_id TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS google_reviewer_name TEXT
            """)
            
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                review_requests_review_token_key
                ON review_requests (review_token)
                WHERE review_token IS NOT NULL
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS customer_name TEXT
            """)

            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS first_delivery_status TEXT
            """)
            
            cur.execute("""
                ALTER TABLE review_requests
                ADD COLUMN IF NOT EXISTS first_delivery_updated_at TIMESTAMPTZ
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS
                communication_suppressions (
                    channel TEXT NOT NULL
                        CHECK (channel IN ('sms', 'email')),
                    destination TEXT NOT NULL,
                    is_suppressed BOOLEAN NOT NULL
                        DEFAULT TRUE,
                    reason TEXT,
                    source TEXT,
                    suppressed_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    resumed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    updated_at TIMESTAMPTZ
                        DEFAULT NOW(),
                    PRIMARY KEY (channel, destination)
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS google_reviews (
                    google_review_id TEXT PRIMARY KEY,
                    reviewer_name TEXT,
                    star_rating TEXT,
                    review_comment TEXT,
                    review_created_at TIMESTAMPTZ,
                    review_updated_at TIMESTAMPTZ,
                    matched_record_id BIGINT,
                    match_status TEXT NOT NULL DEFAULT 'unmatched',
                    imported_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            
            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                google_reviews_match_status_idx
                ON google_reviews (match_status)
            """)

        conn.commit()
