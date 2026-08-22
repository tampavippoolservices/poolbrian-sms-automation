from __future__ import annotations

from sqlalchemy import text

from app.db import transaction


def campaigns_missing_contacts(limit: int) -> list[dict]:
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (customer_id)
                       customer_id, customer_name, phone_e164, email_normalized
                FROM review_campaigns
                WHERE customer_name IS NULL OR BTRIM(customer_name) = ''
                   OR phone_e164 IS NULL OR email_normalized IS NULL
                ORDER BY customer_id, created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings()
        return [dict(row) for row in rows]


def update_campaign_contacts(
    customer_id: int,
    *,
    customer_name: str,
    phone_e164: str | None,
    email_normalized: str | None,
) -> int:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE review_campaigns
                SET customer_name = CASE
                        WHEN customer_name IS NULL OR BTRIM(customer_name) = ''
                            THEN :customer_name ELSE customer_name END,
                    phone_e164 = COALESCE(phone_e164, :phone),
                    email_normalized = COALESCE(email_normalized, :email),
                    updated_at = NOW()
                WHERE customer_id = :customer_id
                """
            ),
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone_e164,
                "email": email_normalized,
            },
        )
        return int(result.rowcount or 0)
