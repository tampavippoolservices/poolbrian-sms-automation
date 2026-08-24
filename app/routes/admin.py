from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import urlencode
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
