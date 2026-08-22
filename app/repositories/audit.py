from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.db import transaction


def record_audit_event(
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
) -> None:
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO audit_events (
                    actor, action, entity_type, entity_id,
                    before_data, after_data, request_id, ip_address
                ) VALUES (
                    :actor, :action, :entity_type, :entity_id,
                    CAST(:before AS JSONB), CAST(:after AS JSONB),
                    :request_id, CAST(:ip_address AS INET)
                )
                """
            ),
            {
                "actor": actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before": json.dumps(before, default=str) if before is not None else None,
                "after": json.dumps(after, default=str) if after is not None else None,
                "request_id": request_id,
                "ip_address": ip_address,
            },
        )
