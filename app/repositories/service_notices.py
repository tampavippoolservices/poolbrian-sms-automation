from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db import transaction

MAX_SERVICE_NOTICE_RECIPIENTS = 100
SERVICE_NOTICE_CANDIDATE_PAGE_SIZE = 50


def service_notice_candidates(search: str = "", limit: int = 100) -> list[dict[str, Any]]:
    normalized_search = search.strip()[:100]
    with transaction() as connection:
        return _candidate_rows(
            connection,
            search=normalized_search,
            limit=min(max(limit, 1), MAX_SERVICE_NOTICE_RECIPIENTS),
            offset=0,
        )


def service_notice_candidate_page(
    search: str = "",
    *,
    page: int = 1,
    page_size: int = SERVICE_NOTICE_CANDIDATE_PAGE_SIZE,
) -> dict[str, Any]:
    """Return one searchable page without weakening the per-campaign send limit."""
    normalized_search = search.strip()[:100]
    safe_page_size = min(max(page_size, 1), 100)
    with transaction() as connection:
        total = _candidate_count(connection, search=normalized_search)
        pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        current_page = min(max(page, 1), pages)
        items = _candidate_rows(
            connection,
            search=normalized_search,
            limit=safe_page_size,
            offset=(current_page - 1) * safe_page_size,
        )
    return {
        "items": items,
        "page": current_page,
        "pages": pages,
        "page_size": safe_page_size,
        "total": total,
        "first_item": (current_page - 1) * safe_page_size + 1 if total else 0,
        "last_item": min(current_page * safe_page_size, total),
        "search": normalized_search,
    }


def eligible_service_notice_recipients(customer_ids: list[int]) -> list[dict[str, Any]]:
    selected = _normalized_customer_ids(customer_ids)
    if not selected:
        return []
    with transaction() as connection:
        return _recipient_rows(connection, selected)


def enqueue_service_notice_campaign(
    *,
    name: str,
    message_body: str,
    customer_ids: list[int],
    consent_basis: str,
    consent_evidence: str,
    scheduled_at: datetime,
    created_by: str,
    max_attempts: int,
) -> dict[str, Any]:
    selected = _normalized_customer_ids(customer_ids)
    if not selected:
        raise ValueError("Select at least one customer")
    with transaction() as connection:
        recipients = _recipient_rows(connection, selected)
        if not recipients:
            raise ValueError("No selected customers are currently eligible")
        campaign_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO service_notice_campaigns (
                        name, message_body, consent_basis, consent_evidence,
                        recipient_count, scheduled_at, created_by
                    ) VALUES (
                        :name, :message_body, :consent_basis, :consent_evidence,
                        :recipient_count, :scheduled_at, :created_by
                    )
                    RETURNING id
                    """
                ),
                {
                    "name": name,
                    "message_body": message_body,
                    "consent_basis": consent_basis,
                    "consent_evidence": consent_evidence,
                    "recipient_count": len(recipients),
                    "scheduled_at": scheduled_at,
                    "created_by": created_by,
                },
            ).scalar_one()
        )
        for recipient in recipients:
            job_id = int(
                connection.execute(
                    text(
                        """
                        INSERT INTO message_jobs (
                            idempotency_key, customer_id, channel, message_kind,
                            destination_normalized, template_key, template_data,
                            scheduled_at, max_attempts
                        ) VALUES (
                            :key, :customer_id, 'sms', 'service_notice_sms',
                            :destination, 'service_notice_sms', CAST(:data AS JSONB),
                            :scheduled_at, :max_attempts
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "key": f"service-notice:{campaign_id}:{recipient['phone_e164']}",
                        "customer_id": recipient["customer_id"],
                        "destination": recipient["phone_e164"],
                        "data": json.dumps(
                            {
                                "service_notice_campaign_id": campaign_id,
                                "customer_name": recipient["customer_name"],
                                "message_body": message_body,
                            }
                        ),
                        "scheduled_at": scheduled_at,
                        "max_attempts": max_attempts,
                    },
                ).scalar_one()
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_notice_recipients (
                        campaign_id, customer_id, customer_name, phone_e164, message_job_id
                    ) VALUES (
                        :campaign_id, :customer_id, :customer_name, :phone_e164, :message_job_id
                    )
                    """
                ),
                {
                    "campaign_id": campaign_id,
                    "customer_id": recipient["customer_id"],
                    "customer_name": recipient["customer_name"],
                    "phone_e164": recipient["phone_e164"],
                    "message_job_id": job_id,
                },
            )
    return {
        "campaign_id": campaign_id,
        "recipient_count": len(recipients),
        "scheduled_at": scheduled_at,
    }


def recent_service_notice_campaigns(limit: int = 25) -> list[dict[str, Any]]:
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                SELECT campaign.id, campaign.name, campaign.status,
                       campaign.recipient_count, campaign.scheduled_at,
                       campaign.created_by, campaign.created_at,
                       COUNT(*) FILTER (WHERE job.status IN ('queued', 'retry', 'leased'))
                           AS pending_count,
                       COUNT(*) FILTER (WHERE job.status IN ('accepted', 'sent', 'delivered'))
                           AS accepted_count,
                       COUNT(*) FILTER (
                           WHERE job.status IN ('failed', 'dead', 'delivery_unknown')
                       ) AS failed_count,
                       COUNT(*) FILTER (WHERE job.status = 'cancelled') AS cancelled_count
                FROM service_notice_campaigns AS campaign
                JOIN service_notice_recipients AS recipient
                  ON recipient.campaign_id = campaign.id
                JOIN message_jobs AS job ON job.id = recipient.message_job_id
                GROUP BY campaign.id
                ORDER BY campaign.created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": min(max(limit, 1), 100)},
        ).mappings()
        campaigns = [dict(row) for row in rows]
        for campaign in campaigns:
            if campaign["status"] == "cancelled":
                campaign["display_status"] = "cancelled"
            elif campaign["failed_count"]:
                campaign["display_status"] = "attention"
            elif campaign["pending_count"]:
                campaign["display_status"] = "queued"
            else:
                campaign["display_status"] = "completed"
        return campaigns


def cancel_service_notice_campaign(campaign_id: int) -> dict[str, int] | None:
    with transaction() as connection:
        campaign = connection.execute(
            text(
                """
                UPDATE service_notice_campaigns
                SET status = 'cancelled', cancelled_at = NOW()
                WHERE id = :campaign_id AND status = 'queued'
                RETURNING id
                """
            ),
            {"campaign_id": campaign_id},
        ).first()
        if not campaign:
            return None
        cancelled = connection.execute(
            text(
                """
                UPDATE message_jobs AS job
                SET status = 'cancelled', cancelled_at = NOW(),
                    last_error = 'service notice campaign cancelled', updated_at = NOW()
                FROM service_notice_recipients AS recipient
                WHERE recipient.campaign_id = :campaign_id
                  AND job.id = recipient.message_job_id
                  AND job.status IN ('queued', 'retry')
                """
            ),
            {"campaign_id": campaign_id},
        ).rowcount
        return {"cancelled_jobs": int(cancelled or 0)}


def _candidate_rows(
    connection: Connection,
    *,
    search: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (customer_id)
                       customer_id, customer_name, phone_e164, created_at
                FROM review_campaigns
                WHERE phone_e164 IS NOT NULL
                ORDER BY customer_id, created_at DESC, id DESC
            )
            SELECT latest.customer_id, latest.customer_name, latest.phone_e164,
                   preference.source AS preference_source,
                   preference.reason AS preference_reason
            FROM latest
            LEFT JOIN communication_preferences AS preference
              ON preference.channel = 'sms'
             AND preference.destination_normalized = latest.phone_e164
            WHERE COALESCE(preference.suppressed, FALSE) = FALSE
              AND (
                  :search = ''
                  OR COALESCE(latest.customer_name, '') ILIKE :pattern
                  OR latest.phone_e164 ILIKE :pattern
                  OR CAST(latest.customer_id AS TEXT) ILIKE :pattern
              )
            ORDER BY latest.customer_name NULLS LAST, latest.customer_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "search": search,
            "pattern": f"%{search}%",
            "limit": limit,
            "offset": offset,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _candidate_count(connection: Connection, *, search: str) -> int:
    return int(
        connection.execute(
            text(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (customer_id)
                           customer_id, customer_name, phone_e164, created_at
                    FROM review_campaigns
                    WHERE phone_e164 IS NOT NULL
                    ORDER BY customer_id, created_at DESC, id DESC
                )
                SELECT COUNT(*)
                FROM latest
                LEFT JOIN communication_preferences AS preference
                  ON preference.channel = 'sms'
                 AND preference.destination_normalized = latest.phone_e164
                WHERE COALESCE(preference.suppressed, FALSE) = FALSE
                  AND (
                      :search = ''
                      OR COALESCE(latest.customer_name, '') ILIKE :pattern
                      OR latest.phone_e164 ILIKE :pattern
                      OR CAST(latest.customer_id AS TEXT) ILIKE :pattern
                  )
                """
            ),
            {"search": search, "pattern": f"%{search}%"},
        ).scalar_one()
    )


def _recipient_rows(connection: Connection, customer_ids: list[int]) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (customer_id)
                       customer_id, customer_name, phone_e164, created_at
                FROM review_campaigns
                WHERE customer_id = ANY(CAST(:customer_ids AS BIGINT[]))
                  AND phone_e164 IS NOT NULL
                ORDER BY customer_id, created_at DESC, id DESC
            )
            SELECT latest.customer_id,
                   COALESCE(NULLIF(latest.customer_name, ''), 'Customer') AS customer_name,
                   latest.phone_e164
            FROM latest
            LEFT JOIN communication_preferences AS preference
              ON preference.channel = 'sms'
             AND preference.destination_normalized = latest.phone_e164
            WHERE COALESCE(preference.suppressed, FALSE) = FALSE
            ORDER BY latest.customer_id
            """
        ),
        {"customer_ids": customer_ids},
    ).mappings()
    unique: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    for row in rows:
        phone = str(row["phone_e164"])
        if phone in seen_phones:
            continue
        seen_phones.add(phone)
        unique.append(dict(row))
    return unique[:MAX_SERVICE_NOTICE_RECIPIENTS]


def _normalized_customer_ids(customer_ids: list[int]) -> list[int]:
    return sorted({value for value in customer_ids if value > 0})[:MAX_SERVICE_NOTICE_RECIPIENTS]
