from __future__ import annotations

import base64

from app import create_app
from app.routes import google as google_routes
from app.routes import microsoft as microsoft_routes


def _basic_auth() -> dict[str, str]:
    encoded = base64.b64encode(b"admin:dashboard-password").decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


class _OAuthClient:
    def authorization_url(self, state: str) -> str:
        return f"https://login.example.test/authorize?state={state}"


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


def test_outlook_connect_supports_top_level_get(monkeypatch) -> None:
    monkeypatch.setattr(microsoft_routes, "create_oauth_state", lambda *args: "state-1")
    monkeypatch.setattr(microsoft_routes, "MicrosoftGraphClient", _OAuthClient)
    response = _app(monkeypatch).test_client().get(
        "/microsoft/connect", headers=_basic_auth()
    )
    assert response.status_code == 302
    assert response.location == "https://login.example.test/authorize?state=state-1"


def test_google_connect_supports_top_level_get(monkeypatch) -> None:
    monkeypatch.setattr(google_routes, "create_oauth_state", lambda *args: "state-2")
    monkeypatch.setattr(google_routes, "GoogleBusinessClient", _OAuthClient)
    response = _app(monkeypatch).test_client().get("/google/connect", headers=_basic_auth())
    assert response.status_code == 302
    assert response.location == "https://login.example.test/authorize?state=state-2"
