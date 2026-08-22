from __future__ import annotations

from flask import Blueprint, jsonify

from app.db import database_ready

health_bp = Blueprint("health", __name__)


@health_bp.get("/")
def home():
    return jsonify({"service": "tampa-vip-automation", "status": "running"})


@health_bp.get("/health/live")
def live():
    return jsonify({"status": "ok"})


@health_bp.get("/health/ready")
def ready():
    if not database_ready():
        return jsonify({"status": "not_ready", "database": False}), 503
    return jsonify({"status": "ready", "database": True})
