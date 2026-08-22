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
