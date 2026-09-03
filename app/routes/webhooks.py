from __future__ import annotations

import hmac
import json
import logging
import os
import re
from contextlib import suppress
from typing import Any

from flask import Blueprint, Response, abort, current_app, jsonify, request

from app.config import AppConfig
from app.domain.contact import (
    InvalidContact,
    masked_destination,
    normalize_email,
    normalize_us_phone,
)
from app.domain.jobs import provider_status_rank
from app.repositories.events import store_inbound_event
from app.repositories.inbound_messages import record_inbound_sms
from app.repositories.jobs import (
    enqueue_delivery_failure_alert,
    enqueue_website_lead_notifications,
    record_provider_event,
)
from app.security import (
    payload_sha256,
    valid_poolbrain_webhook,
    valid_twilio_request,
    valid_website_lead_webhook,
)
from app.workers import process_website_lead_event

logger = logging.getLogger(__name__)
webhook_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")
legacy_webhook_bp = Blueprint("legacy_webhooks", __name__)

_SID_PATTERN = re.compile(r"^[A-Za-z0-9]{20,64}$")
_WEBSITE_LEAD_ID_PATTERN = re.compile(r"^site-lead-[1-9][0-9]{0,18}$")
_TWILIO_STATUSES = {
    "accepted",
    "scheduled",
    "queued",
    "sending",
    "sent",
    "delivered",
    "undelivered",
    "failed",
    "canceled",
}
_OPT_OUT_KEYWORDS = frozenset(
    {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
)
_OPT_IN_KEYWORDS = frozenset({"START", "UNSTOP", "YES"})


@webhook_bp.post("/poolbrain")
def poolbrain():
    if not valid_poolbrain_webhook():
        logger.warning("Rejected invalid PoolBrain webhook", extra={"event": "webhook_rejected"})
        abort(403)
    raw_body = request.get_data(cache=True)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, "Expected a JSON object")
    event_type = str(payload.get("event") or "unknown")[:100]
    event_id = str(payload.get("id") or "").strip()
    digest = payload_sha256(raw_body)
    external_id = event_id[:200] if event_id else f"sha256:{digest}"
    stored_id, created = store_inbound_event(
        provider="poolbrain",
        event_type=event_type,
        external_id=external_id,
        payload=payload,
        payload_sha256=digest,
        max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
    )
    return jsonify({"accepted": True, "created": created, "event_id": stored_id}), 202


@webhook_bp.post("/website-lead")
def website_lead():
    if not valid_website_lead_webhook():
        logger.warning("Rejected invalid website lead webhook", extra={"event": "webhook_rejected"})
        abort(403)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("event") != "website.lead.created":
        abort(400, "Website lead event is invalid")
    event_id = str(payload.get("id") or "").strip()
    lead = payload.get("lead")
    if not _WEBSITE_LEAD_ID_PATTERN.fullmatch(event_id) or not isinstance(lead, dict):
        abort(400, "Website lead payload is invalid")

    try:
        sms_destination = normalize_us_phone(os.getenv("WEBSITE_LEAD_SMS_DESTINATION"))
        email_destination = normalize_email(os.getenv("WEBSITE_LEAD_EMAIL_DESTINATION"))
        customer_phone = normalize_us_phone(_lead_text(lead, "phone", 40))
    except InvalidContact:
        abort(503, "Website lead delivery destinations are not configured")

    mode = _lead_text(lead, "mode", 20)
    if mode not in {"schedule", "callback"}:
        abort(400, "Website lead mode is invalid")
    normalized_lead: dict[str, object] = {
        "lead_id": _positive_int(lead.get("id")),
        "mode": mode,
        "name": _lead_text(lead, "name", 120),
        "phone": customer_phone,
        "zip": _lead_text(lead, "zip", 10),
        "service": _lead_text(lead, "service", 120),
        "preferred_date": _optional_lead_text(lead, "preferred_date", 20),
        "preferred_time": _optional_lead_text(lead, "preferred_time", 80),
        "notes": _optional_lead_text(lead, "notes", 1500),
        "sms_consent": bool(lead.get("sms_consent")),
    }
    if not normalized_lead["lead_id"]:
        abort(400, "Website lead id is invalid")
    created = enqueue_website_lead_notifications(
        event_id=event_id,
        sms_destination=sms_destination,
        email_destination=email_destination,
        lead=normalized_lead,
        max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
    )
    return jsonify({"accepted": True, "created": created}), 202


@webhook_bp.post("/website-lead/dispatch")
def dispatch_website_lead():
    if not valid_website_lead_webhook():
        logger.warning(
            "Rejected invalid website lead dispatch",
            extra={"event": "webhook_rejected"},
        )
        abort(403)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("event") != "website.lead.dispatch":
        abort(400, "Website lead dispatch event is invalid")
    event_id = str(payload.get("id") or "").strip()
    if not _WEBSITE_LEAD_ID_PATTERN.fullmatch(event_id):
        abort(400, "Website lead id is invalid")

    try:
        result = process_website_lead_event(
            AppConfig.from_environment(),
            event_id=event_id,
        )
    except Exception:
        logger.exception(
            "Immediate website lead dispatch failed; scheduled worker will retry",
            extra={"event": "website_lead_dispatch_failed", "external_id": event_id},
        )
        abort(503, "Immediate dispatch is temporarily unavailable")
    return jsonify({"accepted": True, "event_id": event_id, "delivery": result}), 200


@webhook_bp.post("/twilio/inbound")
def twilio_inbound():
    if not valid_twilio_request():
        logger.warning(
            "Rejected invalid Twilio inbound webhook", extra={"event": "webhook_rejected"}
        )
        abort(403)
    message_sid = (request.form.get("MessageSid") or "").strip()
    from_number = request.form.get("From")
    to_number = request.form.get("To")
    body = (request.form.get("Body") or "")[:2000]
    if not _SID_PATTERN.fullmatch(message_sid):
        abort(400, "MessageSid is invalid")
    try:
        from_normalized = normalize_us_phone(from_number)
    except InvalidContact:
        abort(400, "From is invalid")
    try:
        to_normalized = normalize_us_phone(to_number)
    except InvalidContact:
        to_normalized = None
    normalized_body = body.strip().upper()
    opt_out_type = (request.form.get("OptOutType") or "").strip().upper()
    opt_out = opt_out_type == "STOP" or normalized_body in _OPT_OUT_KEYWORDS
    opt_in = opt_out_type == "START" or normalized_body in _OPT_IN_KEYWORDS
    company_phone = None
    with suppress(InvalidContact):
        company_phone = normalize_us_phone(os.getenv("COMPANY_PHONE_NUMBER"))
    result = record_inbound_sms(
        provider_message_id=message_sid,
        from_normalized=from_normalized,
        to_normalized=to_normalized,
        message_body=body,
        customer_id=None,
        opt_out=opt_out,
        opt_in=opt_in,
        company_phone=company_phone,
        customer_name="Unknown customer",
        max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
    )
    logger.info(
        "Stored Twilio inbound message",
        extra={"event": "twilio_inbound_stored", "external_id": message_sid},
    )
    return Response(status=204, headers={"X-Duplicate": str(not result["created"]).lower()})


@webhook_bp.post("/twilio/status")
def twilio_status():
    if not valid_twilio_request():
        logger.warning(
            "Rejected invalid Twilio status webhook", extra={"event": "webhook_rejected"}
        )
        abort(403)
    message_sid = (request.form.get("MessageSid") or "").strip()
    status = (request.form.get("MessageStatus") or "").strip().lower()
    error_code = (request.form.get("ErrorCode") or "").strip() or None
    if not _SID_PATTERN.fullmatch(message_sid) or status not in _TWILIO_STATUSES:
        abort(400, "Twilio status payload is invalid")
    job_id = _positive_int(request.args.get("job_id"))
    destination = None
    with suppress(InvalidContact):
        destination = normalize_us_phone(request.form.get("To"))
    row = record_provider_event(
        provider="twilio",
        provider_message_id=message_sid,
        status=status,
        status_rank=provider_status_rank(status),
        error_code=error_code,
        destination=destination,
        payload={key: value for key, value in request.form.items()},
        message_job_id=job_id,
    )
    if (
        row
        and status in {"failed", "undelivered"}
        and row["message_kind"] != "admin_delivery_failure_sms"
    ):
        try:
            alert_destination = normalize_us_phone(os.getenv("ALERT_PHONE_NUMBER"))
        except InvalidContact:
            alert_destination = None
        if alert_destination:
            enqueue_delivery_failure_alert(
                failed_provider_message_id=message_sid,
                alert_destination=alert_destination,
                failed_destination_masked=masked_destination(destination),
                provider_status=status,
                error_code=error_code,
                max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
            )
    return Response(status=204)


@webhook_bp.route("/microsoft", methods=["GET", "POST"])
def microsoft_notifications():
    validation_token = request.args.get("validationToken")
    if validation_token is not None:
        return Response(validation_token, status=200, content_type="text/plain")
    expected_state = os.getenv("MICROSOFT_WEBHOOK_CLIENT_STATE", "")
    payload = request.get_json(silent=True)
    if not expected_state or not isinstance(payload, dict):
        abort(403)
    notifications = payload.get("value", [])
    if not isinstance(notifications, list):
        abort(400)
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        provided_state = str(notification.get("clientState") or "")
        if not hmac.compare_digest(expected_state, provided_state):
            abort(403)
        serialized = json.dumps(notification, sort_keys=True).encode("utf-8")
        external_id = str(notification.get("id") or payload_sha256(serialized))
        store_inbound_event(
            provider="microsoft",
            event_type="message_change",
            external_id=external_id,
            payload=notification,
            payload_sha256=payload_sha256(serialized),
            max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
        )
    return Response(status=202)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _lead_text(lead: dict[str, Any], key: str, limit: int) -> str:
    value = str(lead.get(key) or "").strip()
    if not value or len(value) > limit:
        abort(400, f"Website lead {key} is invalid")
    return value


def _optional_lead_text(lead: dict[str, Any], key: str, limit: int) -> str | None:
    value = str(lead.get(key) or "").strip()
    if len(value) > limit:
        abort(400, f"Website lead {key} is invalid")
    return value or None


# Compatibility aliases for the existing provider configuration during cutover.
legacy_webhook_bp.add_url_rule(
    "/webhook",
    endpoint="legacy_poolbrain",
    view_func=poolbrain,
    methods=["POST"],
)
legacy_webhook_bp.add_url_rule(
    "/incoming-sms",
    endpoint="legacy_twilio_inbound",
    view_func=twilio_inbound,
    methods=["POST"],
)
legacy_webhook_bp.add_url_rule(
    "/twilio-status",
    endpoint="legacy_twilio_status",
    view_func=twilio_status,
    methods=["POST"],
)
