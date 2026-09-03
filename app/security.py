from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import Response, abort, current_app, redirect, request, session
from twilio.request_validator import RequestValidator

View = TypeVar("View", bound=Callable[..., Any])


def _constant_time_equal(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _basic_auth_valid() -> bool:
    credentials = request.authorization
    if credentials is None:
        return False
    return _constant_time_equal(
        credentials.username,
        current_app.config.get("DASHBOARD_USERNAME"),
    ) and _constant_time_equal(
        credentials.password,
        current_app.config.get("DASHBOARD_PASSWORD"),
    )


def admin_identity() -> str | None:
    if current_app.config.get("ADMIN_AUTH_MODE") == "oidc":
        identity = session.get("admin_email")
        return str(identity) if identity else None
    credentials = request.authorization
    return credentials.username if credentials and _basic_auth_valid() else None


def require_admin(view: View) -> View:  # noqa: UP047
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        authenticated = (
            bool(session.get("admin_email"))
            if current_app.config.get("ADMIN_AUTH_MODE") == "oidc"
            else _basic_auth_valid()
        )
        if not authenticated:
            if current_app.config.get("ADMIN_AUTH_MODE") == "oidc":
                return redirect("/auth/login")
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Tampa VIP Automation"'},
            )
        return view(*args, **kwargs)

    return cast(View, wrapped)


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return str(token)


def validate_csrf() -> None:
    expected = session.get("csrf_token")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not _constant_time_equal(str(provided or ""), str(expected)):
        abort(400, "Invalid CSRF token")


def valid_twilio_request() -> bool:
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    signature = request.headers.get("X-Twilio-Signature")
    base_url = str(current_app.config["PUBLIC_BASE_URL"]).rstrip("/")
    if not auth_token or not signature:
        return False
    path = request.full_path.removesuffix("?")
    validator = RequestValidator(auth_token)
    return bool(validator.validate(base_url + path, request.form, signature))


def valid_poolbrain_webhook() -> bool:
    secret = os.getenv("POOLBRAIN_WEBHOOK_SIGNING_SECRET")
    signature = request.headers.get("X-Webhook-Signature", "").strip()
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        request.get_data(cache=True),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def valid_website_lead_webhook() -> bool:
    secret = os.getenv("WEBSITE_LEAD_WEBHOOK_SECRET")
    signature = request.headers.get("X-Tampa-VIP-Signature", "").strip()
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        request.get_data(cache=True),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
