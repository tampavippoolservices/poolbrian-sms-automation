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
