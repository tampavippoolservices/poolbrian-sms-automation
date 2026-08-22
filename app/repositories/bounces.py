from __future__ import annotations

from sqlalchemy import text

from app.db import transaction
from app.repositories.preferences import save_preference


def known_recent_email_destinations(days: int = 30) -> set[str]:
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT destination_normalized
                FROM message_jobs
                WHERE channel = 'email'
                  AND destination_normalized IS NOT NULL
                  AND created_at >= NOW() - (:days * INTERVAL '1 day')
                """
            ),
            {"days": days},
        ).scalars()
        return {str(value).casefold() for value in rows}


def record_permanent_bounce(destination: str, graph_message_id: str) -> None:
    with transaction() as connection:
        save_preference(
            "email",
            destination,
            True,
            "permanent_bounce",
            "microsoft_graph_ndr",
            evidence={"graph_message_id": graph_message_id},
            connection=connection,
        )
        connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'failed', failed_at = COALESCE(failed_at, NOW()),
                    provider_status = 'bounced', provider_status_rank = 100,
                    last_error_code = 'permanent_bounce',
                    last_error = 'Microsoft 365 non-delivery report', updated_at = NOW()
                WHERE id = (
                    SELECT id FROM message_jobs
                    WHERE channel = 'email' AND destination_normalized = :destination
                      AND status IN ('accepted', 'sent', 'delivered')
                    ORDER BY created_at DESC LIMIT 1
                )
                """
            ),
            {"destination": destination},
        )
