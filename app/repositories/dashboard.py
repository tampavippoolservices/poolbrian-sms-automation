from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db import transaction


def dashboard_snapshot(timezone_name: str) -> dict[str, Any]:
    with transaction() as connection:
        metrics = (
            connection.execute(
                text(
                    """
                SELECT
                    COUNT(*) FILTER (
                        WHERE (created_at AT TIME ZONE :timezone_name)::date =
                              (NOW() AT TIME ZONE :timezone_name)::date
                    ) AS messages_today,
                    COUNT(*) FILTER (
                        WHERE status = 'delivered'
                          AND (delivered_at AT TIME ZONE :timezone_name)::date =
                              (NOW() AT TIME ZONE :timezone_name)::date
                    ) AS delivered_today,
                    COUNT(*) FILTER (
                        WHERE status IN ('failed', 'dead')
                          AND (failed_at AT TIME ZONE :timezone_name)::date =
                              (NOW() AT TIME ZONE :timezone_name)::date
                    ) AS failed_today,
                    COUNT(*) FILTER (WHERE status IN ('queued', 'retry')) AS queued,
                    COUNT(*) FILTER (
                        WHERE status IN ('queued', 'retry') AND scheduled_at <= NOW()
                    ) AS due,
                    COUNT(*) FILTER (WHERE status = 'delivery_unknown') AS delivery_unknown,
                    COALESCE(
                        EXTRACT(EPOCH FROM (
                            NOW() - MIN(scheduled_at) FILTER (
                                WHERE status IN ('queued', 'retry')
                                  AND scheduled_at <= NOW()
                            )
                        )) / 60,
                        0
                    ) AS oldest_due_minutes
                FROM message_jobs
                """
                ),
                {"timezone_name": timezone_name},
            )
            .mappings()
            .one()
        )
        campaign_metrics = (
            connection.execute(
                text(
                    """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'active') AS active,
                    COUNT(*) FILTER (WHERE clicked_at IS NOT NULL) AS clicked,
                    COUNT(*) FILTER (WHERE status = 'confirmed') AS confirmed,
                    COUNT(*) FILTER (WHERE status = 'completed') AS replied,
                    COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled
                FROM review_campaigns
                """
                )
            )
            .mappings()
            .one()
        )
        communication = (
            connection.execute(
                text(
                    """
                SELECT
                    COUNT(*) FILTER (WHERE channel = 'sms' AND suppressed) AS sms_suppressed,
                    COUNT(*) FILTER (WHERE channel = 'email' AND suppressed) AS email_suppressed
                FROM communication_preferences
                """
                )
            )
            .mappings()
            .one()
        )
        recent_jobs = (
            connection.execute(
                text(
                    """
                SELECT id, channel, message_kind, destination_normalized, status,
                       provider_status, last_error_code, attempt_count, scheduled_at,
                       updated_at
                FROM message_jobs
                ORDER BY updated_at DESC
                LIMIT 50
                """
                )
            )
            .mappings()
            .all()
        )
        campaigns = (
            connection.execute(
                text(
                    """
                SELECT id, customer_name, phone_e164, email_normalized, status,
                       created_at, clicked_at, customer_replied_at, confirmed_at,
                       google_reviewer_name, cancelled_reason
                FROM review_campaigns
                ORDER BY created_at DESC
                LIMIT 50
                """
                )
            )
            .mappings()
            .all()
        )
        reviews = (
            connection.execute(
                text(
                    """
                SELECT google_review_id, reviewer_name, star_rating, review_created_at,
                       match_status, match_confidence, campaign_id
                FROM google_reviews
                WHERE match_status IN ('unmatched', 'candidate')
                ORDER BY review_created_at DESC NULLS LAST
                LIMIT 50
                """
                )
            )
            .mappings()
            .all()
        )
        heartbeats = (
            connection.execute(
                text(
                    """
                SELECT worker_name, last_started_at, last_succeeded_at,
                       last_failed_at, last_error, updated_at
                FROM worker_heartbeats ORDER BY worker_name
                """
                )
            )
            .mappings()
            .all()
        )
    return {
        "metrics": dict(metrics),
        "campaign_metrics": dict(campaign_metrics),
        "communication": dict(communication),
        "recent_jobs": [dict(row) for row in recent_jobs],
        "campaigns": [dict(row) for row in campaigns],
        "reviews": [dict(row) for row in reviews],
        "heartbeats": [dict(row) for row in heartbeats],
    }
