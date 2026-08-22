from __future__ import annotations

import secrets

from sqlalchemy import text

from app.db import transaction
from app.repositories.preferences import save_preference


def get_or_create_unsubscribe_token(
    *,
    message_job_id: int,
    channel: str,
    destination_normalized: str,
) -> str:
    with transaction() as connection:
        existing = connection.execute(
            text(
                """
                SELECT token FROM unsubscribe_tokens WHERE message_job_id = :job_id
                """
            ),
            {"job_id": message_job_id},
        ).scalar_one_or_none()
        if existing:
            return str(existing)
        token = secrets.token_urlsafe(32)
        connection.execute(
            text(
                """
                INSERT INTO unsubscribe_tokens (
                    token, message_job_id, channel, destination_normalized
                ) VALUES (:token, :job_id, :channel, :destination)
                """
            ),
            {
                "token": token,
                "job_id": message_job_id,
                "channel": channel,
                "destination": destination_normalized,
            },
        )
        return token


def consume_unsubscribe_token(token: str) -> dict[str, str] | None:
    with transaction() as connection:
        row = (
            connection.execute(
                text(
                    """
                UPDATE unsubscribe_tokens
                SET used_at = COALESCE(used_at, NOW())
                WHERE token = :token
                RETURNING channel, destination_normalized
                """
                ),
                {"token": token},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        save_preference(
            str(row["channel"]),
            str(row["destination_normalized"]),
            True,
            "customer_unsubscribe",
            "unsubscribe_link",
            evidence={"token_used": True},
            connection=connection,
        )
        return {
            "channel": str(row["channel"]),
            "destination": str(row["destination_normalized"]),
        }
