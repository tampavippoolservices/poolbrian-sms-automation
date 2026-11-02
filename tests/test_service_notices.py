from __future__ import annotations

import base64
from datetime import UTC, datetime

from app import create_app
from app.routes import admin as admin_routes


def _basic_auth() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:dashboard-password").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _app(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid/unused")
    return create_app(
        {
            "TESTING": True,
            "DASHBOARD_USERNAME": "admin",
            "DASHBOARD_PASSWORD": "dashboard-password",
        }
    )


def test_service_notice_page_lists_only_repository_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "service_notice_candidate_page",
        lambda _search, page: {
            "items": [
                {
                    "customer_id": 701,
                    "customer_name": "Eligible Customer",
                    "phone_e164": "+18135550101",
                    "preference_source": "twilio",
                    "preference_reason": "customer_opt_in",
                }
            ],
            "page": page,
            "pages": 3,
            "page_size": 50,
            "total": 125,
            "first_item": 1,
            "last_item": 50,
            "search": _search,
        },
    )
    monkeypatch.setattr(admin_routes, "recent_service_notice_campaigns", lambda: [])

    response = _app(monkeypatch).test_client().get("/admin/service-notices", headers=_basic_auth())

    assert response.status_code == 200
    assert b"Eligible Customer" in response.data
    assert b"***-***-0101" in response.data
    assert b"Operational Service Notices" in response.data
    assert b"Preview selected notice" in response.data
    assert b"Showing 1\xe2\x80\x9350 of 125 eligible customers" in response.data
    assert b"Page 1 of 3" in response.data
    assert b"service-notices.js" in response.data


def test_service_notice_candidate_data_is_paginated_and_masks_phone(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "service_notice_candidate_page",
        lambda search, page: {
            "items": [
                {
                    "customer_id": 801,
                    "customer_name": "Search Result",
                    "phone_e164": "+18135550199",
                    "preference_source": None,
                    "preference_reason": None,
                }
            ],
            "page": page,
            "pages": 4,
            "page_size": 50,
            "total": 151,
            "first_item": 51,
            "last_item": 100,
            "search": search,
        },
    )

    response = (
        _app(monkeypatch)
        .test_client()
        .get(
            "/admin/service-notices/candidates?q=Search&page=2",
            headers=_basic_auth(),
        )
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["page"] == 2
    assert payload["total"] == 151
    assert payload["search"] == "Search"
    assert payload["items"][0]["phone_masked"] == "***-***-0199"
    assert "phone_e164" not in payload["items"][0]


def test_preview_does_not_enqueue_messages(monkeypatch) -> None:
    recipients = [
        {
            "customer_id": 701,
            "customer_name": "Eligible Customer",
            "phone_e164": "+18135550101",
        }
    ]
    monkeypatch.setattr(
        admin_routes,
        "eligible_service_notice_recipients",
        lambda _ids: recipients,
    )
    app = _app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "valid-token"

    response = client.post(
        "/admin/service-notices/preview",
        headers=_basic_auth(),
        data={
            "csrf_token": "valid-token",
            "name": "Storm schedule",
            "message_body": "Friday service will move to Saturday.",
            "consent_basis": "poolbrain_service_agreement",
            "consent_evidence": "Operational SMS authorization in service agreement.",
            "customer_ids": "701",
        },
    )

    assert response.status_code == 200
    assert b"Nothing has been queued yet" in response.data
    assert b"Friday service will move to Saturday" in response.data
    assert b"Reply STOP to unsubscribe" in response.data


def test_confirmed_service_notice_is_queued_and_audited(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {
            "campaign_id": 42,
            "recipient_count": 1,
            "scheduled_at": datetime(2026, 11, 2, 16, 0, tzinfo=UTC),
        }

    monkeypatch.setattr(admin_routes, "enqueue_service_notice_campaign", fake_enqueue)
    monkeypatch.setattr(admin_routes, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        admin_routes,
        "utc_now",
        lambda: datetime(2026, 11, 2, 16, 0, tzinfo=UTC),
    )
    app = _app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "valid-token"

    response = client.post(
        "/admin/service-notices/send",
        headers=_basic_auth(),
        data={
            "csrf_token": "valid-token",
            "confirm": "yes",
            "name": "Storm schedule",
            "message_body": "Friday service will move to Saturday.",
            "consent_basis": "poolbrain_service_agreement",
            "consent_evidence": "Operational SMS authorization in service agreement.",
            "customer_ids": "701",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/service-notices?queued=42")
    assert captured["customer_ids"] == [701]
    assert captured["created_by"] == "admin"


def test_service_notice_requires_final_confirmation(monkeypatch) -> None:
    app = _app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "valid-token"
    response = client.post(
        "/admin/service-notices/send",
        headers=_basic_auth(),
        data={"csrf_token": "valid-token"},
    )
    assert response.status_code == 400
