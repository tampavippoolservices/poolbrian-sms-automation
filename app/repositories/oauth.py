from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from app.crypto import decrypt_secret, encrypt_secret
from app.db import transaction
from app.time_utils import utc_now


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def create_oauth_state(provider: str, initiated_by: str | None, lifetime_minutes: int = 10) -> str:
    state = secrets.token_urlsafe(32)
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO oauth_states (state_hash, provider, initiated_by, expires_at)
                VALUES (:state_hash, :provider, :initiated_by, :expires_at)
                """
            ),
            {
                "state_hash": _state_hash(state),
                "provider": provider,
                "initiated_by": initiated_by,
                "expires_at": utc_now() + timedelta(minutes=lifetime_minutes),
            },
        )
    return state


def consume_oauth_state(provider: str, state: str) -> bool:
    with transaction() as connection:
        row = connection.execute(
            text(
                """
                UPDATE oauth_states
                SET consumed_at = NOW()
                WHERE state_hash = :state_hash
                  AND provider = :provider
                  AND consumed_at IS NULL
                  AND expires_at > NOW()
                RETURNING state_hash
                """
            ),
            {"state_hash": _state_hash(state), "provider": provider},
        ).first()
        return row is not None


def save_refresh_token(
    provider: str,
    refresh_token: str,
    *,
    scopes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    encrypted = encrypt_secret(refresh_token)
    with transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO oauth_credentials (
                    provider, encrypted_refresh_token, scopes, metadata
                ) VALUES (
                    :provider, :encrypted, :scopes, CAST(:metadata AS JSONB)
                )
                ON CONFLICT (provider)
                DO UPDATE SET
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    scopes = EXCLUDED.scopes,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "provider": provider,
                "encrypted": encrypted,
                "scopes": scopes,
                "metadata": json.dumps(metadata or {}),
            },
        )


def get_refresh_token(provider: str) -> str | None:
    with transaction() as connection:
        encrypted = connection.execute(
            text(
                """
                SELECT encrypted_refresh_token
                FROM oauth_credentials WHERE provider = :provider
                """
            ),
            {"provider": provider},
        ).scalar_one_or_none()
    return decrypt_secret(str(encrypted)) if encrypted else None


def prune_oauth_states() -> int:
    with transaction() as connection:
        result = connection.execute(
            text("DELETE FROM oauth_states WHERE expires_at < NOW() - INTERVAL '1 day'")
        )
        return int(result.rowcount or 0)
