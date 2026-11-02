from __future__ import annotations

import uuid
from datetime import datetime
from typing import TypedDict
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request

from app.domain.contact import masked_destination
from app.domain.sms import sms_segment_count
from app.messages import render_message
from app.repositories.admin import cancel_campaign, confirm_campaign, undo_campaign_confirmation
from app.repositories.audit import record_audit_event
from app.repositories.dashboard import dashboard_snapshot
from app.repositories.jobs import retry_dead_or_failed_job
from app.repositories.service_notices import (
    MAX_SERVICE_NOTICE_RECIPIENTS,
    cancel_service_notice_campaign,
    eligible_service_notice_recipients,
    enqueue_service_notice_campaign,
    recent_service_notice_campaigns,
    service_notice_candidate_page,
)
from app.security import admin_identity, csrf_token, require_admin, validate_csrf
from app.time_utils import next_service_notice_at, utc_now

_SERVICE_NOTICE_CONSENT_BASES = {
    "poolbrain_service_agreement": "PoolBrain service agreement",
    "written_customer_consent": "Written customer consent",
    "recorded_customer_consent": "Recorded phone or in-person consent",
    "customer_requested_update": "Customer requested this update",
}


class ServiceNoticeForm(TypedDict):
    name: str
    message_body: str
    consent_basis: str
    consent_evidence: str
    customer_ids: list[int]


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/dashboard")
@require_admin
def dashboard():
    timezone_name = str(current_app.config["BUSINESS_TIMEZONE"])
    filters: dict[str, str | int] = {
        "jobs_page": _page_argument("jobs_page"),
        "jobs_q": _search_argument("jobs_q"),
        "campaigns_page": _page_argument("campaigns_page"),
        "campaigns_q": _search_argument("campaigns_q"),
        "reviews_page": _page_argument("reviews_page"),
        "reviews_q": _search_argument("reviews_q"),
        "workers_page": _page_argument("workers_page"),
        "workers_q": _search_argument("workers_q"),
    }
    snapshot = dashboard_snapshot(
        timezone_name,
        jobs_page=int(filters["jobs_page"]),
        jobs_search=str(filters["jobs_q"]),
        campaigns_page=int(filters["campaigns_page"]),
        campaigns_search=str(filters["campaigns_q"]),
        reviews_page=int(filters["reviews_page"]),
        reviews_search=str(filters["reviews_q"]),
        workers_page=int(filters["workers_page"]),
        workers_search=str(filters["workers_q"]),
    )
    for row in snapshot["recent_jobs"]:
        row["destination_masked"] = masked_destination(row.pop("destination_normalized", None))
    for row in snapshot["campaigns"]:
        row["phone_masked"] = masked_destination(row.pop("phone_e164", None))
        row["email_masked"] = masked_destination(row.pop("email_normalized", None))
    table_parameters = {
        "jobs": ("jobs_page", "jobs_q", "message-jobs-heading"),
        "campaigns": ("campaigns_page", "campaigns_q", "campaigns-heading"),
        "reviews": ("reviews_page", "reviews_q", "google-reviews-heading"),
        "workers": ("workers_page", "workers_q", "worker-health-heading"),
    }
    for table_name, (page_name, query_name, anchor) in table_parameters.items():
        page_data = snapshot["pagination"][table_name]
        page = int(page_data["page"])
        pages = int(page_data["pages"])
        page_data.update(
            {
                "page_name": page_name,
                "query_name": query_name,
                "anchor": anchor,
                "first_url": _dashboard_page_url(filters, page_name, 1, anchor),
                "previous_url": (
                    _dashboard_page_url(filters, page_name, page - 1, anchor) if page > 1 else None
                ),
                "next_url": (
                    _dashboard_page_url(filters, page_name, page + 1, anchor)
                    if page < pages
                    else None
                ),
                "last_url": _dashboard_page_url(filters, page_name, pages, anchor),
                "clear_url": _dashboard_page_url({**filters, query_name: ""}, page_name, 1, anchor),
            }
        )
    return render_template(
        "dashboard.html",
        **snapshot,
        dashboard_filters=filters,
        csrf_token=csrf_token(),
        identity=admin_identity(),
        auth_mode=current_app.config["ADMIN_AUTH_MODE"],
        format_time=lambda value: _format_time(value, timezone_name),
    )


def _page_argument(name: str) -> int:
    try:
        return min(max(int(request.args.get(name, "1")), 1), 1_000_000)
    except (TypeError, ValueError):
        return 1


def _search_argument(name: str) -> str:
    return (request.args.get(name) or "").strip()[:100]


def _dashboard_page_url(
    filters: dict[str, str | int],
    page_name: str,
    page: int,
    anchor: str,
) -> str:
    query = {**filters, page_name: page}
    return f"/admin/dashboard?{urlencode(query)}#{anchor}"


def _service_notice_page_url(search: str, page: int) -> str:
    return f"/admin/service-notices?{urlencode({'q': search, 'page': page})}#recipient-heading"


def _mask_service_notice_candidate_phones(candidates: list[dict]) -> None:
    for candidate in candidates:
        candidate["phone_masked"] = masked_destination(candidate.pop("phone_e164", None))


@admin_bp.post("/campaigns/<int:campaign_id>/confirm")
@require_admin
def confirm(campaign_id: int):
    validate_csrf()
    reviewer_name = (request.form.get("reviewer_name") or "").strip()
    google_review_id = (request.form.get("google_review_id") or "").strip() or None
    if not reviewer_name or len(reviewer_name) > 120:
        abort(400, "Reviewer name is required and must be no more than 120 characters")
    result = confirm_campaign(campaign_id, reviewer_name, google_review_id=google_review_id)
    if not result:
        abort(404)
    _audit("campaign_confirmed", "review_campaign", campaign_id, result[0], result[1])
    return redirect("/admin/dashboard")


@admin_bp.post("/campaigns/<int:campaign_id>/undo-confirmation")
@require_admin
def undo_confirmation(campaign_id: int):
    validate_csrf()
    result = undo_campaign_confirmation(campaign_id)
    if not result:
        abort(404)
    _audit("campaign_confirmation_undone", "review_campaign", campaign_id, result[0], result[1])
    return redirect("/admin/dashboard")


@admin_bp.post("/campaigns/<int:campaign_id>/cancel")
@require_admin
def cancel(campaign_id: int):
    validate_csrf()
    reason = (request.form.get("reason") or "manual cancellation").strip()[:250]
    if not cancel_campaign(campaign_id, reason):
        abort(409, "Only active campaigns can be cancelled")
    _audit("campaign_cancelled", "review_campaign", campaign_id, None, {"reason": reason})
    return redirect("/admin/dashboard")


@admin_bp.post("/message-jobs/<int:job_id>/retry")
@require_admin
def retry_job(job_id: int):
    validate_csrf()
    if not retry_dead_or_failed_job(job_id):
        abort(409, "Only failed or dead jobs can be retried")
    _audit("message_job_retried", "message_job", job_id, None, {"status": "queued"})
    return redirect("/admin/dashboard")


@admin_bp.get("/service-notices")
@require_admin
def service_notices():
    search = _search_argument("q")
    candidate_page = service_notice_candidate_page(
        search,
        page=_page_argument("page"),
    )
    _mask_service_notice_candidate_phones(candidate_page["items"])
    page = int(candidate_page["page"])
    pages = int(candidate_page["pages"])
    candidate_page.update(
        {
            "first_url": _service_notice_page_url(search, 1),
            "previous_url": _service_notice_page_url(search, page - 1) if page > 1 else None,
            "next_url": _service_notice_page_url(search, page + 1) if page < pages else None,
            "last_url": _service_notice_page_url(search, pages),
            "clear_url": _service_notice_page_url("", 1),
        }
    )
    return render_template(
        "service_notices.html",
        candidates=candidate_page["items"],
        candidate_pagination=candidate_page,
        recent_campaigns=recent_service_notice_campaigns(),
        search=search,
        queued_campaign_id=request.args.get("queued", ""),
        consent_bases=_SERVICE_NOTICE_CONSENT_BASES,
        max_recipients=MAX_SERVICE_NOTICE_RECIPIENTS,
        csrf_token=csrf_token(),
        identity=admin_identity(),
        auth_mode=current_app.config["ADMIN_AUTH_MODE"],
        format_time=lambda value: _format_time(value, str(current_app.config["BUSINESS_TIMEZONE"])),
    )


@admin_bp.get("/service-notices/candidates")
@require_admin
def service_notice_candidate_data():
    candidate_page = service_notice_candidate_page(
        _search_argument("q"),
        page=_page_argument("page"),
    )
    _mask_service_notice_candidate_phones(candidate_page["items"])
    return jsonify(candidate_page)


@admin_bp.post("/service-notices/preview")
@require_admin
def preview_service_notice():
    validate_csrf()
    form = _service_notice_form()
    recipients = eligible_service_notice_recipients(form["customer_ids"])
    if not recipients:
        abort(400, "Select at least one currently eligible customer")
    for recipient in recipients:
        recipient["phone_masked"] = masked_destination(recipient.pop("phone_e164", None))
    preview = render_message(
        template_key="service_notice_sms",
        data={"message_body": form["message_body"]},
        public_base_url=str(current_app.config["PUBLIC_BASE_URL"]),
    )
    return render_template(
        "service_notice_preview.html",
        form=form,
        recipients=recipients,
        preview_text=preview.text,
        preview_character_count=len(preview.text),
        preview_segment_count=sms_segment_count(preview.text),
        excluded_count=len(form["customer_ids"]) - len(recipients),
        consent_label=_SERVICE_NOTICE_CONSENT_BASES[form["consent_basis"]],
        csrf_token=csrf_token(),
    )


@admin_bp.post("/service-notices/send")
@require_admin
def send_service_notice():
    validate_csrf()
    if request.form.get("confirm") != "yes":
        abort(400, "Final confirmation is required")
    form = _service_notice_form()
    scheduled_at = next_service_notice_at(
        utc_now(),
        timezone_name=str(current_app.config["BUSINESS_TIMEZONE"]),
    )
    try:
        result = enqueue_service_notice_campaign(
            name=form["name"],
            message_body=form["message_body"],
            customer_ids=form["customer_ids"],
            consent_basis=form["consent_basis"],
            consent_evidence=form["consent_evidence"],
            scheduled_at=scheduled_at,
            created_by=admin_identity() or "unknown-admin",
            max_attempts=int(current_app.config["MESSAGE_MAX_ATTEMPTS"]),
        )
    except ValueError as exc:
        abort(409, str(exc))
    _audit(
        "service_notice_queued",
        "service_notice_campaign",
        int(result["campaign_id"]),
        None,
        {
            "recipient_count": result["recipient_count"],
            "scheduled_at": result["scheduled_at"],
            "consent_basis": form["consent_basis"],
        },
    )
    return redirect(f"/admin/service-notices?queued={result['campaign_id']}")


@admin_bp.post("/service-notices/<int:campaign_id>/cancel")
@require_admin
def cancel_service_notice(campaign_id: int):
    validate_csrf()
    result = cancel_service_notice_campaign(campaign_id)
    if result is None:
        abort(409, "This campaign is already cancelled or does not exist")
    _audit(
        "service_notice_cancelled",
        "service_notice_campaign",
        campaign_id,
        None,
        result,
    )
    return redirect("/admin/service-notices")


def _service_notice_form() -> ServiceNoticeForm:
    name = (request.form.get("name") or "").strip()
    message_body = (request.form.get("message_body") or "").strip()
    consent_basis = (request.form.get("consent_basis") or "").strip()
    consent_evidence = (request.form.get("consent_evidence") or "").strip()
    customer_ids: list[int] = []
    for raw_value in request.form.getlist("customer_ids"):
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            customer_ids.append(value)
    customer_ids = sorted(set(customer_ids))
    if not name or len(name) > 100:
        abort(400, "Campaign name is required and must be no more than 100 characters")
    if not message_body or len(message_body) > 420:
        abort(400, "Message is required and must be no more than 420 characters")
    if consent_basis not in _SERVICE_NOTICE_CONSENT_BASES:
        abort(400, "Select a valid consent basis")
    if not consent_evidence or len(consent_evidence) > 500:
        abort(400, "Consent evidence is required and must be no more than 500 characters")
    if not customer_ids or len(customer_ids) > MAX_SERVICE_NOTICE_RECIPIENTS:
        abort(400, f"Select between 1 and {MAX_SERVICE_NOTICE_RECIPIENTS} customers")
    return {
        "name": name,
        "message_body": message_body,
        "consent_basis": consent_basis,
        "consent_evidence": consent_evidence,
        "customer_ids": customer_ids,
    }


def _audit(
    action: str,
    entity_type: str,
    entity_id: int,
    before,
    after,
) -> None:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    record_audit_event(
        actor=admin_identity() or "unknown-admin",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=before,
        after=after,
        request_id=request_id,
        ip_address=request.remote_addr,
    )


def _format_time(value, timezone_name: str) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo(timezone_name)).strftime("%b %d, %Y %I:%M %p")
    return str(value)
