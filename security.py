import hashlib
import hmac
import os

from flask import Response, request
from twilio.request_validator import RequestValidator


def check_dashboard_auth():
    username = os.environ.get(
        "DASHBOARD_USERNAME"
    )
    password = os.environ.get(
        "DASHBOARD_PASSWORD"
    )

    auth = request.authorization

    return (
        auth is not None
        and auth.username == username
        and auth.password == password
    )


def require_dashboard_auth():
    if check_dashboard_auth():
        return None

    return Response(
        "Login required",
        401,
        {
            "WWW-Authenticate": (
                'Basic realm="Tampa VIP SMS Dashboard"'
            )
        }
    )


def valid_twilio_request():
    auth_token = os.environ.get(
        "TWILIO_AUTH_TOKEN"
    )
    signature = request.headers.get(
        "X-Twilio-Signature",
        ""
    )
    public_base_url = os.environ.get(
        "PUBLIC_BASE_URL"
    )

    if (
        not auth_token
        or not signature
        or not public_base_url
    ):
        return False

    requested_path = request.full_path

    if requested_path.endswith("?"):
        requested_path = requested_path[:-1]

    validation_url = (
        public_base_url.rstrip("/")
        + requested_path
    )

    validator = RequestValidator(auth_token)

    return validator.validate(
        validation_url,
        request.form,
        signature
    )


def valid_poolbrain_webhook():
    signing_secret = os.environ.get(
        "POOLBRAIN_WEBHOOK_SIGNING_SECRET"
    )
    received_signature = request.headers.get(
        "X-Webhook-Signature",
        ""
    )

    if not signing_secret or not received_signature:
        return False

    raw_request_body = request.get_data()

    expected_signature = hmac.new(
        signing_secret.encode("utf-8"),
        raw_request_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        received_signature.strip()
    )
