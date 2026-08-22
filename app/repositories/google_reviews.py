from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db import transaction
from app.domain.reviews import name_match_score
from app.repositories.campaigns import cancel_campaign_jobs

STAR_RATINGS = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
}


def import_google_review(review: dict[str, Any]) -> dict[str, Any]:
    review_id = str(review.get("reviewId") or "").strip()
    if not review_id:
        raise ValueError("Google review is missing reviewId")
    reviewer = review.get("reviewer") or {}
    reviewer_name = str(reviewer.get("displayName") or "").strip() or None
    star_value = review.get("starRating")
    star_rating = STAR_RATINGS.get(str(star_value))
    created_at = _parse_datetime(review.get("createTime"))
    updated_at = _parse_datetime(review.get("updateTime"))

    with transaction() as connection:
        existing = (
            connection.execute(
                text(
                    """
                SELECT campaign_id, match_status FROM google_reviews
                WHERE google_review_id = :review_id
                """
                ),
                {"review_id": review_id},
            )
            .mappings()
            .first()
        )
        connection.execute(
            text(
                """
                INSERT INTO google_reviews (
                    google_review_id, reviewer_name, star_rating, review_comment,
                    review_created_at, review_updated_at, raw_payload
                ) VALUES (
                    :review_id, :reviewer_name, :star_rating, :comment,
                    :created_at, :updated_at, CAST(:payload AS JSONB)
                )
                ON CONFLICT (google_review_id)
                DO UPDATE SET
                    reviewer_name = EXCLUDED.reviewer_name,
                    star_rating = EXCLUDED.star_rating,
                    review_comment = EXCLUDED.review_comment,
                    review_created_at = EXCLUDED.review_created_at,
                    review_updated_at = EXCLUDED.review_updated_at,
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = NOW()
                """
            ),
            {
                "review_id": review_id,
                "reviewer_name": reviewer_name,
                "star_rating": star_rating,
                "comment": review.get("comment"),
                "created_at": created_at,
                "updated_at": updated_at,
                "payload": json.dumps(review),
            },
        )
        if existing and existing["match_status"] == "matched":
            return {"review_id": review_id, "match_status": "matched", "existing": True}
        if not reviewer_name or not created_at:
            return {"review_id": review_id, "match_status": "unmatched"}

        candidates = (
            connection.execute(
                text(
                    """
                SELECT id, customer_name, created_at
                FROM review_campaigns
                WHERE status IN ('active', 'completed')
                  AND created_at BETWEEN :window_start AND :window_end
                ORDER BY created_at DESC
                LIMIT 100
                """
                ),
                {
                    "window_start": created_at - timedelta(days=45),
                    "window_end": created_at + timedelta(days=1),
                },
            )
            .mappings()
            .all()
        )
        scored = [
            (name_match_score(candidate["customer_name"], reviewer_name), candidate)
            for candidate in candidates
        ]
        strong = [(score, candidate) for score, candidate in scored if score == 1.0]
        if len(strong) == 1:
            campaign_id = int(strong[0][1]["id"])
            connection.execute(
                text(
                    """
                    UPDATE google_reviews
                    SET campaign_id = :campaign_id, match_status = 'matched',
                        match_confidence = 0.950, updated_at = NOW()
                    WHERE google_review_id = :review_id
                    """
                ),
                {"campaign_id": campaign_id, "review_id": review_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE review_campaigns
                    SET status = 'confirmed', confirmed_at = COALESCE(confirmed_at, NOW()),
                        google_review_id = :review_id,
                        google_reviewer_name = :reviewer_name,
                        updated_at = NOW()
                    WHERE id = :campaign_id
                    """
                ),
                {
                    "campaign_id": campaign_id,
                    "review_id": review_id,
                    "reviewer_name": reviewer_name,
                },
            )
            cancel_campaign_jobs(campaign_id, "matched Google review", connection=connection)
            return {"review_id": review_id, "match_status": "matched", "campaign_id": campaign_id}

        candidate_scores = [(score, candidate) for score, candidate in scored if score >= 0.5]
        if candidate_scores:
            best_score, best_candidate = max(candidate_scores, key=lambda item: item[0])
            connection.execute(
                text(
                    """
                    UPDATE google_reviews
                    SET campaign_id = :campaign_id, match_status = 'candidate',
                        match_confidence = :confidence, updated_at = NOW()
                    WHERE google_review_id = :review_id
                    """
                ),
                {
                    "campaign_id": int(best_candidate["id"]),
                    "confidence": round(best_score, 3),
                    "review_id": review_id,
                },
            )
            return {
                "review_id": review_id,
                "match_status": "candidate",
                "campaign_id": int(best_candidate["id"]),
            }
        return {"review_id": review_id, "match_status": "unmatched"}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
