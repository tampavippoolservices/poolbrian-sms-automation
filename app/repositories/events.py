from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db import transaction


def store_inbound_event(
    *,
    provider: str,
    event_type: str,
    external_id: str,
    payload: dict[str, Any],
    payload_sha256: str,
    max_attempts: int,
) -> tuple[int, bool]:
    with transaction() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO inbound_events (
                    provider, event_type, external_id, payload, payload_sha256, max_attempts
                ) VALUES (
                    :provider, :event_type, :external_id,
                    CAST(:payload AS JSONB), :payload_sha256, :max_attempts
                )
                ON CONFLICT (provider, external_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": provider,
                "event_type": event_type,
                "external_id": external_id,
                "payload": json.dumps(payload),
                "payload_sha256": payload_sha256,
                "max_attempts": max_attempts,
            },
        ).first()
        if row:
            return int(row.id), True
        existing = connection.execute(
            text(
                """
                SELECT id FROM inbound_events
                WHERE provider = :provider AND external_id = :external_id
                """
            ),
            {"provider": provider, "external_id": external_id},
        ).one()
        return int(existing.id), False


def claim_inbound_events(
    *,
    worker_id: str,
    limit: int,
    lease_minutes: int,
) -> list[dict[str, Any]]:
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                WITH due AS (
                    SELECT id
                    FROM inbound_events
                    WHERE status IN ('queued', 'retry')
                      AND next_attempt_at <= NOW()
                    ORDER BY next_attempt_at, id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE inbound_events AS event
                SET status = 'leased',
                    attempt_count = attempt_count + 1,
                    locked_at = NOW(),
                    locked_by = :worker_id,
                    lease_expires_at = NOW() + (:lease_minutes * INTERVAL '1 minute'),
                    updated_at = NOW()
                FROM due
                WHERE event.id = due.id
                RETURNING event.*
                """
            ),
            {"limit": limit, "worker_id": worker_id, "lease_minutes": lease_minutes},
        ).mappings()
        return [dict(row) for row in rows]


def complete_inbound_event(event_id: int, worker_id: str) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                UPDATE inbound_events
                SET status = 'completed', completed_at = NOW(), updated_at = NOW(),
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    last_error = NULL
                WHERE id = :event_id AND locked_by = :worker_id AND status = 'leased'
                """
            ),
            {"event_id": event_id, "worker_id": worker_id},
        )


def fail_inbound_event(
    event_id: int,
    worker_id: str,
    error: str,
    next_attempt_at: datetime,
) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                UPDATE inbound_events
                SET status = CASE WHEN attempt_count >= max_attempts THEN 'dead' ELSE 'retry' END,
                    next_attempt_at = :next_attempt_at,
                    last_error = :error,
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = :event_id AND locked_by = :worker_id AND status = 'leased'
                """
            ),
            {
                "event_id": event_id,
                "worker_id": worker_id,
                "error": error[:1000],
                "next_attempt_at": next_attempt_at,
            },
        )


def recover_stale_events() -> int:
    with transaction() as connection:
        result = connection.execute(
            text(
                """
                UPDATE inbound_events
                SET status = CASE WHEN attempt_count >= max_attempts THEN 'dead' ELSE 'retry' END,
                    next_attempt_at = NOW(),
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL,
                    last_error = COALESCE(last_error, 'worker lease expired'),
                    updated_at = NOW()
                WHERE status = 'leased' AND lease_expires_at < NOW()
                """
            )
        )
        return int(result.rowcount or 0)
