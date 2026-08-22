from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.db import transaction
from app.repositories.campaigns import cancel_campaign_jobs


def confirm_campaign(
    campaign_id: int,
    reviewer_name: str,
    *,
    google_review_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with transaction() as connection:
        before = (
            connection.execute(
                text("SELECT * FROM review_campaigns WHERE id = :id FOR UPDATE"),
                {"id": campaign_id},
            )
            .mappings()
            .first()
        )
        if not before:
            return None
        after = (
            connection.execute(
                text(
                    """
                UPDATE review_campaigns
                SET status = 'confirmed',
                    confirmed_at = COALESCE(confirmed_at, NOW()),
                    google_reviewer_name = :reviewer_name,
                    google_review_id = COALESCE(:google_review_id, google_review_id),
                    updated_at = NOW()
                WHERE id = :id
                RETURNING *
                """
                ),
                {
                    "id": campaign_id,
                    "reviewer_name": reviewer_name,
                    "google_review_id": google_review_id,
                },
            )
            .mappings()
            .one()
        )
        cancel_campaign_jobs(campaign_id, "review confirmed", connection=connection)
        if google_review_id:
            connection.execute(
                text(
                    """
                    UPDATE google_reviews
                    SET campaign_id = :campaign_id, match_status = 'matched',
                        match_confidence = 1.000, updated_at = NOW()
                    WHERE google_review_id = :google_review_id
                    """
                ),
                {"campaign_id": campaign_id, "google_review_id": google_review_id},
            )
        return dict(before), dict(after)


def undo_campaign_confirmation(
    campaign_id: int,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with transaction() as connection:
        before = (
            connection.execute(
                text(
                    """
                SELECT * FROM review_campaigns
                WHERE id = :id AND confirmed_at IS NOT NULL
                FOR UPDATE
                """
                ),
                {"id": campaign_id},
            )
            .mappings()
            .first()
        )
        if not before:
            return None
        restored_status = "completed" if before["customer_replied_at"] else "active"
        after = (
            connection.execute(
                text(
                    """
                UPDATE review_campaigns
                SET status = :status, confirmed_at = NULL,
                    google_reviewer_name = NULL, google_review_id = NULL,
                    updated_at = NOW()
                WHERE id = :id
                RETURNING *
                """
                ),
                {"id": campaign_id, "status": restored_status},
            )
            .mappings()
            .one()
        )
        connection.execute(
            text(
                """
                UPDATE google_reviews
                SET campaign_id = NULL, match_status = 'unmatched',
                    match_confidence = NULL, updated_at = NOW()
                WHERE campaign_id = :campaign_id
                """
            ),
            {"campaign_id": campaign_id},
        )
        return dict(before), dict(after)


def cancel_campaign(campaign_id: int, reason: str) -> bool:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE review_campaigns
                SET status = 'cancelled', cancelled_reason = :reason, updated_at = NOW()
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": campaign_id, "reason": reason},
        )
        if result.rowcount:
            cancel_campaign_jobs(campaign_id, reason, connection=connection)
        return result.rowcount == 1
