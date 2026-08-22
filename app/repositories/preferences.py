from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db import transaction


def is_suppressed(channel: str, destination_normalized: str) -> bool:
    with transaction() as connection:
        value = connection.execute(
            text(
                """
                SELECT suppressed
                FROM communication_preferences
                WHERE channel = :channel
                  AND destination_normalized = :destination
                """
            ),
            {"channel": channel, "destination": destination_normalized},
        ).scalar_one_or_none()
    return bool(value)


def save_preference(
    channel: str,
    destination_normalized: str,
    suppressed: bool,
    reason: str,
    source: str,
    *,
    evidence: dict[str, Any] | None = None,
    connection: Connection | None = None,
) -> None:
    def write(active_connection: Connection) -> None:
        active_connection.execute(
            text(
                """
                INSERT INTO communication_preferences (
                    channel, destination_normalized, suppressed, reason, source
                ) VALUES (
                    :channel, :destination, :suppressed, :reason, :source
                )
                ON CONFLICT (channel, destination_normalized)
                DO UPDATE SET
                    suppressed = EXCLUDED.suppressed,
                    reason = EXCLUDED.reason,
                    source = EXCLUDED.source,
                    changed_at = NOW()
                """
            ),
            {
                "channel": channel,
                "destination": destination_normalized,
                "suppressed": suppressed,
                "reason": reason,
                "source": source,
            },
        )
        active_connection.execute(
            text(
                """
                INSERT INTO communication_preference_events (
                    channel, destination_normalized, suppressed, reason, source, evidence
                ) VALUES (
                    :channel, :destination, :suppressed, :reason, :source,
                    CAST(:evidence AS JSONB)
                )
                """
            ),
            {
                "channel": channel,
                "destination": destination_normalized,
                "suppressed": suppressed,
                "reason": reason,
                "source": source,
                "evidence": json.dumps(evidence or {}),
            },
        )
        if suppressed:
            active_connection.execute(
                text(
                    """
                    UPDATE message_jobs
                    SET status = 'cancelled',
                        cancelled_at = NOW(),
                        last_error = :reason,
                        updated_at = NOW()
                    WHERE channel = :channel
                      AND destination_normalized = :destination
                      AND status IN ('queued', 'retry', 'leased')
                    """
                ),
                {
                    "channel": channel,
                    "destination": destination_normalized,
                    "reason": reason,
                },
            )

    if connection is not None:
        write(connection)
    else:
        with transaction() as owned_connection:
            write(owned_connection)
