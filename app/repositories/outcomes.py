from __future__ import annotations

from sqlalchemy import text

from app.db import transaction


def set_processed_job_outcome(
    record_id: int,
    customer_id: int | None,
    status: str,
    *,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO processed_jobs (
                    record_id, customer_id, status, twilio_message_sid, last_error
                ) VALUES (
                    :record_id, :customer_id, :status, :message_id, :error
                )
                ON CONFLICT (record_id)
                DO UPDATE SET status = EXCLUDED.status,
                    twilio_message_sid = COALESCE(
                        EXCLUDED.twilio_message_sid,
                        processed_jobs.twilio_message_sid
                    ),
                    last_error = EXCLUDED.last_error,
                    processed_at = NOW()
                """
            ),
            {
                "record_id": record_id,
                "customer_id": customer_id,
                "status": status,
                "message_id": provider_message_id,
                "error": error[:1000] if error else None,
            },
        )


def set_processed_alert_outcome(
    alert_id: int,
    customer_id: int | None,
    source_job_id: int | None,
    status: str,
    *,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO processed_alerts (
                    alert_id, customer_id, job_id, alert_type, status,
                    twilio_message_sid, last_error
                ) VALUES (
                    :alert_id, :customer_id, :job_id, 'WaterLevelLow', :status,
                    :message_id, :error
                )
                ON CONFLICT (alert_id)
                DO UPDATE SET status = EXCLUDED.status,
                    twilio_message_sid = COALESCE(
                        EXCLUDED.twilio_message_sid,
                        processed_alerts.twilio_message_sid
                    ),
                    last_error = EXCLUDED.last_error,
                    processed_at = NOW()
                """
            ),
            {
                "alert_id": alert_id,
                "customer_id": customer_id,
                "job_id": source_job_id,
                "status": status,
                "message_id": provider_message_id,
                "error": error[:1000] if error else None,
            },
        )
