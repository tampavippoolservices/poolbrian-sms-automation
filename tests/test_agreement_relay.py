import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest
import requests

from app import workers
from app.services import agreements


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("AGREEMENTS_DRAFT_RELAY_ENABLED", "true")
    monkeypatch.setenv("POOLBRAIN_WEBHOOK_SIGNING_SECRET", "synthetic-test-secret")
    post = Mock(return_value=Mock(status_code=200, json=lambda: {"received": True, "pending": 0}))
    monkeypatch.setattr(agreements.requests, "post", post)
    return post


def payload():
    return {
        "id": "test",
        "event": "customer.created",
        "company_id": 40,
        "timestamp": "2026-09-08T01:00:00Z",
        "data": [{"customerId": 1001}],
    }


def test_forwards_signed_original_envelope_only_to_fixed_destination(configured):
    agreements.forward_customer_created(payload())
    args, kwargs = configured.call_args
    assert args == (agreements.AGREEMENTS_ENDPOINT,)
    assert json.loads(kwargs["data"]) == payload()
    assert (
        kwargs["headers"]["X-Webhook-Signature"]
        == hmac.new(b"synthetic-test-secret", kwargs["data"], hashlib.sha256).hexdigest()
    )
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("code", [301, 400, 401, 429, 500, 503])
def test_rejected_or_partial_responses_fail_for_durable_retry(configured, code):
    configured.return_value.status_code = code
    with pytest.raises(RuntimeError):
        agreements.forward_customer_created(payload())


def test_timeout_fails_for_safe_idempotent_retry(configured):
    configured.side_effect = requests.Timeout("must not appear in error")
    with pytest.raises(RuntimeError, match="could not reach"):
        agreements.forward_customer_created(payload())


def test_disabled_keeps_event_retryable_without_network(configured, monkeypatch):
    monkeypatch.delenv("AGREEMENTS_DRAFT_RELAY_ENABLED")
    with pytest.raises(RuntimeError, match="not enabled"):
        agreements.forward_customer_created(payload())
    configured.assert_not_called()


def test_customer_event_takes_relay_path_without_sms(configured):
    client = Mock()
    workers._process_poolbrain_event({"payload": payload()}, client, Mock())
    configured.assert_called_once()
    assert client.mock_calls == []


def test_alert_and_status_events_never_use_relay(configured):
    for kind in ("alert.triggered", "customer.status.updated"):
        workers._process_poolbrain_event({"payload": {"event": kind, "data": {}}}, Mock(), Mock())
    configured.assert_not_called()


def test_worker_retries_relay_failure_and_continues_other_events(monkeypatch, configured):
    configured.side_effect = requests.Timeout()
    monkeypatch.setattr(workers, "heartbeat_started", Mock())
    monkeypatch.setattr(workers, "heartbeat_succeeded", Mock())
    monkeypatch.setattr(
        workers,
        "claim_inbound_events",
        lambda **kw: [
            {
                "id": 1,
                "provider": "poolbrain",
                "payload": payload(),
                "external_id": "test",
                "attempt_count": 1,
            },
            {
                "id": 2,
                "provider": "poolbrain",
                "payload": {"event": "alert.triggered", "data": {}},
                "external_id": "alert",
                "attempt_count": 1,
            },
        ],
    )
    complete, fail = Mock(), Mock()
    monkeypatch.setattr(workers, "complete_inbound_event", complete)
    monkeypatch.setattr(workers, "fail_inbound_event", fail)
    monkeypatch.setattr(workers, "PoolBrainClient", Mock())
    result = workers.process_inbound_events(Mock(MESSAGE_LEASE_MINUTES=10))
    assert result == {"claimed": 2, "completed": 1, "failed": 1}
    assert fail.call_args.args[0] == 1
    assert complete.call_args.args[0] == 2
