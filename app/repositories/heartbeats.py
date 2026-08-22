from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.db import transaction


def heartbeat_started(worker_name: str, metadata: dict[str, Any] | None = None) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO worker_heartbeats (worker_name, last_started_at, metadata)
                VALUES (:name, NOW(), CAST(:metadata AS JSONB))
                ON CONFLICT (worker_name)
                DO UPDATE SET
                    last_started_at = NOW(),
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {"name": worker_name, "metadata": json.dumps(metadata or {})},
        )


def heartbeat_succeeded(worker_name: str, metadata: dict[str, Any] | None = None) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO worker_heartbeats (
                    worker_name, last_started_at, last_succeeded_at, metadata
                ) VALUES (:name, NOW(), NOW(), CAST(:metadata AS JSONB))
                ON CONFLICT (worker_name)
                DO UPDATE SET last_succeeded_at = NOW(), last_error = NULL,
                    metadata = EXCLUDED.metadata, updated_at = NOW()
                """
            ),
            {"name": worker_name, "metadata": json.dumps(metadata or {})},
        )


def heartbeat_failed(worker_name: str, error: str) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO worker_heartbeats (
                    worker_name, last_started_at, last_failed_at, last_error
                ) VALUES (:name, NOW(), NOW(), :error)
                ON CONFLICT (worker_name)
                DO UPDATE SET last_failed_at = NOW(), last_error = EXCLUDED.last_error,
                    updated_at = NOW()
                """
            ),
            {"name": worker_name, "error": error[:1000]},
        )
