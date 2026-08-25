from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, redirect, render_template, request
from requests import RequestException

from app.repositories.oauth import consume_oauth_state, create_oauth_state, save_refresh_token
from app.security import admin_identity, require_admin, validate_csrf
from app.services.google import GOOGLE_SCOPE, GoogleApiError, GoogleBusinessClient

logger = logging.getLogger(__name__)
google_bp = Blueprint("google", __name__, url_prefix="/google")


@google_bp.route("/connect", methods=["GET", "POST"])
@require_admin
def connect():
    # Use a top-level GET navigation for OAuth. A POST followed by an external
    # redirect can be blocked by browsers when form-action is restricted to
    # this application. Preserve POST compatibility with CSRF validation.
    if request.method == "POST":
        validate_csrf()
    state = create_oauth_state("google", admin_identity())
    return redirect(GoogleBusinessClient().authorization_url(state))


@google_bp.get("/oauth/callback")
def oauth_callback():
    error = request.args.get("error")
    if error:
        abort(400, f"Google authorization failed: {error}")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or not consume_oauth_state("google", state):
        abort(400, "Invalid or expired Google authorization")
    try:
        payload = GoogleBusinessClient().exchange_code(code)
        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            abort(400, "Google did not provide a refresh token; reconnect with consent.")
        save_refresh_token(
            "google",
            str(refresh_token),
            scopes=str(payload.get("scope") or GOOGLE_SCOPE),
            metadata={"token_type": payload.get("token_type")},
        )
    except RequestException:
        logger.exception("Google OAuth exchange failed", extra={"event": "google_oauth_failed"})
        abort(502, "Google authorization could not be completed")
    return render_template(
        "provider_connected.html",
        provider="Google Business Profile",
        dashboard_url="/admin/dashboard",
    )


@google_bp.get("/test-connection")
@require_admin
def test_connection():
    try:
        accounts = GoogleBusinessClient().list_accounts()
    except (GoogleApiError, RequestException) as exc:
        logger.warning("Google connection test failed: %s", type(exc).__name__)
        return jsonify({"connected": False, "message": "Google API is unavailable"}), 502
    return jsonify(
        {
            "connected": True,
            "account_count": len(accounts),
            "accounts": [
                {
                    "name": account.get("name"),
                    "account_name": account.get("accountName"),
                    "type": account.get("type"),
                }
                for account in accounts
            ],
        }
    )
