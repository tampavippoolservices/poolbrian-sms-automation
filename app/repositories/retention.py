from __future__ import annotations

from sqlalchemy import text

from app.db import transaction


def apply_retention_policy(
    *,
    message_content_days: int,
    operational_event_days: int,
    audit_days: int,
) -> dict[str, int]:
    """Remove sensitive content before removing longer-lived operational metadata."""
    with transaction() as connection:
        scrubbed_messages = connection.execute(
            text(
                """
                UPDATE inbound_messages
                SET body_ciphertext = NULL, body_preview = '[content expired]'
                WHERE received_at < NOW() - (:days * INTERVAL '1 day')
                  AND body_ciphertext IS NOT NULL
                """
            ),
            {"days": message_content_days},
        ).rowcount
        scrubbed_forwarded_bodies = connection.execute(
            text(
                """
                UPDATE message_jobs
                SET template_data = template_data - 'message_body', updated_at = NOW()
                WHERE created_at < NOW() - (:days * INTERVAL '1 day')
                  AND template_data ? 'message_body'
                """
            ),
            {"days": message_content_days},
        ).rowcount
        deleted_events = connection.execute(
            text(
                """
                DELETE FROM inbound_events
                WHERE received_at < NOW() - (:days * INTERVAL '1 day')
                  AND status IN ('completed', 'dead')
                """
            ),
            {"days": operational_event_days},
        ).rowcount
        deleted_provider_events = connection.execute(
            text(
                """
                DELETE FROM provider_message_events
                WHERE received_at < NOW() - (:days * INTERVAL '1 day')
                """
            ),
            {"days": operational_event_days},
        ).rowcount
        deleted_attempts = connection.execute(
            text(
                """
                DELETE FROM message_attempts
                WHERE started_at < NOW() - (:days * INTERVAL '1 day')
                """
            ),
            {"days": operational_event_days},
        ).rowcount
        deleted_states = connection.execute(
            text(
                """
                DELETE FROM oauth_states
                WHERE expires_at < NOW() - INTERVAL '1 day'
                """
            )
        ).rowcount
        deleted_audits = connection.execute(
            text(
                """
                DELETE FROM audit_events
                WHERE occurred_at < NOW() - (:days * INTERVAL '1 day')
                """
            ),
            {"days": audit_days},
        ).rowcount

        legacy_deleted = 0
        if connection.execute(text("SELECT to_regclass('public.inbound_sms')")).scalar_one():
            legacy_deleted = connection.execute(
                text(
                    """
                    DELETE FROM inbound_sms
                    WHERE received_at < NOW() - (:days * INTERVAL '1 day')
                    """
                ),
                {"days": message_content_days},
            ).rowcount

    return {
        "inbound_message_bodies_scrubbed": int(scrubbed_messages or 0),
        "forwarded_message_bodies_scrubbed": int(scrubbed_forwarded_bodies or 0),
        "inbound_events_deleted": int(deleted_events or 0),
        "provider_events_deleted": int(deleted_provider_events or 0),
        "message_attempts_deleted": int(deleted_attempts or 0),
        "oauth_states_deleted": int(deleted_states or 0),
        "audit_events_deleted": int(deleted_audits or 0),
        "legacy_inbound_messages_deleted": int(legacy_deleted or 0),
    }
