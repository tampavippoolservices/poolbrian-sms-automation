from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.crypto import encrypt_secret
from app.db import transaction
from app.repositories.campaigns import cancel_campaign_jobs
from app.repositories.preferences import save_preference


def record_inbound_sms(
    *,
    provider_message_id: str,
    from_normalized: str,
    to_normalized: str | None,
    message_body: str,
    customer_id: int | None,
    opt_out: bool,
    opt_in: bool,
    company_phone: str | None,
    customer_name: str,
    max_attempts: int,
) -> dict[str, Any]:
    preview = "opt_out" if opt_out else "opt_in" if opt_in else "customer_reply"
    with transaction() as connection:
        inserted = connection.execute(
            text(
                """
                INSERT INTO inbound_messages (
                    provider, provider_message_id, from_normalized, to_normalized,
                    body_ciphertext, body_preview, customer_id
                ) VALUES (
                    'twilio', :message_id, :from_number, :to_number,
                    :body_ciphertext, :preview, :customer_id
                )
                ON CONFLICT (provider, provider_message_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "message_id": provider_message_id,
                "from_number": from_normalized,
                "to_number": to_normalized,
                "body_ciphertext": encrypt_secret(message_body),
                "preview": preview,
                "customer_id": customer_id,
            },
        ).first()
        if not inserted:
            return {"created": False, "reason": "duplicate"}

        if opt_out or opt_in:
            save_preference(
                "sms",
                from_normalized,
                opt_out,
                "customer_opt_out" if opt_out else "customer_opt_in",
                "twilio_inbound",
                evidence={"message_sid": provider_message_id},
                connection=connection,
            )

        campaign = (
            connection.execute(
                text(
                    """
                SELECT id, status, customer_name, customer_id
                FROM review_campaigns
                WHERE phone_e164 = :phone AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """
                ),
                {"phone": from_normalized},
            )
            .mappings()
            .first()
        )
        campaign_id: int | None = None
        if campaign:
            campaign_id = int(campaign["id"])
            customer_id = customer_id or campaign["customer_id"]
            customer_name = str(campaign["customer_name"] or customer_name)
            connection.execute(
                text(
                    """
                    UPDATE review_campaigns
                    SET customer_replied_at = COALESCE(customer_replied_at, NOW()),
                        status = :status,
                        cancelled_reason = CASE
                            WHEN :opt_out THEN 'sms_opt_out'
                            ELSE cancelled_reason
                        END,
                        updated_at = NOW()
                    WHERE id = :campaign_id
                    """
                ),
                {
                    "campaign_id": campaign_id,
                    "status": "cancelled" if opt_out else "completed",
                    "opt_out": opt_out,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE inbound_messages
                    SET campaign_id = :campaign_id, customer_id = :customer_id
                    WHERE id = :id
                    """
                ),
                {"campaign_id": campaign_id, "customer_id": customer_id, "id": inserted.id},
            )
            cancel_campaign_jobs(
                campaign_id,
                "customer replied" if not opt_out else "sms opt-out",
                connection=connection,
            )

        if company_phone and not opt_out and not opt_in:
            connection.execute(
                text(
                    """
                    INSERT INTO message_jobs (
                        idempotency_key, campaign_id, customer_id, channel, message_kind,
                        destination_normalized, template_key, template_data,
                        scheduled_at, max_attempts
                    ) VALUES (
                        :key, NULL, :customer_id, 'sms', 'admin_customer_reply_sms',
                        :destination, 'admin_customer_reply_sms', CAST(:template_data AS JSONB),
                        NOW(), :max_attempts
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """
                ),
                {
                    "key": f"forward-inbound:{provider_message_id}",
                    "customer_id": customer_id,
                    "destination": company_phone,
                    "template_data": json.dumps(
                        {
                            "customer_name": customer_name,
                            "customer_phone": from_normalized,
                            "message_body": message_body,
                        }
                    ),
                    "max_attempts": max_attempts,
                },
            )
        return {"created": True, "campaign_id": campaign_id, "inbound_id": int(inserted.id)}
