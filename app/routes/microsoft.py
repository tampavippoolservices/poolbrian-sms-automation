from __future__ import annotations

import os

from flask import Blueprint, abort, current_app, redirect, render_template, request, session

from app.repositories.oauth import consume_oauth_state, create_oauth_state, save_refresh_token
from app.security import admin_identity, csrf_token, require_admin, validate_csrf
from app.services.microsoft import MICROSOFT_SCOPES, MicrosoftGraphClient

microsoft_bp = Blueprint("microsoft", __name__)
ADMIN_SCOPES = "openid profile email User.Read"


@microsoft_bp.get("/auth/login")
def admin_login():
    if current_app.config.get("ADMIN_AUTH_MODE") != "oidc":
        return redirect("/admin/dashboard")
    return render_template("login.html", csrf_token=csrf_token())


@microsoft_bp.post("/auth/login")
def admin_login_start():
    if current_app.config.get("ADMIN_AUTH_MODE") != "oidc":
        return redirect("/admin/dashboard")
    validate_csrf()
    redirect_uri = os.getenv("MICROSOFT_ADMIN_REDIRECT_URI", "").strip()
    if not redirect_uri:
        abort(503, "MICROSOFT_ADMIN_REDIRECT_URI is not configured")
    state = create_oauth_state("microsoft_admin", None)
    return redirect(
        MicrosoftGraphClient().authorization_url(
            state,
            redirect_uri=redirect_uri,
            scopes=ADMIN_SCOPES,
        )
    )


@microsoft_bp.get("/auth/callback")
def admin_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    redirect_uri = os.getenv("MICROSOFT_ADMIN_REDIRECT_URI", "").strip()
    if not code or not state or not consume_oauth_state("microsoft_admin", state):
        abort(400, "Invalid or expired Microsoft sign-in")
    client = MicrosoftGraphClient()
    token_payload = client.exchange_code(
        code,
        redirect_uri=redirect_uri,
        scopes=ADMIN_SCOPES,
    )
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        abort(400, "Microsoft did not provide an access token")
    user = client.current_user(access_token)
    email = str(user.get("mail") or user.get("userPrincipalName") or "").casefold()
    if not _admin_email_allowed(email):
        session.clear()
        abort(403, "This Microsoft account is not authorized")
    session.clear()
    session.permanent = True
    session["admin_email"] = email
    session["admin_name"] = str(user.get("displayName") or email)
    return redirect("/admin/dashboard")


@microsoft_bp.post("/auth/logout")
@require_admin
def admin_logout():
    validate_csrf()
    session.clear()
    return redirect("/auth/login")


@microsoft_bp.route("/microsoft/connect", methods=["GET", "POST"])
@require_admin
def outlook_connect():
    # OAuth begins with a top-level GET navigation so browsers do not apply the
    # dashboard's form-action CSP to the cross-origin Microsoft redirect. Keep
    # POST support for older dashboard versions and validate those submissions.
    if request.method == "POST":
        validate_csrf()
    state = create_oauth_state("microsoft", admin_identity())
    return redirect(MicrosoftGraphClient().authorization_url(state))


@microsoft_bp.get("/microsoft/oauth/callback")
def outlook_callback():
    error = request.args.get("error")
    if error:
        abort(400, f"Microsoft authorization failed: {error}")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state or not consume_oauth_state("microsoft", state):
        abort(400, "Invalid or expired Microsoft authorization")
    payload = MicrosoftGraphClient().exchange_code(code)
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        abort(400, "Microsoft did not provide a refresh token")
    save_refresh_token(
        "microsoft",
        str(refresh_token),
        scopes=str(payload.get("scope") or MICROSOFT_SCOPES),
        metadata={"token_type": payload.get("token_type")},
    )
    return render_template(
        "provider_connected.html",
        provider="Microsoft Outlook",
        dashboard_url="/admin/dashboard",
    )


@microsoft_bp.get("/microsoft/test-connection")
@require_admin
def outlook_test_connection():
    client = MicrosoftGraphClient()
    user = client.current_user(client.access_token())
    return {
        "connected": True,
        "mailbox": user.get("mail") or user.get("userPrincipalName"),
        "display_name": user.get("displayName"),
    }


def _admin_email_allowed(email: str) -> bool:
    allowed_emails = {
        value.strip().casefold()
        for value in str(current_app.config.get("ADMIN_ALLOWED_EMAILS") or "").split(",")
        if value.strip()
    }
    allowed_domain = (
        str(current_app.config.get("ADMIN_ALLOWED_DOMAIN") or "").casefold().lstrip("@")
    )
    return email in allowed_emails or bool(allowed_domain and email.endswith("@" + allowed_domain))
