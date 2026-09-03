import hashlib
import hmac
import json
from types import SimpleNamespace

from flask import Flask

from app.routes import webhooks


def test_signed_website_lead_queues_email_and_sms(monkeypatch) -> None:
    app = Flask(__name__)
    app.config["MESSAGE_MAX_ATTEMPTS"] = 5
    app.register_blueprint(webhooks.webhook_bp)
    secret = "website-lead-secret"
    monkeypatch.setenv("WEBSITE_LEAD_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("WEBSITE_LEAD_SMS_DESTINATION", "8134421960")
    monkeypatch.setenv("WEBSITE_LEAD_EMAIL_DESTINATION", "contact@tampavippoolservices.com")
    queued: dict[str, object] = {}

    def fake_enqueue(**kwargs):
        queued.update(kwargs)
        return {"sms": True, "email": True}

    monkeypatch.setattr(webhooks, "enqueue_website_lead_notifications", fake_enqueue)
    payload = {
        "event": "website.lead.created",
        "id": "site-lead-42",
        "occurred_at": "2026-09-02T20:00:00Z",
        "lead": {
            "id": 42,
            "mode": "schedule",
            "name": "Taylor Smith",
            "phone": "(813) 555-0199",
            "zip": "33609",
            "service": "Weekly pool service",
            "preferred_date": "2026-09-05",
            "preferred_time": "Morning",
            "notes": "Please call first",
            "sms_consent": True,
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = app.test_client().post(
        "/webhooks/website-lead",
        data=body,
        content_type="application/json",
        headers={"X-Tampa-VIP-Signature": signature},
    )

    assert response.status_code == 202
    assert queued["event_id"] == "site-lead-42"
    assert queued["sms_destination"] == "+18134421960"
    assert queued["email_destination"] == "contact@tampavippoolservices.com"
    assert queued["lead"]["phone"] == "+18135550199"  # type: ignore[index]


def test_unsigned_website_lead_is_rejected(monkeypatch) -> None:
    app = Flask(__name__)
    app.config["MESSAGE_MAX_ATTEMPTS"] = 5
    app.register_blueprint(webhooks.webhook_bp)
    monkeypatch.setenv("WEBSITE_LEAD_WEBHOOK_SECRET", "website-lead-secret")

    response = app.test_client().post(
        "/webhooks/website-lead",
        json={"event": "website.lead.created", "id": "site-lead-42", "lead": {}},
    )

    assert response.status_code == 403


def test_signed_dispatch_processes_only_requested_website_lead(monkeypatch) -> None:
    app = Flask(__name__)
    app.register_blueprint(webhooks.webhook_bp)
    secret = "website-lead-secret"
    monkeypatch.setenv("WEBSITE_LEAD_WEBHOOK_SECRET", secret)
    config = SimpleNamespace()
    monkeypatch.setattr(
        webhooks.AppConfig,
        "from_environment",
        classmethod(lambda _cls: config),
    )
    dispatched: dict[str, object] = {}

    def fake_dispatch(received_config, *, event_id):
        dispatched.update(config=received_config, event_id=event_id)
        return {"claimed": 2, "accepted": 2}

    monkeypatch.setattr(webhooks, "process_website_lead_event", fake_dispatch)
    payload = {"event": "website.lead.dispatch", "id": "site-lead-42"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    response = app.test_client().post(
        "/webhooks/website-lead/dispatch",
        data=body,
        content_type="application/json",
        headers={"X-Tampa-VIP-Signature": signature},
    )

    assert response.status_code == 200
    assert response.get_json()["delivery"] == {"accepted": 2, "claimed": 2}
    assert dispatched == {"config": config, "event_id": "site-lead-42"}
