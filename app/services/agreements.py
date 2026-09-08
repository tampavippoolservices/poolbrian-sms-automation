"""Relay authenticated customer events through the existing durable inbound queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

import requests

# Pin the destination so a configuration mistake cannot export customer events elsewhere.
AGREEMENTS_ENDPOINT = (
    "https://tampa-vip-agreements.javier-tamay-6677.chatgpt.site/api/webhooks/poolbrain"
)


def forward_customer_created(payload: dict[str, Any]) -> None:
    if payload.get("event") != "customer.created":
        raise ValueError("Only customer-created events may be forwarded")
    if os.getenv("AGREEMENTS_DRAFT_RELAY_ENABLED", "").strip().lower() != "true":
        # Fail into the durable retry queue rather than silently losing customer events.
        raise RuntimeError("Agreement draft relay is not enabled")
    _post(payload, AGREEMENTS_ENDPOINT)


def reconcile_agreement_drafts() -> None:
    if os.getenv("AGREEMENTS_DRAFT_RELAY_ENABLED", "").strip().lower() != "true":
        return
    _post(
        {"event": "drafts.reconcile", "timestamp": datetime.now(UTC).isoformat()},
        AGREEMENTS_ENDPOINT.replace("/api/webhooks/poolbrain", "/api/poolbrain/reconcile"),
    )


def _post(payload: dict[str, Any], endpoint: str) -> None:
    secret = os.getenv("POOLBRAIN_WEBHOOK_SIGNING_SECRET", "")
    if not secret:
        raise RuntimeError("PoolBrain signing secret is not configured")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        response = requests.post(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": signature},
            timeout=(5, 40),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise RuntimeError("Agreement draft relay could not reach the agreement app") from None
    if response.status_code != 200:
        raise RuntimeError(f"Agreement draft relay returned HTTP {response.status_code}")
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Agreement draft relay returned an invalid response") from None
    if not isinstance(result, dict) or not (
        (result.get("received") is True and result.get("pending") == 0)
        or result.get("ignored") is True
    ):
        raise RuntimeError("Agreement app did not confirm draft processing")
