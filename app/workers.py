from __future__ import annotations

import logging
import os
import socket
import uuid
from typing import Any

from requests import RequestException

from app.config import AppConfig
from app.domain.bounces import extract_permanent_bounce
from app.domain.contact import InvalidContact, normalize_email, normalize_us_phone
from app.domain.jobs import provider_status_rank, retry_delay
from app.messages import render_message
from app.repositories.backfill import campaigns_missing_contacts, update_campaign_contacts
from app.repositories.bounces import known_recent_email_destinations, record_permanent_bounce
from app.repositories.campaigns import create_completed_service_workflow, create_water_alert_job
from app.repositories.events import (
    claim_inbound_events,
    complete_inbound_event,
    fail_inbound_event,
    recover_stale_events,
)
from app.repositories.google_reviews import import_google_review
from app.repositories.heartbeats import heartbeat_failed, heartbeat_started, heartbeat_succeeded
from app.repositories.jobs import (
    cancel_message_job,
    cancel_suppressed_queued_jobs,
    claim_message_jobs,
    fail_message_job,
    mark_accepted,
    mark_delivery_unknown,
    mark_sending,
    recover_stale_message_jobs,
)
from app.repositories.outcomes import set_processed_alert_outcome, set_processed_job_outcome
from app.repositories.preferences import is_suppressed
from app.repositories.state import baseline_completed_jobs, get_state
from app.repositories.unsubscribe import get_or_create_unsubscribe_token
from app.services.google import GoogleBusinessClient
from app.services.microsoft import MicrosoftApiError, MicrosoftGraphClient
from app.services.poolbrain import PoolBrainClient, PoolBrainCreatePending
from app.services.twilio import SmsSendError, TwilioSmsClient
from app.time_utils import utc_now, within_local_hours

logger = logging.getLogger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def recover_stale_work() -> dict[str, int]:
    return {
        "events_recovered": recover_stale_events(),
        "messages_recovered": recover_stale_message_jobs(),
        "suppressed_cancelled": cancel_suppressed_queued_jobs(),
    }


def initialize_completed_service_baseline(config: AppConfig) -> dict[str, int]:
    client = PoolBrainClient()
    lookback_days = _integer_environment("POOLBRAIN_COMPLETED_JOB_LOOKBACK_DAYS", 2, 1, 30)
    jobs = client.recent_completed_jobs(
        timezone_name=config.BUSINESS_TIMEZONE,
        lookback_days=lookback_days,
    )
    remembered = baseline_completed_jobs(jobs)
    return {"jobs_received": len(jobs), "jobs_remembered": remembered}


def poll_completed_services(config: AppConfig) -> dict[str, int | str]:
    name = "poll_completed_services"
    heartbeat_started(name)
    if not within_local_hours(utc_now(), config.BUSINESS_TIMEZONE, 6, 19):
        result: dict[str, int | str] = {"status": "outside_hours", "created": 0, "skipped": 0}
        heartbeat_succeeded(name, result)
        return result
    if get_state("completed_service_baseline_v2") != "complete":
        result = {"status": "baseline_required", "created": 0, "skipped": 0}
        heartbeat_succeeded(name, result)
        return result

    client = PoolBrainClient()
    lookback_days = _integer_environment("POOLBRAIN_COMPLETED_JOB_LOOKBACK_DAYS", 2, 1, 30)
    created = 0
    skipped = 0
    failed = 0
    try:
        jobs = client.recent_completed_jobs(
            timezone_name=config.BUSINESS_TIMEZONE,
            lookback_days=lookback_days,
        )
        for job in jobs:
            record_id = _positive_int(job.get("RecordID"))
            customer_id = _positive_int(job.get("CustomerId"))
            if record_id is None or customer_id is None:
                skipped += 1
                continue
            try:
                customer = client.customer(customer_id)
            except Exception:
                failed += 1
                logger.exception(
                    "PoolBrain customer lookup failed",
                    extra={"event": "customer_lookup_failed", "external_id": record_id},
                )
                continue
            if not customer:
                set_processed_job_outcome(record_id, customer_id, "customer_not_found")
                skipped += 1
                continue
            customer_name, phone, email = _customer_contacts(customer)
            if not phone and not email:
                set_processed_job_outcome(record_id, customer_id, "no_contact")
                skipped += 1
                continue
            outcome = create_completed_service_workflow(
                source_job_id=record_id,
                customer_id=customer_id,
                customer_name=customer_name,
                phone_e164=phone,
                email_normalized=email,
                now=utc_now(),
                timezone_name=config.BUSINESS_TIMEZONE,
                sms_delay_hours=config.REVIEW_SMS_DELAY_HOURS,
                email_hour=config.REVIEW_EMAIL_HOUR,
                suppression_days=config.REVIEW_SUPPRESSION_DAYS,
                max_attempts=config.MESSAGE_MAX_ATTEMPTS,
            )
            if outcome.get("created"):
                created += 1
            else:
                skipped += 1
        result = {"status": "complete", "created": created, "skipped": skipped, "failed": failed}
        heartbeat_succeeded(name, result)
        return result
    except Exception as exc:
        heartbeat_failed(name, str(exc))
        raise


def process_inbound_events(config: AppConfig, *, limit: int = 50) -> dict[str, int]:
    name = "process_inbound_events"
    heartbeat_started(name)
    current_worker = worker_id()
    claimed = claim_inbound_events(
        worker_id=current_worker,
        limit=limit,
        lease_minutes=config.MESSAGE_LEASE_MINUTES,
        excluded_provider="website",
    )
    completed = 0
    failed = 0
    client = PoolBrainClient()
    for event in claimed:
        try:
            if event["provider"] == "poolbrain":
                _process_poolbrain_event(event, client, config)
            complete_inbound_event(int(event["id"]), current_worker)
            completed += 1
        except Exception as exc:
            failed += 1
            fail_inbound_event(
                int(event["id"]),
                current_worker,
                str(exc),
                utc_now() + retry_delay(int(event["attempt_count"])),
            )
            logger.exception(
                "Inbound event processing failed",
                extra={"event": "inbound_event_failed", "external_id": event["external_id"]},
            )
    result = {"claimed": len(claimed), "completed": completed, "failed": failed}
    heartbeat_succeeded(name, result)
    return result


def process_due_messages(
    config: AppConfig,
    *,
    limit: int = 50,
    allowed_message_kinds: list[str] | None = None,
    idempotency_prefix: str | None = None,
    heartbeat_name: str = "process_due_messages",
) -> dict[str, int]:
    name = heartbeat_name
    heartbeat_started(name)
    allowed_kinds = (
        _allowed_message_kinds(config) if allowed_message_kinds is None else allowed_message_kinds
    )
    if not allowed_kinds:
        result = {
            "claimed": 0,
            "accepted": 0,
            "failed": 0,
            "delivery_unknown": 0,
            "cancelled": 0,
        }
        heartbeat_succeeded(name, result)
        return result
    current_worker = worker_id()
    jobs = claim_message_jobs(
        worker_id=current_worker,
        limit=limit,
        lease_minutes=config.MESSAGE_LEASE_MINUTES,
        allowed_kinds=allowed_kinds,
        idempotency_prefix=idempotency_prefix,
    )
    accepted = 0
    failed = 0
    delivery_unknown = 0
    cancelled = 0
    sms_client: TwilioSmsClient | None = None
    email_client: MicrosoftGraphClient | None = None
    for job in jobs:
        job_id = int(job["id"])
        destination = str(job.get("destination_normalized") or "")
        channel = str(job["channel"])
        message_kind = str(job["message_kind"])
        if not destination or (
            not message_kind.startswith("admin_") and is_suppressed(channel, destination)
        ):
            cancel_message_job(job_id, current_worker, "missing or suppressed destination")
            cancelled += 1
            continue
        provider_accepted = False
        try:
            unsubscribe_token = None
            if channel == "email" and message_kind in {
                "next_day_review_email",
                "saturday_review_email",
            }:
                unsubscribe_token = get_or_create_unsubscribe_token(
                    message_job_id=job_id,
                    channel="email",
                    destination_normalized=destination,
                )
            rendered = render_message(
                template_key=str(job["template_key"]),
                data=dict(job["template_data"] or {}),
                public_base_url=config.PUBLIC_BASE_URL,
                unsubscribe_token=unsubscribe_token,
            )
            if not mark_sending(job_id, current_worker):
                continue
            if channel == "sms":
                sms_client = sms_client or TwilioSmsClient(config.PUBLIC_BASE_URL)
                send_result: Any = sms_client.send(
                    destination=destination,
                    body=rendered.text,
                    message_job_id=job_id,
                )
                provider = "twilio"
            elif channel == "email":
                if not rendered.subject or not rendered.html:
                    raise ValueError("Rendered email is incomplete")
                email_client = email_client or MicrosoftGraphClient()
                send_result = email_client.send_email(
                    destination=destination,
                    subject=rendered.subject,
                    text_body=rendered.text,
                    html_body=rendered.html,
                    message_job_id=job_id,
                )
                provider = "microsoft_graph"
            else:
                raise ValueError(f"Unsupported channel: {channel}")
            provider_accepted = True
            if not mark_accepted(
                job_id=job_id,
                worker_id=current_worker,
                provider=provider,
                provider_message_id=send_result.provider_message_id,
                provider_status=send_result.provider_status,
                status_rank=provider_status_rank(send_result.provider_status),
            ):
                raise RuntimeError(
                    "Provider accepted the message but the lease was no longer owned"
                )
            _update_source_outcome(job, "message_accepted", send_result.provider_message_id)
            accepted += 1
        except (SmsSendError, MicrosoftApiError) as exc:
            if exc.uncertain:
                mark_delivery_unknown(
                    job_id=job_id,
                    worker_id=current_worker,
                    error=str(exc),
                    error_code=exc.code,
                )
                _update_source_outcome(job, "delivery_unknown", None, str(exc))
                delivery_unknown += 1
            else:
                failed += 1
                state = fail_message_job(
                    job_id=job_id,
                    worker_id=current_worker,
                    error=str(exc),
                    error_code=exc.code,
                    retryable=exc.retryable,
                    next_attempt_at=utc_now() + retry_delay(int(job["attempt_count"])),
                )
                if state in {"failed", "dead"}:
                    _update_source_outcome(job, "send_failed", None, str(exc))
        except RequestException as exc:
            failed += 1
            fail_message_job(
                job_id=job_id,
                worker_id=current_worker,
                error=str(exc),
                error_code="network_error",
                retryable=True,
                next_attempt_at=utc_now() + retry_delay(int(job["attempt_count"])),
            )
        except Exception as exc:
            failed += 1
            if provider_accepted:
                mark_delivery_unknown(
                    job_id=job_id,
                    worker_id=current_worker,
                    error=str(exc),
                    error_code="persistence_error",
                )
                _update_source_outcome(job, "delivery_unknown", None, str(exc))
                delivery_unknown += 1
                logger.exception(
                    "Provider accepted message but persistence failed; leaving for reconciliation",
                    extra={"event": "delivery_unknown", "job_id": job_id},
                )
            else:
                fail_message_job(
                    job_id=job_id,
                    worker_id=current_worker,
                    error=str(exc),
                    error_code="unexpected_error",
                    retryable=False,
                    next_attempt_at=utc_now(),
                )
                logger.exception(
                    "Message processing failed",
                    extra={"event": "message_failed", "job_id": job_id},
                )
    result = {
        "claimed": len(jobs),
        "accepted": accepted,
        "failed": failed,
        "delivery_unknown": delivery_unknown,
        "cancelled": cancelled,
    }
    heartbeat_succeeded(name, result)
    return result


def process_website_lead_messages(config: AppConfig, *, limit: int = 50) -> dict[str, int]:
    return process_due_messages(
        config,
        limit=limit,
        allowed_message_kinds=_website_lead_message_kinds(config),
        heartbeat_name="process_website_lead_messages",
    )


def process_website_lead_poolbrain_events(
    config: AppConfig,
    *,
    limit: int = 50,
    event_id: str | None = None,
) -> dict[str, int | str]:
    name = "process_website_lead_poolbrain_events"
    heartbeat_started(name)
    if not config.POOLBRAIN_WEBSITE_LEAD_SYNC_ENABLED:
        result: dict[str, int | str] = {
            "status": "disabled",
            "claimed": 0,
            "completed": 0,
            "failed": 0,
        }
        heartbeat_succeeded(name, result)
        return result

    current_worker = worker_id()
    claimed = claim_inbound_events(
        worker_id=current_worker,
        limit=limit,
        lease_minutes=config.MESSAGE_LEASE_MINUTES,
        provider="website",
        event_type="website.lead.poolbrain_sync",
        external_id=event_id,
    )
    completed = 0
    failed = 0
    client = PoolBrainClient()
    for event in claimed:
        try:
            sync_result = client.sync_website_lead(
                str(event["external_id"]),
                dict(event.get("payload") or {}),
                creation_previously_attempted=bool(
                    dict(event.get("result") or {}).get("creation_attempted")
                ),
            )
            customer_id = _positive_int(sync_result.get("poolbrain_customer_id"))
            complete_inbound_event(
                int(event["id"]),
                current_worker,
                provider_record_id=str(customer_id) if customer_id else None,
                result=sync_result,
            )
            completed += 1
        except Exception as exc:
            failed += 1
            fail_inbound_event(
                int(event["id"]),
                current_worker,
                str(exc),
                utc_now() + retry_delay(int(event["attempt_count"])),
                result={"creation_attempted": True}
                if isinstance(exc, PoolBrainCreatePending)
                else None,
            )
            logger.exception(
                "Website lead PoolBrain synchronization failed",
                extra={
                    "event": "website_lead_poolbrain_sync_failed",
                    "external_id": event["external_id"],
                },
            )
    result = {"claimed": len(claimed), "completed": completed, "failed": failed}
    heartbeat_succeeded(name, result)
    return result


def process_website_leads(config: AppConfig, *, limit: int = 50) -> dict[str, object]:
    return {
        "poolbrain": process_website_lead_poolbrain_events(config, limit=limit),
        "notifications": process_website_lead_messages(config, limit=limit),
    }


def process_website_lead_event(config: AppConfig, *, event_id: str) -> dict[str, object]:
    poolbrain = process_website_lead_poolbrain_events(config, limit=1, event_id=event_id)
    notifications = process_due_messages(
        config,
        limit=2,
        allowed_message_kinds=_website_lead_message_kinds(config),
        idempotency_prefix=f"website-lead:{event_id}:",
        heartbeat_name="dispatch_website_lead_event",
    )
    return {"poolbrain": poolbrain, "notifications": notifications}


def sync_google_reviews(config: AppConfig) -> dict[str, int | str]:
    name = "sync_google_reviews"
    heartbeat_started(name)
    if not config.GOOGLE_SYNC_ENABLED:
        result: dict[str, int | str] = {
            "status": "disabled",
            "imported": 0,
            "matched": 0,
            "candidates": 0,
        }
        heartbeat_succeeded(name, result)
        return result
    account_id = os.getenv("GOOGLE_ACCOUNT_ID", "").strip()
    location_id = os.getenv("GOOGLE_LOCATION_ID", "").strip()
    if not account_id or not location_id:
        error = "GOOGLE_ACCOUNT_ID and GOOGLE_LOCATION_ID are required"
        heartbeat_failed(name, error)
        raise RuntimeError(error)
    imported = matched = candidates = 0
    try:
        client = GoogleBusinessClient()
        for review in client.iter_reviews(account_id, location_id):
            outcome = import_google_review(review)
            imported += 1
            matched += int(outcome["match_status"] == "matched")
            candidates += int(outcome["match_status"] == "candidate")
        result = {"imported": imported, "matched": matched, "candidates": candidates}
        heartbeat_succeeded(name, result)
        return result
    except Exception as exc:
        heartbeat_failed(name, str(exc))
        raise


def sync_outlook_bounces(config: AppConfig) -> dict[str, int | str]:
    name = "sync_outlook_bounces"
    heartbeat_started(name)
    if not config.OUTLOOK_BOUNCE_SYNC_ENABLED:
        result: dict[str, int | str] = {"status": "disabled", "processed": 0, "suppressed": 0}
        heartbeat_succeeded(name, result)
        return result
    known = known_recent_email_destinations()
    processed = suppressed = 0
    try:
        client = MicrosoftGraphClient()
        for message in client.recent_inbox_messages():
            if message.get("isRead"):
                continue
            destination = extract_permanent_bounce(message, known)
            if not destination:
                continue
            message_id = str(message.get("id") or "")
            record_permanent_bounce(destination, message_id)
            if message_id:
                client.mark_message_read(message_id)
            processed += 1
            suppressed += 1
        result = {"processed": processed, "suppressed": suppressed}
        heartbeat_succeeded(name, result)
        return result
    except Exception as exc:
        heartbeat_failed(name, str(exc))
        raise


def backfill_campaign_contacts(config: AppConfig, *, limit: int = 50) -> dict[str, int]:
    name = "backfill_campaign_contacts"
    heartbeat_started(name)
    rows = campaigns_missing_contacts(min(max(limit, 1), 100))
    client = PoolBrainClient()
    checked = updated = failed = 0
    for row in rows:
        checked += 1
        try:
            customer = client.customer(int(row["customer_id"]))
            if not customer:
                failed += 1
                continue
            customer_name, phone, email = _customer_contacts(customer)
            updated += update_campaign_contacts(
                int(row["customer_id"]),
                customer_name=customer_name,
                phone_e164=phone,
                email_normalized=email,
            )
        except Exception:
            failed += 1
            logger.exception(
                "Campaign contact backfill failed",
                extra={"event": "backfill_failed", "external_id": row["customer_id"]},
            )
    result = {"checked": checked, "updated": updated, "failed": failed}
    heartbeat_succeeded(name, result)
    return result


def process_all(config: AppConfig) -> dict[str, Any]:
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    steps = (
        ("recovery", recover_stale_work),
        ("completed_services", lambda: poll_completed_services(config)),
        ("inbound_events", lambda: process_inbound_events(config)),
        ("messages", lambda: process_due_messages(config)),
    )
    for step_name, step in steps:
        try:
            results[step_name] = step()
        except Exception as exc:
            errors[step_name] = type(exc).__name__
            logger.exception(
                "Worker step failed; continuing independent steps",
                extra={"event": "worker_step_failed"},
            )
    results["success"] = not errors
    if errors:
        results["errors"] = errors
    return results


def _process_poolbrain_event(
    event: dict[str, Any],
    client: PoolBrainClient,
    config: AppConfig,
) -> None:
    payload = dict(event.get("payload") or {})
    if payload.get("event") != "alert.triggered":
        return
    outer = payload.get("data") or {}
    jobs = outer.get("data", []) if isinstance(outer, dict) else []
    if not isinstance(jobs, list):
        raise ValueError("PoolBrain alert data is not a list")
    for source_job in jobs:
        if not isinstance(source_job, dict):
            continue
        customer_id = _positive_int(source_job.get("CustomerID"))
        source_job_id = _positive_int(source_job.get("JobID"))
        categories = source_job.get("AlertCategories", [])
        if not isinstance(categories, list):
            continue
        for category in categories:
            reports = category.get("IssueReport", []) if isinstance(category, dict) else []
            if not isinstance(reports, list):
                continue
            for alert in reports:
                if not isinstance(alert, dict):
                    continue
                alert_name = alert.get("AlertName") or alert.get("type")
                if alert_name != "WaterLevelLow":
                    continue
                alert_id = _positive_int(alert.get("alertId"))
                if alert_id is None or customer_id is None:
                    logger.warning(
                        "Skipping PoolBrain alert with invalid identifiers",
                        extra={"event": "invalid_alert", "external_id": event["external_id"]},
                    )
                    continue
                customer = client.customer(customer_id)
                if not customer:
                    set_processed_alert_outcome(
                        alert_id,
                        customer_id,
                        source_job_id,
                        "customer_not_found",
                    )
                    continue
                customer_name, phone, _email = _customer_contacts(customer)
                if not phone:
                    set_processed_alert_outcome(alert_id, customer_id, source_job_id, "no_phone")
                    continue
                if is_suppressed("sms", phone):
                    set_processed_alert_outcome(
                        alert_id, customer_id, source_job_id, "sms_suppressed"
                    )
                    continue
                create_water_alert_job(
                    alert_id=alert_id,
                    customer_id=customer_id,
                    source_job_id=source_job_id,
                    customer_name=customer_name,
                    phone_e164=phone,
                    inbound_event_id=int(event["id"]),
                    scheduled_at=utc_now(),
                    max_attempts=config.MESSAGE_MAX_ATTEMPTS,
                )


def _customer_contacts(customer: dict[str, Any]) -> tuple[str, str | None, str | None]:
    name = str(customer.get("CustomerName") or "Customer").strip() or "Customer"
    phone = None
    email = None
    for key in ("Phone", "PhoneNumber", "ContactPhoneNumber", "MobilePhone"):
        try:
            phone = normalize_us_phone(customer.get(key))
            break
        except InvalidContact:
            continue
    for key in ("Email", "EmailAddress", "ContactEmail", "PrimaryEmail"):
        try:
            email = normalize_email(customer.get(key))
            break
        except InvalidContact:
            continue
    return name, phone, email


def _allowed_message_kinds(config: AppConfig) -> list[str]:
    now = utc_now()
    kinds: list[str] = []
    if within_local_hours(now, config.BUSINESS_TIMEZONE, 6, 19):
        kinds.extend(
            [
                "completed_service_sms",
                "water_level_low_sms",
                "admin_customer_reply_sms",
                "admin_delivery_failure_sms",
            ]
        )
    if within_local_hours(now, config.BUSINESS_TIMEZONE, 9, 19):
        kinds.append("initial_review_sms")
        if config.OUTLOOK_SEND_ENABLED:
            kinds.extend(
                [
                    "next_day_review_email",
                    "saturday_review_email",
                ]
            )
    return kinds


def _website_lead_message_kinds(config: AppConfig) -> list[str]:
    kinds: list[str] = []
    if within_local_hours(utc_now(), config.BUSINESS_TIMEZONE, 6, 19):
        kinds.append("admin_website_lead_sms")
    if config.OUTLOOK_SEND_ENABLED:
        kinds.append("admin_website_lead_email")
    return kinds


def _update_source_outcome(
    job: dict[str, Any],
    status: str,
    provider_message_id: str | None,
    error: str | None = None,
) -> None:
    data = dict(job.get("template_data") or {})
    kind = str(job["message_kind"])
    if kind == "completed_service_sms":
        source_job_id = _positive_int(data.get("source_job_id"))
        if source_job_id:
            set_processed_job_outcome(
                source_job_id,
                _positive_int(job.get("customer_id")),
                status,
                provider_message_id=provider_message_id,
                error=error,
            )
    elif kind == "water_level_low_sms":
        alert_id = _positive_int(data.get("alert_id"))
        if alert_id:
            set_processed_alert_outcome(
                alert_id,
                _positive_int(job.get("customer_id")),
                _positive_int(data.get("source_job_id")),
                status,
                provider_message_id=provider_message_id,
                error=error,
            )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _integer_environment(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value
