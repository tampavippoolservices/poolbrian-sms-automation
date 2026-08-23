from app import create_app


def test_liveness_and_security_headers(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid/unused")
    app = create_app({"TESTING": True})
    response = app.test_client().get("/health/live")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Request-ID"]

    invalid_id = app.test_client().get(
        "/health/live", headers={"X-Request-ID": "spaces are not allowed"}
    )
    assert invalid_id.headers["X-Request-ID"] != "spaces are not allowed"

    valid_id = app.test_client().get("/health/live", headers={"X-Request-ID": "request-123"})
    assert valid_id.headers["X-Request-ID"] == "request-123"

    token_page = app.test_client().get("/unsubscribe/" + "x" * 32)
    assert token_page.status_code == 200
    assert token_page.headers["Cache-Control"] == "no-store"


def test_brand_assets_are_served(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid/unused")
    app = create_app({"TESTING": True})
    client = app.test_client()

    stylesheet = client.get("/static/css/brand.css")
    primary_logo = client.get("/static/images/tampa-vip-logo.jpeg")
    badge_logo = client.get("/static/images/tampa-vip-logo-badge.jpeg")
    favicon = client.get("/static/images/favicon.jpeg")

    assert stylesheet.status_code == 200
    assert stylesheet.mimetype == "text/css"
    assert primary_logo.status_code == 200
    assert primary_logo.mimetype == "image/jpeg"
    assert badge_logo.status_code == 200
    assert badge_logo.mimetype == "image/jpeg"
    assert favicon.status_code == 200
    assert favicon.mimetype == "image/jpeg"


def test_oidc_login_page_uses_tampa_vip_branding(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid/unused")
    app = create_app({"TESTING": True, "ADMIN_AUTH_MODE": "oidc"})

    response = app.test_client().get("/auth/login")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert b"Tampa VIP Pool Services" in response.data
    assert b"Continue with Microsoft" in response.data
    assert b"/static/css/brand.css" in response.data
