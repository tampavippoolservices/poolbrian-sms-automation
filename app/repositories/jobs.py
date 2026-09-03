from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db import transaction


def claim_message_jobs(
    *,
    worker_id: str,
    limit: int,
    lease_minutes: int,
    allowed_kinds: list[str] | None = None,
) -> list[dict[str, Any]]:
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                WITH due AS (
                    SELECT
                        job.id,
                        COALESCE(
                            (
                                SELECT MAX(attempt.attempt_number)
                                FROM message_attempts AS attempt
                                WHERE attempt.message_job_id = job.id
                            ),
                            0
                        ) AS recorded_attempt_count
                    FROM message_jobs AS job
                    LEFT JOIN review_campaigns AS campaign ON campaign.id = job.campaign_id
                    LEFT JOIN communication_preferences AS preference
                      ON preference.channel = job.channel
                     AND preference.destination_normalized = job.destination_normalized
                    WHERE job.status IN ('queued', 'retry')
                      AND job.scheduled_at <= NOW()
                      AND COALESCE(preference.suppressed, FALSE) = FALSE
                      AND (campaign.id IS NULL OR campaign.status = 'active')
                      AND (
                          CAST(:allowed_kinds AS TEXT[]) IS NULL
                          OR job.message_kind = ANY(CAST(:allowed_kinds AS TEXT[]))
                      )
                    ORDER BY job.scheduled_at, job.id
                    LIMIT :limit
                    FOR UPDATE OF job SKIP LOCKED
                )
                UPDATE message_jobs AS job
                SET status = 'leased',
                    attempt_count = GREATEST(
                        job.attempt_count,
                        due.recorded_attempt_count
                    ) + 1,
                    locked_at = NOW(),
                    locked_by = :worker_id,
                    lease_expires_at = NOW() + (:lease_minutes * INTERVAL '1 minute'),
                    updated_at = NOW()
                FROM due
                WHERE job.id = due.id
                RETURNING job.*
                """
            ),
            {
                "limit": limit,
                "worker_id": worker_id,
                "lease_minutes": lease_minutes,
                "allowed_kinds": allowed_kinds,
            },
        ).mappings()
        claimed = [dict(row) for row in rows]
        for job in claimed:
            connection.execute(
                text(
                    """
                    INSERT INTO message_attempts (
                        message_job_id, attempt_number, worker_id
                    ) VALUES (:job_id, :attempt_number, :worker_id)
                    """
                ),
                {
                    "job_id": job["id"],
                    "attempt_number": job["attempt_count"],
                    "worker_id": worker_id,
                },
            )
        return claimed


def mark_sending(job_id: int, worker_id: str) -> bool:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'sending', updated_at = NOW()
                WHERE id = :job_id AND locked_by = :worker_id AND status = 'leased'
                """
            ),
            {"job_id": job_id, "worker_id": worker_id},
        )
        return result.rowcount == 1


def mark_accepted(
    *,
    job_id: int,
    worker_id: str,
    provider: str,
    provider_message_id: str,
    provider_status: str,
    status_rank: int,
) -> bool:
    with transaction() as connection:
        row = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = CASE
                        WHEN provider_status_rank > :status_rank THEN status
                        ELSE 'accepted'
                    END,
                    provider = COALESCE(provider, :provider),
                    provider_message_id = COALESCE(provider_message_id, :provider_message_id),
                    provider_status = CASE
                        WHEN provider_status_rank > :status_rank THEN provider_status
                        ELSE :provider_status
                    END,
                    provider_status_rank = GREATEST(provider_status_rank, :status_rank),
                    accepted_at = NOW(),
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, last_error = NULL, updated_at = NOW()
                WHERE id = :job_id AND locked_by = :worker_id
                  AND status IN ('sending', 'accepted', 'sent', 'delivered', 'failed')
                  AND (
                      provider_message_id IS NULL
                      OR provider_message_id = :provider_message_id
                  )
                RETURNING status
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "provider": provider,
                "provider_message_id": provider_message_id,
                "provider_status": provider_status,
                "status_rank": status_rank,
            },
        ).first()
        if not row:
            return False
        connection.execute(
            text(
                """
                UPDATE message_attempts
                SET finished_at = NOW(), outcome = :outcome,
                    provider_message_id = :provider_message_id
                WHERE message_job_id = :job_id
                  AND attempt_number = (
                      SELECT attempt_count FROM message_jobs WHERE id = :job_id
                  )
                """
            ),
            {
                "job_id": job_id,
                "provider_message_id": provider_message_id,
                "outcome": str(row.status),
            },
        )
        return True


def fail_message_job(
    *,
    job_id: int,
    worker_id: str,
    error: str,
    error_code: str | None,
    retryable: bool,
    next_attempt_at: datetime,
) -> str:
    with transaction() as connection:
        row = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = CASE
                        WHEN :retryable AND attempt_count < max_attempts THEN 'retry'
                        WHEN attempt_count >= max_attempts THEN 'dead'
                        ELSE 'failed'
                    END,
                    scheduled_at = CASE
                        WHEN :retryable AND attempt_count < max_attempts
                            THEN :next_attempt_at
                        ELSE scheduled_at
                    END,
                    failed_at = CASE
                        WHEN :retryable AND attempt_count < max_attempts THEN failed_at
                        ELSE NOW()
                    END,
                    last_error_code = :error_code,
                    last_error = :error,
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :job_id AND locked_by = :worker_id
                  AND status IN ('leased', 'sending')
                RETURNING status
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "retryable": retryable,
                "next_attempt_at": next_attempt_at,
                "error_code": error_code,
                "error": error[:1000],
            },
        ).first()
        if not row:
            return "unchanged"
        connection.execute(
            text(
                """
                UPDATE message_attempts
                SET finished_at = NOW(), outcome = :outcome,
                    error_code = :error_code, error_message = :error
                WHERE message_job_id = :job_id
                  AND attempt_number = (
                      SELECT attempt_count FROM message_jobs WHERE id = :job_id
                  )
                """
            ),
            {
                "job_id": job_id,
                "outcome": row.status,
                "error_code": error_code,
                "error": error[:1000],
            },
        )
        return str(row.status)


def mark_delivery_unknown(
    *,
    job_id: int,
    worker_id: str,
    error: str,
    error_code: str | None,
) -> bool:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'delivery_unknown',
                    last_error_code = :error_code,
                    last_error = :error,
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :job_id AND locked_by = :worker_id AND status = 'sending'
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "error_code": error_code,
                "error": error[:1000],
            },
        )
        if result.rowcount != 1:
            return False
        connection.execute(
            text(
                """
                UPDATE message_attempts
                SET finished_at = NOW(), outcome = 'delivery_unknown',
                    error_code = :error_code, error_message = :error
                WHERE message_job_id = :job_id
                  AND attempt_number = (
                      SELECT attempt_count FROM message_jobs WHERE id = :job_id
                  )
                """
            ),
            {
                "job_id": job_id,
                "error_code": error_code,
                "error": error[:1000],
            },
        )
        return True


def recover_stale_message_jobs() -> int:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = CASE
                        WHEN status = 'sending' THEN 'delivery_unknown'
                        WHEN attempt_count >= max_attempts THEN 'dead'
                        ELSE 'retry'
                    END,
                    scheduled_at = NOW(),
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    last_error = COALESCE(last_error, 'worker lease expired'),
                    updated_at = NOW()
                WHERE status IN ('leased', 'sending') AND lease_expires_at < NOW()
                """
            )
        )
        return int(result.rowcount or 0)


def cancel_message_job(job_id: int, worker_id: str, reason: str) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'cancelled', cancelled_at = NOW(), last_error = :reason,
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :job_id AND locked_by = :worker_id
                  AND status IN ('leased', 'sending')
                """
            ),
            {"job_id": job_id, "worker_id": worker_id, "reason": reason},
        )


def retry_dead_or_failed_job(job_id: int) -> bool:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'queued', scheduled_at = NOW(),
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    provider = NULL, provider_message_id = NULL,
                    provider_status = NULL, provider_status_rank = 0,
                    last_error_code = NULL, last_error = NULL,
                    accepted_at = NULL, sent_at = NULL, delivered_at = NULL,
                    failed_at = NULL, updated_at = NOW()
                WHERE id = :job_id
                  AND status IN ('failed', 'dead')
                """
            ),
            {"job_id": job_id},
        )
        return result.rowcount == 1


def record_provider_event(
    *,
    provider: str,
    provider_message_id: str,
    status: str,
    status_rank: int,
    error_code: str | None,
    destination: str | None,
    payload: dict[str, Any],
    message_job_id: int | None = None,
) -> dict[str, Any] | None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO provider_message_events (
                    provider, provider_message_id, status, status_rank,
                    error_code, destination_normalized, payload
                ) VALUES (
                    :provider, :message_id, :status, :status_rank,
                    :error_code, :destination, CAST(:payload AS JSONB)
                )
                """
            ),
            {
                "provider": provider,
                "message_id": provider_message_id,
                "status": status,
                "status_rank": status_rank,
                "error_code": error_code,
                "destination": destination,
                "payload": json.dumps(payload),
            },
        )
        if message_job_id is not None:
            connection.execute(
                text(
                    """
                    UPDATE message_jobs
                    SET provider = COALESCE(provider, :provider),
                        provider_message_id = COALESCE(provider_message_id, :message_id),
                        accepted_at = COALESCE(accepted_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :job_id
                      AND (provider_message_id IS NULL OR provider_message_id = :message_id)
                    """
                ),
                {
                    "provider": provider,
                    "message_id": provider_message_id,
                    "job_id": message_job_id,
                },
            )
        row = (
            connection.execute(
                text(
                    """
                UPDATE message_jobs
                SET provider_status = :status,
                    provider_status_rank = :status_rank,
                    status = CASE
                        WHEN :status = 'delivered' THEN 'delivered'
                        WHEN :status IN ('failed', 'undelivered', 'canceled') THEN 'failed'
                        WHEN :status = 'sent' THEN 'sent'
                        ELSE status
                    END,
                    sent_at = CASE
                        WHEN :status = 'sent' THEN COALESCE(sent_at, NOW())
                        ELSE sent_at
                    END,
                    delivered_at = CASE
                        WHEN :status = 'delivered' THEN COALESCE(delivered_at, NOW())
                        ELSE delivered_at
                    END,
                    failed_at = CASE
                        WHEN :status IN ('failed', 'undelivered', 'canceled')
                            THEN COALESCE(failed_at, NOW())
                        ELSE failed_at
                    END,
                    last_error_code = CASE
                        WHEN :status IN ('failed', 'undelivered') THEN :error_code
                        ELSE last_error_code
                    END,
                    updated_at = NOW()
                WHERE provider = :provider
                  AND provider_message_id = :message_id
                  AND (
                      :status_rank > provider_status_rank
                      OR (
                          :status_rank = provider_status_rank
                          AND status NOT IN ('delivered', 'failed', 'cancelled', 'dead')
                      )
                  )
                RETURNING id, message_kind, campaign_id, destination_normalized, status
                """
                ),
                {
                    "provider": provider,
                    "message_id": provider_message_id,
                    "status": status,
                    "status_rank": status_rank,
                    "error_code": error_code,
                },
            )
            .mappings()
            .first()
        )
        if row:
            if row["message_kind"] == "completed_service_sms":
                connection.execute(
                    text(
                        """
                        UPDATE processed_jobs
                        SET status = :status,
                            twilio_message_sid = :message_id,
                            last_error = :error,
                            processed_at = NOW()
                        WHERE record_id = (
                            SELECT (template_data->>'source_job_id')::BIGINT
                            FROM message_jobs WHERE id = :job_id
                        )
                        """
                    ),
                    {
                        "status": "delivery_failed"
                        if status in {"failed", "undelivered"}
                        else status,
                        "message_id": provider_message_id,
                        "error": error_code,
                        "job_id": row["id"],
                    },
                )
            elif row["message_kind"] == "water_level_low_sms":
                connection.execute(
                    text(
                        """
                        UPDATE processed_alerts
                        SET status = :status,
                            twilio_message_sid = :message_id,
                            last_error = :error,
                            processed_at = NOW()
                        WHERE alert_id = (
                            SELECT (template_data->>'alert_id')::BIGINT
                            FROM message_jobs WHERE id = :job_id
                        )
                        """
                    ),
                    {
                        "status": "delivery_failed"
                        if status in {"failed", "undelivered"}
                        else status,
                        "message_id": provider_message_id,
                        "error": error_code,
                        "job_id": row["id"],
                    },
                )
        return dict(row) if row else None


def enqueue_delivery_failure_alert(
    *,
    failed_provider_message_id: str,
    alert_destination: str,
    failed_destination_masked: str,
    provider_status: str,
    error_code: str | None,
    max_attempts: int,
) -> bool:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                INSERT INTO message_jobs (
                    idempotency_key, channel, message_kind, destination_normalized,
                    template_key, template_data, scheduled_at, max_attempts
                ) VALUES (
                    :key, 'sms', 'admin_delivery_failure_sms', :destination,
                    'admin_delivery_failure_sms', CAST(:data AS JSONB), NOW(), :max_attempts
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {
                "key": f"delivery-failure-alert:{failed_provider_message_id}",
                "destination": alert_destination,
                "data": json.dumps(
                    {
                        "message": (
                            f"SMS ALERT: Message to {failed_destination_masked} was "
                            f"{provider_status}. Twilio error: {error_code or 'none'}."
                        )
                    }
                ),
                "max_attempts": max_attempts,
            },
        )
        return result.rowcount == 1


def enqueue_website_lead_notifications(
    *,
    event_id: str,
    sms_destination: str,
    email_destination: str,
    lead: dict[str, object],
    max_attempts: int,
) -> dict[str, bool]:
    template_data = json.dumps(lead)
    with transaction() as connection:
        sms = connection.execute(
            text(
                """
                INSERT INTO message_jobs (
                    idempotency_key, channel, message_kind, destination_normalized,
                    template_key, template_data, scheduled_at, max_attempts
                ) VALUES (
                    :key, 'sms', 'admin_website_lead_sms', :destination,
                    'admin_website_lead_sms', CAST(:data AS JSONB), NOW(), :max_attempts
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {
                "key": f"website-lead:{event_id}:sms",
                "destination": sms_destination,
                "data": template_data,
                "max_attempts": max_attempts,
            },
        )
        email = connection.execute(
            text(
                """
                INSERT INTO message_jobs (
                    idempotency_key, channel, message_kind, destination_normalized,
                    template_key, template_data, scheduled_at, max_attempts
                ) VALUES (
                    :key, 'email', 'admin_website_lead_email', :destination,
                    'admin_website_lead_email', CAST(:data AS JSONB), NOW(), :max_attempts
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {
                "key": f"website-lead:{event_id}:email",
                "destination": email_destination,
                "data": template_data,
                "max_attempts": max_attempts,
            },
        )
        return {"sms": sms.rowcount == 1, "email": email.rowcount == 1}


def cancel_suppressed_queued_jobs() -> int:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE message_jobs AS job
                SET status = 'cancelled', cancelled_at = NOW(),
                    last_error = 'communication preference suppressed', updated_at = NOW()
                FROM communication_preferences AS preference
                WHERE preference.channel = job.channel
                  AND preference.destination_normalized = job.destination_normalized
                  AND preference.suppressed = TRUE
                  AND job.status IN ('queued', 'retry', 'leased')
                """
            )
        )
        return int(result.rowcount or 0)
