from __future__ import annotations

import logging
import os
import re
import uuid

import sentry_sdk
from flask import Flask, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import AppConfig
from app.db import close_engine, init_engine
from app.logging_config import configure_logging


def create_app(config_overrides: dict[str, object] | None = None) -> Flask:
    config = AppConfig.from_environment()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # type: ignore[method-assign]
    app.config.update(config.as_flask_config())
    if config_overrides:
        app.config.update(config_overrides)

    configure_logging(app.config["LOG_LEVEL"])
    if os.getenv("SENTRY_DSN"):
        sentry_sdk.init(
            dsn=os.environ["SENTRY_DSN"],
            environment=str(app.config["APP_ENV"]),
            send_default_pii=False,
            traces_sample_rate=0.05,
        )
    init_engine(app.config["DATABASE_URL"])

    from app.routes.admin import admin_bp
    from app.routes.google import google_bp
    from app.routes.health import health_bp
    from app.routes.microsoft import microsoft_bp
    from app.routes.public import public_bp
    from app.routes.webhooks import legacy_webhook_bp, webhook_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(legacy_webhook_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(google_bp)
    app.register_blueprint(microsoft_bp)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
        )
        if app.config.get("APP_ENV") == "production":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        sensitive_response_prefixes = (
            "/admin",
            "/auth",
            "/google/oauth",
            "/microsoft/oauth",
            "/review/",
            "/unsubscribe/",
        )
        if request.path.startswith(sensitive_response_prefixes):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = str(getattr(g, "request_id", ""))
        if not request.path.startswith("/health"):
            logging.getLogger("app.access").info(
                "Request completed",
                extra={
                    "event": "request_completed",
                    "method": request.method,
                    "route": request.url_rule.rule if request.url_rule else "unmatched",
                    "status_code": response.status_code,
                    "request_id": g.request_id,
                },
            )
        return response

    @app.before_request
    def assign_request_id() -> None:
        supplied = request.headers.get("X-Request-ID", "")
        g.request_id = (
            supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied) else uuid.uuid4().hex
        )

    @app.errorhandler(500)
    def internal_error(error):
        logging.getLogger(__name__).exception(
            "Unhandled request error",
            extra={"event": "request_failed"},
        )
        return jsonify({"error": "Internal server error", "request_id": g.request_id}), 500

    app.teardown_appcontext(lambda _error: None)
    return app


__all__ = ["close_engine", "create_app"]
