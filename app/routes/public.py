from __future__ import annotations

import os

from flask import Blueprint, abort, redirect, render_template

from app.repositories.campaigns import mark_campaign_clicked
from app.repositories.unsubscribe import consume_unsubscribe_token
from app.security import csrf_token, validate_csrf

public_bp = Blueprint("public", __name__)


@public_bp.get("/dashboard")
def legacy_dashboard_redirect():
    return redirect("/admin/dashboard", code=302)


@public_bp.get("/review/<review_token>")
def review_redirect(review_token: str):
    if len(review_token) < 24 or len(review_token) > 128:
        abort(404)
    campaign_id = mark_campaign_clicked(review_token)
    if campaign_id is None:
        abort(404, "This review link is invalid.")
    review_url = os.getenv("GOOGLE_REVIEW_URL", "").strip()
    if not review_url:
        abort(503, "The Google review page is temporarily unavailable.")
    response = redirect(review_url, code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@public_bp.get("/unsubscribe/<token>")
def unsubscribe_confirmation(token: str):
    if len(token) < 24 or len(token) > 128:
        abort(404)
    return render_template(
        "unsubscribe.html", token=token, csrf_token=csrf_token(), completed=False
    )


@public_bp.post("/unsubscribe/<token>")
def unsubscribe(token: str):
    validate_csrf()
    result = consume_unsubscribe_token(token)
    if result is None:
        abort(404, "This unsubscribe link is invalid.")
    return render_template("unsubscribe.html", token=token, csrf_token=csrf_token(), completed=True)
