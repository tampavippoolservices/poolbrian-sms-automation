from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db import transaction

TABLE_PAGE_SIZE = 25


def dashboard_snapshot(
    timezone_name: str,
    *,
    jobs_page: int = 1,
    jobs_search: str = "",
    campaigns_page: int = 1,
    campaigns_search: str = "",
    reviews_page: int = 1,
    reviews_search: str = "",
    workers_page: int = 1,
    workers_search: str = "",
    page_size: int = TABLE_PAGE_SIZE,
) -> dict[str, Any]:
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
        recent_jobs, jobs_pagination = _paginated_rows(
            connection,
            count_sql="""
                SELECT COUNT(*)
                FROM message_jobs
                WHERE (
                    :search = ''
                    OR CAST(id AS TEXT) ILIKE :pattern
                    OR channel ILIKE :pattern
                    OR message_kind ILIKE :pattern
                    OR destination_normalized ILIKE :pattern
                    OR status ILIKE :pattern
                    OR COALESCE(provider_status, '') ILIKE :pattern
                    OR COALESCE(last_error_code, '') ILIKE :pattern
                )
            """,
            rows_sql="""
                SELECT id, channel, message_kind, destination_normalized, status,
                       provider_status, last_error_code, attempt_count, scheduled_at,
                       updated_at
                FROM message_jobs
                WHERE (
                    :search = ''
                    OR CAST(id AS TEXT) ILIKE :pattern
                    OR channel ILIKE :pattern
                    OR message_kind ILIKE :pattern
                    OR destination_normalized ILIKE :pattern
                    OR status ILIKE :pattern
                    OR COALESCE(provider_status, '') ILIKE :pattern
                    OR COALESCE(last_error_code, '') ILIKE :pattern
                )
                ORDER BY updated_at DESC, id DESC
                LIMIT :limit OFFSET :offset
            """,
            search=jobs_search,
            page=jobs_page,
            page_size=page_size,
        )
        campaigns, campaigns_pagination = _paginated_rows(
            connection,
            count_sql="""
                SELECT COUNT(*)
                FROM review_campaigns
                WHERE (
                    :search = ''
                    OR CAST(id AS TEXT) ILIKE :pattern
                    OR COALESCE(customer_name, '') ILIKE :pattern
                    OR COALESCE(phone_e164, '') ILIKE :pattern
                    OR COALESCE(email_normalized, '') ILIKE :pattern
                    OR status ILIKE :pattern
                    OR COALESCE(google_reviewer_name, '') ILIKE :pattern
                    OR COALESCE(cancelled_reason, '') ILIKE :pattern
                )
            """,
            rows_sql="""
                SELECT id, customer_name, phone_e164, email_normalized, status,
                       created_at, clicked_at, customer_replied_at, confirmed_at,
                       google_reviewer_name, cancelled_reason
                FROM review_campaigns
                WHERE (
                    :search = ''
                    OR CAST(id AS TEXT) ILIKE :pattern
                    OR COALESCE(customer_name, '') ILIKE :pattern
                    OR COALESCE(phone_e164, '') ILIKE :pattern
                    OR COALESCE(email_normalized, '') ILIKE :pattern
                    OR status ILIKE :pattern
                    OR COALESCE(google_reviewer_name, '') ILIKE :pattern
                    OR COALESCE(cancelled_reason, '') ILIKE :pattern
                )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
            """,
            search=campaigns_search,
            page=campaigns_page,
            page_size=page_size,
        )
        reviews, reviews_pagination = _paginated_rows(
            connection,
            count_sql="""
                SELECT COUNT(*)
                FROM google_reviews
                WHERE match_status IN ('unmatched', 'candidate')
                  AND (
                      :search = ''
                      OR google_review_id ILIKE :pattern
                      OR COALESCE(reviewer_name, '') ILIKE :pattern
                      OR COALESCE(CAST(star_rating AS TEXT), '') ILIKE :pattern
                      OR match_status ILIKE :pattern
                      OR COALESCE(CAST(campaign_id AS TEXT), '') ILIKE :pattern
                  )
            """,
            rows_sql="""
                SELECT google_review_id, reviewer_name, star_rating, review_created_at,
                       match_status, match_confidence, campaign_id
                FROM google_reviews
                WHERE match_status IN ('unmatched', 'candidate')
                  AND (
                      :search = ''
                      OR google_review_id ILIKE :pattern
                      OR COALESCE(reviewer_name, '') ILIKE :pattern
                      OR COALESCE(CAST(star_rating AS TEXT), '') ILIKE :pattern
                      OR match_status ILIKE :pattern
                      OR COALESCE(CAST(campaign_id AS TEXT), '') ILIKE :pattern
                  )
                ORDER BY review_created_at DESC NULLS LAST, google_review_id DESC
                LIMIT :limit OFFSET :offset
            """,
            search=reviews_search,
            page=reviews_page,
            page_size=page_size,
        )
        heartbeats, workers_pagination = _paginated_rows(
            connection,
            count_sql="""
                SELECT COUNT(*)
                FROM worker_heartbeats
                WHERE (
                    :search = ''
                    OR worker_name ILIKE :pattern
                    OR COALESCE(last_error, '') ILIKE :pattern
                )
            """,
            rows_sql="""
                SELECT worker_name, last_started_at, last_succeeded_at,
                       last_failed_at, last_error, updated_at
                FROM worker_heartbeats
                WHERE (
                    :search = ''
                    OR worker_name ILIKE :pattern
                    OR COALESCE(last_error, '') ILIKE :pattern
                )
                ORDER BY worker_name
                LIMIT :limit OFFSET :offset
            """,
            search=workers_search,
            page=workers_page,
            page_size=page_size,
        )
    return {
        "metrics": dict(metrics),
        "campaign_metrics": dict(campaign_metrics),
        "communication": dict(communication),
        "recent_jobs": [dict(row) for row in recent_jobs],
        "campaigns": [dict(row) for row in campaigns],
        "reviews": [dict(row) for row in reviews],
        "heartbeats": [dict(row) for row in heartbeats],
        "pagination": {
            "jobs": jobs_pagination,
            "campaigns": campaigns_pagination,
            "reviews": reviews_pagination,
            "workers": workers_pagination,
        },
    }


def _paginated_rows(
    connection,
    *,
    count_sql: str,
    rows_sql: str,
    search: str,
    page: int,
    page_size: int,
) -> tuple[list[Any], dict[str, int | str]]:
    normalized_search = search.strip()[:100]
    safe_page_size = min(max(page_size, 1), 100)
    params: dict[str, Any] = {
        "search": normalized_search,
        "pattern": f"%{normalized_search}%",
    }
    total = int(connection.execute(text(count_sql), params).scalar_one())
    pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    current_page = min(max(page, 1), pages)
    rows = (
        connection.execute(
            text(rows_sql),
            {
                **params,
                "limit": safe_page_size,
                "offset": (current_page - 1) * safe_page_size,
            },
        )
        .mappings()
        .all()
    )
    first_item = (current_page - 1) * safe_page_size + 1 if total else 0
    last_item = min(current_page * safe_page_size, total)
    return list(rows), {
        "page": current_page,
        "pages": pages,
        "page_size": safe_page_size,
        "total": total,
        "first_item": first_item,
        "last_item": last_item,
        "search": normalized_search,
    }
