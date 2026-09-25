import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setenv("PANEL_USERNAME", "admin")
    monkeypatch.setenv("PANEL_PASSWORD", "secret")
    monkeypatch.setenv("PANEL_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("NOTIFY_EMAIL", "depo@example.com")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    monkeypatch.setenv("N8N_INSPECT_WEBHOOK", "")
    monkeypatch.setenv("N8N_APPROVAL_WEBHOOK", "")
    monkeypatch.setenv("N8N_SIGNUP_FORM", "")
    from app.main import create_app

    app = create_app()
    return TestClient(app), app


@pytest.fixture
def client(app_client):
    http, app = app_client
    response = http.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    assert response.status_code == 200
    return http, app
