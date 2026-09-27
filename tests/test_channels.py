from app.channels import ChannelError
from app.models import IncomingLine


def _member(http):
    signup = http.post(
        "/api/register",
        json={
            "first_name": "Deniz",
            "last_name": "Ak",
            "email": "deniz@ornek.com",
            "company_name": "Ak",
            "password": "gizli123",
        },
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "deniz@ornek.com", "password": "gizli123"})


def test_trendyol_orders_land_for_that_member_only(client, monkeypatch):
    http, _app = client
    _member(http)

    def fake(secrets):
        assert secrets["api_secret"] == "gizli-anahtar"
        return [
            IncomingLine(
                source="Trendyol",
                order_id="TY-9",
                product_name="Kalem",
                quantity=1,
                external_key="trendyol|TY-9|KALEM",
                sku="KALEM",
            )
        ]

    monkeypatch.setattr("app.service.pull_trendyol", fake)
    saved = http.post(
        "/api/channels/trendyol",
        json={"seller_id": "111", "api_key": "anahtar", "api_secret": "gizli-anahtar"},
    )
    assert saved.status_code == 200
    trendyol = next(row for row in http.get("/api/channels").json()["data"]["rows"] if row["code"] == "trendyol")
    assert trendyol["connected"] is True
    assert "api_secret" not in trendyol
    synced = http.post("/api/channels/sync")
    assert synced.status_code == 200
    assert synced.json()["data"]["imported"] == 1
    assert http.get("/api/orders").json()["data"]["rows"][0]["order_id"] == "TY-9"
    assert http.post("/api/channels/sync").json()["data"]["imported"] == 0

    http.post("/api/logout")
    http.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    assert all(row["order_id"] != "TY-9" for row in http.get("/api/orders").json()["data"]["rows"])


def test_failed_trendyol_sync_asks_n8n_to_mail(client, monkeypatch):
    http, app = client
    _member(http)
    sent = []

    def fail(_secrets):
        raise ChannelError("Trendyol bilgileri eksik")

    def capture(url, json, timeout):
        sent.append((url, json))

        class Response:
            status_code = 200

        return Response()

    monkeypatch.setattr("app.service.pull_trendyol", fail)
    monkeypatch.setattr("app.service.httpx.post", capture)
    app.state.settings.channel_error_webhook = "http://n8n.test/kanal-hata"
    http.post("/api/channels/trendyol", json={"seller_id": "111", "api_key": "anahtar", "api_secret": "gizli"})
    synced = http.post("/api/channels/sync")
    assert synced.status_code == 502
    assert sent[-1][0] == "http://n8n.test/kanal-hata"
    assert sent[-1][1]["to"] == "deniz@ornek.com"
    assert sent[-1][1]["subject"] == "Kanal bağlantısı koptu"


def test_daily_summary_includes_pending_order(client):
    http, _app = client
    _member(http)
    intake = http.get("/api/me").json()["data"]["user"]["intake_key"]
    created = http.post(
        f"/api/webhooks/in/{intake}",
        json={"source": "n11", "order_id": "N11-1", "product_name": "Kalem", "quantity": 2},
    )
    assert created.status_code == 201
    summary = http.get("/api/n8n/summary")
    assert summary.status_code == 200
    rows = summary.json()["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["to"] == "deniz@ornek.com"
    assert rows[0]["subject"] == "Günlük depo özeti"
    assert "Bekleyen sipariş: 1" in rows[0]["body"]
    assert "N11-1" in rows[0]["body"]


def test_channel_rejects_missing_fields(client):
    http, _app = client
    _member(http)
    missing = http.post("/api/channels/trendyol", json={"seller_id": "111"})
    assert missing.status_code == 400
    amazon = http.post("/api/channels/amazon", json={"seller_id": "A1B2C3"})
    assert amazon.status_code == 200
    assert amazon.json()["code"] == "stored"
    stored = http.post("/api/channels/hepsiburada", json={"merchant_id": "m1", "service_key": "k1"})
    assert stored.status_code == 200
    assert stored.json()["code"] == "stored"
