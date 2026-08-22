from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, redirect, render_template, request

from app.domain.contact import masked_destination
from app.repositories.admin import cancel_campaign, confirm_campaign, undo_campaign_confirmation
from app.repositories.audit import record_audit_event
from app.repositories.dashboard import dashboard_snapshot
from app.repositories.jobs import retry_dead_or_failed_job
from app.security import admin_identity, csrf_token, require_admin, validate_csrf

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/dashboard")
@require_admin
def dashboard():
    timezone_name = str(current_app.config["BUSINESS_TIMEZONE"])
    snapshot = dashboard_snapshot(timezone_name)
    for row in snapshot["recent_jobs"]:
        row["destination_masked"] = masked_destination(row.pop("destination_normalized", None))
    for row in snapshot["campaigns"]:
        row["phone_masked"] = masked_destination(row.pop("phone_e164", None))
        row["email_masked"] = masked_destination(row.pop("email_normalized", None))
    return render_template(
        "dashboard.html",
        **snapshot,
        csrf_token=csrf_token(),
        identity=admin_identity(),
        auth_mode=current_app.config["ADMIN_AUTH_MODE"],
        format_time=lambda value: _format_time(value, timezone_name),
    )


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
