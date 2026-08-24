from __future__ import annotations

import base64
from typing import Any

from app import create_app
from app.routes import admin as admin_routes


def _basic_auth() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:dashboard-password").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _page(page: int, search: str, *, total: int = 80) -> dict[str, int | str]:
    pages = max(1, (total + 24) // 25)
    current = min(page, pages)
    return {
        "page": current,
        "pages": pages,
        "page_size": 25,
        "total": total,
        "first_item": (current - 1) * 25 + 1 if total else 0,
        "last_item": min(current * 25, total),
        "search": search,
    }


def test_dashboard_has_independent_search_and_pagination(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid/unused")
    captured: dict[str, Any] = {}

    def fake_snapshot(timezone_name: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"timezone_name": timezone_name, **kwargs})
        return {
            "metrics": {
                "messages_today": 0,
                "delivered_today": 0,
                "failed_today": 0,
                "queued": 0,
                "due": 0,
                "delivery_unknown": 0,
                "oldest_due_minutes": 0,
            },
            "campaign_metrics": {
                "active": 0,
                "clicked": 0,
                "confirmed": 0,
                "replied": 0,
                "cancelled": 0,
            },
            "communication": {"sms_suppressed": 0, "email_suppressed": 0},
            "recent_jobs": [],
            "campaigns": [],
            "reviews": [],
            "heartbeats": [],
            "pagination": {
                "jobs": _page(kwargs["jobs_page"], kwargs["jobs_search"]),
                "campaigns": _page(kwargs["campaigns_page"], kwargs["campaigns_search"], total=0),
                "reviews": _page(kwargs["reviews_page"], kwargs["reviews_search"], total=2),
                "workers": _page(kwargs["workers_page"], kwargs["workers_search"], total=4),
            },
        }

    monkeypatch.setattr(admin_routes, "dashboard_snapshot", fake_snapshot)
    app = create_app(
        {
            "TESTING": True,
            "DASHBOARD_USERNAME": "admin",
            "DASHBOARD_PASSWORD": "dashboard-password",
        }
    )

    response = app.test_client().get(
        "/admin/dashboard?jobs_page=3&jobs_q=failed&campaigns_page=invalid&"
        "campaigns_q=Smith&reviews_page=-2&workers_q=heartbeat",
        headers=_basic_auth(),
    )

    assert response.status_code == 200
    assert captured["jobs_page"] == 3
    assert captured["jobs_search"] == "failed"
    assert captured["campaigns_page"] == 1
    assert captured["campaigns_search"] == "Smith"
    assert captured["reviews_page"] == 1
    assert captured["workers_search"] == "heartbeat"
    assert b'name="jobs_q" value="failed"' in response.data
    assert b'name="campaigns_q" value="Smith"' in response.data
    assert b'name="reviews_q"' in response.data
    assert b'name="workers_q" value="heartbeat"' in response.data
    assert b"Showing 51\xe2\x80\x9375 of 80" in response.data
    assert b"Page 3 of 4" in response.data
    assert b"No matching entries" in response.data
