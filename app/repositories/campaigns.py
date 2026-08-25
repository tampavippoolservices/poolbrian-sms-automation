from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db import transaction
from app.domain.reviews import review_message_schedule


def create_completed_service_workflow(
    *,
    source_job_id: int,
    customer_id: int,
    customer_name: str,
    phone_e164: str | None,
    email_normalized: str | None,
    now: datetime,
    timezone_name: str,
    sms_delay_hours: int,
    email_hour: int,
    suppression_days: int,
    max_attempts: int,
) -> dict[str, Any]:
    schedule = review_message_schedule(
        now,
        timezone_name=timezone_name,
        sms_delay_hours=sms_delay_hours,
        email_hour=email_hour,
    )
    with transaction() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"completed-job:{source_job_id}"},
        )
        inserted_job = connection.execute(
            text(
                """
                INSERT INTO processed_jobs (record_id, customer_id, status)
                VALUES (:source_job_id, :customer_id, 'queued')
                ON CONFLICT (record_id) DO NOTHING
                RETURNING record_id
                """
            ),
            {"source_job_id": source_job_id, "customer_id": customer_id},
        ).first()
        if not inserted_job:
            return {"created": False, "reason": "already_processed"}

        if phone_e164:
            _insert_message_job(
                connection,
                idempotency_key=f"completed-service:{source_job_id}",
                campaign_id=None,
                customer_id=customer_id,
                channel="sms",
                message_kind="completed_service_sms",
                destination=phone_e164,
                template_key="completed_service_sms",
                template_data={"customer_name": customer_name, "source_job_id": source_job_id},
                scheduled_at=now,
                max_attempts=max_attempts,
            )

        # Serialize campaign creation across every stable recipient identity.
        # Lock keys are sorted so concurrent requests acquire multiple locks in
        # a consistent order and cannot deadlock one another.
        recipient_lock_keys = {f"review-customer:{customer_id}"}
        if phone_e164:
            recipient_lock_keys.add(f"review-phone:{phone_e164}")
        if email_normalized:
            recipient_lock_keys.add(f"review-email:{email_normalized}")
        for lock_key in sorted(recipient_lock_keys):
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": lock_key},
            )
        suppressed_campaign = connection.execute(
            text(
                """
                SELECT id
                FROM review_campaigns
                WHERE (
                    customer_id = :customer_id
                    OR phone_e164 = :phone
                    OR email_normalized = :email
                )
                  AND (
                      confirmed_at IS NOT NULL
                      OR (
                          status IN ('active', 'completed', 'confirmed')
                          AND created_at >= NOW() - (:days * INTERVAL '1 day')
                      )
                  )
                LIMIT 1
                """
            ),
            {
                "customer_id": customer_id,
                "phone": phone_e164,
                "email": email_normalized,
                "days": suppression_days,
            },
        ).first()
        if suppressed_campaign:
            return {
                "created": True,
                "campaign_created": False,
                "reason": "recent_or_confirmed_campaign",
            }

        campaign = connection.execute(
            text(
                """
                INSERT INTO review_campaigns (
                    source_job_id, customer_id, customer_name, phone_e164,
                    email_normalized, review_token
                ) VALUES (
                    :source_job_id, :customer_id, :customer_name, :phone,
                    :email, :review_token
                )
                RETURNING id, review_token
                """
            ),
            {
                "source_job_id": source_job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone_e164,
                "email": email_normalized,
                "review_token": secrets.token_urlsafe(32),
            },
        ).one()
        campaign_id = int(campaign.id)
        common_data = {"customer_name": customer_name, "review_token": campaign.review_token}

        if phone_e164:
            _insert_message_job(
                connection,
                idempotency_key=f"review:{campaign_id}:initial-sms",
                campaign_id=campaign_id,
                customer_id=customer_id,
                channel="sms",
                message_kind="initial_review_sms",
                destination=phone_e164,
                template_key="initial_review_sms",
                template_data=common_data,
                scheduled_at=schedule["initial_review_sms"],
                max_attempts=max_attempts,
            )
        if email_normalized:
            for kind in ("next_day_review_email", "saturday_review_email"):
                _insert_message_job(
                    connection,
                    idempotency_key=f"review:{campaign_id}:{kind}",
                    campaign_id=campaign_id,
                    customer_id=customer_id,
                    channel="email",
                    message_kind=kind,
                    destination=email_normalized,
                    template_key=kind,
                    template_data=common_data,
                    scheduled_at=schedule[kind],
                    max_attempts=max_attempts,
                )
        return {"created": True, "campaign_created": True, "campaign_id": campaign_id}


def _insert_message_job(
    connection,
    *,
    idempotency_key: str,
    campaign_id: int | None,
    customer_id: int | None,
    channel: str,
    message_kind: str,
    destination: str,
    template_key: str,
    template_data: dict[str, Any],
    scheduled_at: datetime,
    max_attempts: int,
    inbound_event_id: int | None = None,
) -> int | None:
    row = connection.execute(
        text(
            """
            INSERT INTO message_jobs (
                idempotency_key, campaign_id, inbound_event_id, customer_id,
                channel, message_kind, destination_normalized, template_key,
                template_data, scheduled_at, max_attempts
            ) VALUES (
                :idempotency_key, :campaign_id, :inbound_event_id, :customer_id,
                :channel, :message_kind, :destination, :template_key,
                CAST(:template_data AS JSONB), :scheduled_at, :max_attempts
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "idempotency_key": idempotency_key,
            "campaign_id": campaign_id,
            "inbound_event_id": inbound_event_id,
            "customer_id": customer_id,
            "channel": channel,
            "message_kind": message_kind,
            "destination": destination,
            "template_key": template_key,
            "template_data": json.dumps(template_data),
            "scheduled_at": scheduled_at,
            "max_attempts": max_attempts,
        },
    ).first()
    return int(row.id) if row else None


def create_water_alert_job(
    *,
    alert_id: int,
    customer_id: int,
    source_job_id: int | None,
    customer_name: str,
    phone_e164: str,
    inbound_event_id: int,
    scheduled_at: datetime,
    max_attempts: int,
) -> bool:
    with transaction() as connection:
        claimed = connection.execute(
            text(
                """
                INSERT INTO processed_alerts (
                    alert_id, customer_id, job_id, alert_type, status
                ) VALUES (
                    :alert_id, :customer_id, :job_id, 'WaterLevelLow', 'queued'
                )
                ON CONFLICT (alert_id) DO NOTHING
                RETURNING alert_id
                """
            ),
            {"alert_id": alert_id, "customer_id": customer_id, "job_id": source_job_id},
        ).first()
        if not claimed:
            return False
        _insert_message_job(
            connection,
            idempotency_key=f"water-alert:{alert_id}",
            campaign_id=None,
            customer_id=customer_id,
            channel="sms",
            message_kind="water_level_low_sms",
            destination=phone_e164,
            template_key="water_level_low_sms",
            template_data={
                "customer_name": customer_name,
                "alert_id": alert_id,
                "source_job_id": source_job_id,
            },
            scheduled_at=scheduled_at,
            max_attempts=max_attempts,
            inbound_event_id=inbound_event_id,
        )
        return True


def mark_campaign_clicked(review_token: str) -> int | None:
    with transaction() as connection:
        row = connection.execute(
            text(
                """
                UPDATE review_campaigns
                SET clicked_at = COALESCE(clicked_at, NOW()), updated_at = NOW()
                WHERE review_token = :review_token
                RETURNING id
                """
            ),
            {"review_token": review_token},
        ).first()
        return int(row.id) if row else None


def cancel_campaign_jobs(campaign_id: int, reason: str, *, connection=None) -> None:
    statement = text(
        """
        UPDATE message_jobs
        SET status = 'cancelled', cancelled_at = NOW(), last_error = :reason, updated_at = NOW()
        WHERE campaign_id = :campaign_id AND status IN ('queued', 'retry', 'leased')
        """
    )
    if connection is not None:
        connection.execute(statement, {"campaign_id": campaign_id, "reason": reason})
    else:
        with transaction() as owned_connection:
            owned_connection.execute(statement, {"campaign_id": campaign_id, "reason": reason})
