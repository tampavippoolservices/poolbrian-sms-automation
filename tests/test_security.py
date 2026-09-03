import hashlib
import hmac

from flask import Flask
from twilio.request_validator import RequestValidator

from app.security import (
    valid_poolbrain_webhook,
    valid_twilio_request,
    valid_website_lead_webhook,
)


def test_poolbrain_hmac_uses_raw_request_body(monkeypatch) -> None:
    app = Flask(__name__)
    body = b'{"event":"alert.triggered"}'
    secret = "poolbrain-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setenv("POOLBRAIN_WEBHOOK_SIGNING_SECRET", secret)
    with app.test_request_context(
        "/webhooks/poolbrain",
        method="POST",
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": signature},
    ):
        assert valid_poolbrain_webhook()


def test_twilio_signature_includes_query_string(monkeypatch) -> None:
    app = Flask(__name__)
    app.config["PUBLIC_BASE_URL"] = "https://automation.example.com"
    token = "twilio-secret"
    form = {"MessageSid": "SM123", "MessageStatus": "delivered"}
    url = "https://automation.example.com/webhooks/twilio/status?job_id=42"
    signature = RequestValidator(token).compute_signature(url, form)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    with app.test_request_context(
        "/webhooks/twilio/status?job_id=42",
        method="POST",
        data=form,
        headers={"X-Twilio-Signature": signature},
    ):
        assert valid_twilio_request()


def test_website_lead_hmac_uses_raw_request_body(monkeypatch) -> None:
    app = Flask(__name__)
    body = b'{"event":"website.lead.created","id":"site-lead-42"}'
    secret = "website-lead-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setenv("WEBSITE_LEAD_WEBHOOK_SECRET", secret)
    with app.test_request_context(
        "/webhooks/website-lead",
        method="POST",
        data=body,
        content_type="application/json",
        headers={"X-Tampa-VIP-Signature": signature},
    ):
        assert valid_website_lead_webhook()
