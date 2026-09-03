from types import SimpleNamespace
from typing import cast

from app import workers
from app.config import AppConfig


def test_process_all_continues_after_independent_step_failure(monkeypatch) -> None:
    called: list[str] = []

    def fail_completed(_config) -> None:
        called.append("completed")
        raise RuntimeError("PoolBrain unavailable")

    monkeypatch.setattr(workers, "recover_stale_work", lambda: {"recovered": 0})
    monkeypatch.setattr(workers, "poll_completed_services", fail_completed)
    monkeypatch.setattr(
        workers,
        "process_inbound_events",
        lambda _config: called.append("events") or {"processed": 1},
    )
    monkeypatch.setattr(
        workers,
        "process_due_messages",
        lambda _config: called.append("messages") or {"accepted": 1},
    )

    result = workers.process_all(cast(AppConfig, object()))

    assert called == ["completed", "events", "messages"]
    assert result["success"] is False
    assert result["errors"] == {"completed_services": "RuntimeError"}
    assert result["messages"] == {"accepted": 1}


def test_general_worker_excludes_website_leads(monkeypatch) -> None:
    monkeypatch.setattr(workers, "within_local_hours", lambda *_args: True)
    disabled = SimpleNamespace(
        BUSINESS_TIMEZONE="America/New_York",
        OUTLOOK_SEND_ENABLED=False,
    )
    enabled = SimpleNamespace(
        BUSINESS_TIMEZONE="America/New_York",
        OUTLOOK_SEND_ENABLED=True,
    )

    disabled_kinds = workers._allowed_message_kinds(cast(AppConfig, disabled))
    enabled_kinds = workers._allowed_message_kinds(cast(AppConfig, enabled))

    assert "initial_review_sms" in disabled_kinds
    assert "next_day_review_email" not in disabled_kinds
    assert "saturday_review_email" not in disabled_kinds
    assert "next_day_review_email" in enabled_kinds
    assert "saturday_review_email" in enabled_kinds
    assert "admin_website_lead_sms" not in enabled_kinds
    assert "admin_website_lead_email" not in disabled_kinds
    assert "admin_website_lead_email" not in enabled_kinds


def test_website_lead_worker_controls_sms_hours_and_email_flag(monkeypatch) -> None:
    disabled = SimpleNamespace(
        BUSINESS_TIMEZONE="America/New_York",
        OUTLOOK_SEND_ENABLED=False,
    )
    enabled = SimpleNamespace(
        BUSINESS_TIMEZONE="America/New_York",
        OUTLOOK_SEND_ENABLED=True,
    )

    monkeypatch.setattr(workers, "within_local_hours", lambda *_args: False)
    assert workers._website_lead_message_kinds(cast(AppConfig, disabled)) == []
    assert workers._website_lead_message_kinds(cast(AppConfig, enabled)) == [
        "admin_website_lead_email"
    ]

    monkeypatch.setattr(workers, "within_local_hours", lambda *_args: True)
    assert workers._website_lead_message_kinds(cast(AppConfig, disabled)) == [
        "admin_website_lead_sms"
    ]
    assert workers._website_lead_message_kinds(cast(AppConfig, enabled)) == [
        "admin_website_lead_sms",
        "admin_website_lead_email",
    ]


def test_website_lead_worker_uses_dedicated_filter_and_heartbeat(monkeypatch) -> None:
    config = cast(AppConfig, SimpleNamespace())
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        workers,
        "_website_lead_message_kinds",
        lambda _config: ["admin_website_lead_sms", "admin_website_lead_email"],
    )

    def fake_process(_config, **kwargs):
        captured.update(kwargs)
        return {"accepted": 2}

    monkeypatch.setattr(workers, "process_due_messages", fake_process)

    result = workers.process_website_lead_messages(config, limit=12)

    assert result == {"accepted": 2}
    assert captured == {
        "limit": 12,
        "allowed_message_kinds": [
            "admin_website_lead_sms",
            "admin_website_lead_email",
        ],
        "heartbeat_name": "process_website_lead_messages",
    }


def test_website_lead_event_worker_scopes_claim_to_one_event(monkeypatch) -> None:
    config = cast(AppConfig, SimpleNamespace())
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        workers,
        "_website_lead_message_kinds",
        lambda _config: ["admin_website_lead_sms", "admin_website_lead_email"],
    )

    def fake_process(_config, **kwargs):
        captured.update(kwargs)
        return {"accepted": 2}

    monkeypatch.setattr(workers, "process_due_messages", fake_process)

    result = workers.process_website_lead_event(config, event_id="site-lead-42")

    assert result == {"accepted": 2}
    assert captured == {
        "limit": 2,
        "allowed_message_kinds": [
            "admin_website_lead_sms",
            "admin_website_lead_email",
        ],
        "idempotency_prefix": "website-lead:site-lead-42:",
        "heartbeat_name": "dispatch_website_lead_event",
    }
