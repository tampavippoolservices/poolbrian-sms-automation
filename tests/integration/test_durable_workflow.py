from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.db import transaction
from app.domain.jobs import provider_status_rank
from app.repositories.campaigns import create_completed_service_workflow
from app.repositories.events import claim_inbound_events, store_inbound_event
from app.repositories.jobs import (
    claim_message_jobs,
    mark_accepted,
    mark_sending,
    record_provider_event,
    retry_dead_or_failed_job,
)
from app.repositories.preferences import is_suppressed, save_preference

pytestmark = pytest.mark.integration


def test_completed_workflow_is_idempotent_and_schedules_all_channels() -> None:
    arguments = {
        "source_job_id": 9001,
        "customer_id": 501,
        "customer_name": "Javier Tamayo",
        "phone_e164": "+18135551212",
        "email_normalized": "javier@example.com",
        "now": datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        "timezone_name": "America/New_York",
        "sms_delay_hours": 3,
        "email_hour": 10,
        "suppression_days": 120,
        "max_attempts": 5,
    }
    first = create_completed_service_workflow(**arguments)
    second = create_completed_service_workflow(**arguments)

    assert first["campaign_created"] is True
    assert second == {"created": False, "reason": "already_processed"}
    with transaction() as connection:
        rows = connection.execute(
            text(
                """
                SELECT message_kind, channel, scheduled_at
                FROM message_jobs ORDER BY message_kind
                """
            )
        ).mappings()
        jobs = {row["message_kind"]: dict(row) for row in rows}
    assert set(jobs) == {
        "completed_service_sms",
        "initial_review_sms",
        "next_day_review_email",
        "saturday_review_email",
    }
    assert jobs["next_day_review_email"]["scheduled_at"] == datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
    assert jobs["saturday_review_email"]["scheduled_at"] == datetime(2026, 8, 22, 14, 0, tzinfo=UTC)


def test_review_campaign_is_suppressed_for_shared_email_across_customers() -> None:
    common = {
        "phone_e164": None,
        "email_normalized": "shared@example.com",
        "now": datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        "timezone_name": "America/New_York",
        "sms_delay_hours": 3,
        "email_hour": 10,
        "suppression_days": 120,
        "max_attempts": 5,
    }
    first = create_completed_service_workflow(
        source_job_id=9010,
        customer_id=510,
        customer_name="First Customer",
        **common,
    )
    second = create_completed_service_workflow(
        source_job_id=9011,
        customer_id=511,
        customer_name="Second Customer",
        **common,
    )

    assert first["campaign_created"] is True
    assert second == {
        "created": True,
        "campaign_created": False,
        "reason": "recent_or_confirmed_campaign",
    }
    with transaction() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM review_campaigns")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM message_jobs WHERE channel = 'email'")
            ).scalar_one()
            == 2
        )


def test_suppression_is_audited_and_cancels_pending_jobs() -> None:
    create_completed_service_workflow(
        source_job_id=9002,
        customer_id=502,
        customer_name="Customer",
        phone_e164="+18135551212",
        email_normalized=None,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        sms_delay_hours=3,
        email_hour=10,
        suppression_days=120,
        max_attempts=5,
    )
    save_preference("sms", "+18135551212", True, "customer_stop", "twilio")
    assert is_suppressed("sms", "+18135551212")
    with transaction() as connection:
        statuses = connection.execute(
            text("SELECT DISTINCT status FROM message_jobs WHERE channel = 'sms'")
        ).scalars()
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM communication_preference_events")
        ).scalar_one()
    assert set(statuses) == {"cancelled"}
    assert event_count == 1


def test_inbound_event_claim_is_atomic() -> None:
    event_id, created = store_inbound_event(
        provider="poolbrain",
        event_type="alert.triggered",
        external_id="event-1",
        payload={"event": "alert.triggered"},
        payload_sha256="digest",
        max_attempts=5,
    )
    duplicate_id, duplicate_created = store_inbound_event(
        provider="poolbrain",
        event_type="alert.triggered",
        external_id="event-1",
        payload={"event": "alert.triggered"},
        payload_sha256="digest",
        max_attempts=5,
    )
    first_claim = claim_inbound_events(worker_id="one", limit=10, lease_minutes=10)
    second_claim = claim_inbound_events(worker_id="two", limit=10, lease_minutes=10)
    assert created is True
    assert duplicate_created is False
    assert duplicate_id == event_id
    assert [row["id"] for row in first_claim] == [event_id]
    assert second_claim == []


def test_fast_provider_callback_cannot_be_overwritten_by_sender() -> None:
    create_completed_service_workflow(
        source_job_id=9003,
        customer_id=503,
        customer_name="Customer",
        phone_e164="+18135551212",
        email_normalized=None,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        sms_delay_hours=3,
        email_hour=10,
        suppression_days=120,
        max_attempts=5,
    )
    job = claim_message_jobs(
        worker_id="sender-one",
        limit=1,
        lease_minutes=10,
        allowed_kinds=["completed_service_sms"],
    )[0]
    assert mark_sending(int(job["id"]), "sender-one")
    record_provider_event(
        provider="twilio",
        provider_message_id="SM12345678901234567890123456789012",
        status="delivered",
        status_rank=provider_status_rank("delivered"),
        error_code=None,
        destination="+18135551212",
        payload={"MessageStatus": "delivered"},
        message_job_id=int(job["id"]),
    )
    assert mark_accepted(
        job_id=int(job["id"]),
        worker_id="sender-one",
        provider="twilio",
        provider_message_id="SM12345678901234567890123456789012",
        provider_status="accepted",
        status_rank=provider_status_rank("accepted"),
    )
    with transaction() as connection:
        final = (
            connection.execute(
                text(
                    """
                SELECT status, provider_status, locked_by
                FROM message_jobs WHERE id = :job_id
                """
                ),
                {"job_id": job["id"]},
            )
            .mappings()
            .one()
        )
    assert final["status"] == "delivered"
    assert final["provider_status"] == "delivered"
    assert final["locked_by"] is None


def test_manual_retry_preserves_attempt_history_and_replaces_provider_reference() -> None:
    create_completed_service_workflow(
        source_job_id=9004,
        customer_id=504,
        customer_name="Customer",
        phone_e164="+18135551212",
        email_normalized=None,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        sms_delay_hours=3,
        email_hour=10,
        suppression_days=120,
        max_attempts=5,
    )
    first = claim_message_jobs(
        worker_id="sender-one",
        limit=1,
        lease_minutes=10,
        allowed_kinds=["completed_service_sms"],
    )[0]
    job_id = int(first["id"])
    first_message_id = "SM11111111111111111111111111111111"
    second_message_id = "SM22222222222222222222222222222222"

    assert first["attempt_count"] == 1
    assert mark_sending(job_id, "sender-one")
    assert mark_accepted(
        job_id=job_id,
        worker_id="sender-one",
        provider="twilio",
        provider_message_id=first_message_id,
        provider_status="accepted",
        status_rank=provider_status_rank("accepted"),
    )
    record_provider_event(
        provider="twilio",
        provider_message_id=first_message_id,
        status="undelivered",
        status_rank=provider_status_rank("undelivered"),
        error_code="30006",
        destination="+18135551212",
        payload={"MessageStatus": "undelivered", "ErrorCode": "30006"},
        message_job_id=job_id,
    )

    assert retry_dead_or_failed_job(job_id)
    with transaction() as connection:
        retried = (
            connection.execute(
                text(
                    """
                    SELECT status, attempt_count, provider, provider_message_id,
                           provider_status, provider_status_rank
                    FROM message_jobs WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            .mappings()
            .one()
        )
    assert dict(retried) == {
        "status": "queued",
        "attempt_count": 1,
        "provider": None,
        "provider_message_id": None,
        "provider_status": None,
        "provider_status_rank": 0,
    }

    second = claim_message_jobs(
        worker_id="sender-two",
        limit=1,
        lease_minutes=10,
        allowed_kinds=["completed_service_sms"],
    )[0]
    assert second["id"] == job_id
    assert second["attempt_count"] == 2
    assert mark_sending(job_id, "sender-two")
    assert mark_accepted(
        job_id=job_id,
        worker_id="sender-two",
        provider="twilio",
        provider_message_id=second_message_id,
        provider_status="accepted",
        status_rank=provider_status_rank("accepted"),
    )

    with transaction() as connection:
        final = (
            connection.execute(
                text(
                    """
                    SELECT status, attempt_count, provider_message_id
                    FROM message_jobs WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            .mappings()
            .one()
        )
        attempts = list(
            connection.execute(
                text(
                    """
                    SELECT attempt_number, provider_message_id
                    FROM message_attempts
                    WHERE message_job_id = :job_id
                    ORDER BY attempt_number
                    """
                ),
                {"job_id": job_id},
            ).mappings()
        )
    assert dict(final) == {
        "status": "accepted",
        "attempt_count": 2,
        "provider_message_id": second_message_id,
    }
    assert [dict(attempt) for attempt in attempts] == [
        {"attempt_number": 1, "provider_message_id": first_message_id},
        {"attempt_number": 2, "provider_message_id": second_message_id},
    ]


def test_claim_repairs_legacy_attempt_counter_drift() -> None:
    create_completed_service_workflow(
        source_job_id=9005,
        customer_id=505,
        customer_name="Customer",
        phone_e164="+18135551212",
        email_normalized=None,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        timezone_name="America/New_York",
        sms_delay_hours=3,
        email_hour=10,
        suppression_days=120,
        max_attempts=5,
    )
    first = claim_message_jobs(
        worker_id="sender-one",
        limit=1,
        lease_minutes=10,
        allowed_kinds=["completed_service_sms"],
    )[0]
    job_id = int(first["id"])
    with transaction() as connection:
        connection.execute(
            text(
                """
                UPDATE message_jobs
                SET status = 'queued', attempt_count = 0,
                    locked_at = NULL, locked_by = NULL, lease_expires_at = NULL
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )

    repaired = claim_message_jobs(
        worker_id="sender-two",
        limit=1,
        lease_minutes=10,
        allowed_kinds=["completed_service_sms"],
    )[0]

    assert repaired["id"] == job_id
    assert repaired["attempt_count"] == 2
    with transaction() as connection:
        attempt_numbers = list(
            connection.execute(
                text(
                    """
                    SELECT attempt_number FROM message_attempts
                    WHERE message_job_id = :job_id
                    ORDER BY attempt_number
                    """
                ),
                {"job_id": job_id},
            ).scalars()
        )
    assert attempt_numbers == [1, 2]
