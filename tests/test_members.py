def test_member_cannot_login_until_admin_approves(client, monkeypatch):
    http, app = client
    calls = []

    def capture(url, json, timeout):
        calls.append((url, json))

        class Response:
            status_code = 200

        return Response()

    monkeypatch.setattr("app.service.httpx.post", capture)
    app.state.settings.approval_webhook = "http://n8n.local/webhook/depo-uye-onay"

    signup = http.post(
        "/api/register",
        json={
            "first_name": "Ayşe",
            "last_name": "Yılmaz",
            "email": "ayse@ornek.com",
            "company_name": "Yılmaz Ticaret",
            "password": "gizli123",
        },
    )
    assert signup.status_code == 201
    blocked = http.post("/api/login", json={"username": "ayse@ornek.com", "password": "gizli123"})
    assert blocked.status_code == 403
    assert "onaylanmadı" in blocked.json()["message"]

    member_id = signup.json()["data"]["account"]["id"]
    approved = http.post(f"/api/members/{member_id}/approve")
    assert approved.status_code == 200
    assert calls[0][0] == "http://n8n.local/webhook/depo-uye-onay"
    assert calls[0][1]["to"] == "ayse@ornek.com"
    assert "onaylandı" in calls[0][1]["subject"]

    http.post("/api/logout")
    entered = http.post("/api/login", json={"username": "ayse@ornek.com", "password": "gizli123"})
    assert entered.status_code == 200
    assert entered.json()["data"]["user"]["role"] == "member"

    shelf = http.post("/api/locations", json={"block": "F", "shelf_code": "210", "capacity": 12})
    assert shelf.status_code == 201
    product = http.post("/api/products", json={"sku": "AYSE-1", "product_name": "Ayşe defteri"})
    assert product.status_code == 201
    added = http.post(
        "/api/stock/add",
        json={
            "product_id": product.json()["data"]["product"]["id"],
            "location_id": shelf.json()["data"]["location"]["id"],
            "quantity": 4,
            "confirmed": False,
        },
    )
    assert added.status_code == 400
    preview = http.post(
        "/api/stock/preview",
        json={
            "product_id": product.json()["data"]["product"]["id"],
            "location_id": shelf.json()["data"]["location"]["id"],
            "quantity": 4,
        },
    )
    assert "onaylıyor musun?" in preview.json()["message"]
    saved = http.post(
        "/api/stock/add",
        json={
            "product_id": product.json()["data"]["product"]["id"],
            "location_id": shelf.json()["data"]["location"]["id"],
            "quantity": 4,
            "confirmed": True,
        },
    )
    assert saved.status_code == 200
    rows = http.get("/api/stock").json()["data"]["rows"]
    assert any(row["sku"] == "AYSE-1" and row["block"] == "F" and row["quantity"] == 4 for row in rows)


def test_admin_message_shows_for_that_member(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={
            "first_name": "Ali",
            "last_name": "Demir",
            "email": "ali@ornek.com",
            "company_name": "Demir Ltd",
            "password": "gizli123",
        },
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    sent = http.post(f"/api/members/{member_id}/message", json={"body": "Yarın sayım var"})
    assert sent.status_code == 201
    http.post("/api/logout")
    http.post("/api/login", json={"username": "ali@ornek.com", "password": "gizli123"})
    notes = http.get("/api/messages").json()["data"]["rows"]
    assert notes[0]["body"] == "Yarın sayım var"
    assert notes[0]["seen"] is False
    assert http.post("/api/messages/seen").status_code == 200
    assert http.get("/api/messages").json()["data"]["rows"][0]["seen"] is True


def test_member_order_uses_their_shelf_only(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={
            "first_name": "Can",
            "last_name": "Kaya",
            "email": "can@ornek.com",
            "company_name": "Kaya",
            "password": "gizli123",
        },
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "can@ornek.com", "password": "gizli123"})
    shelf = http.post("/api/locations", json={"block": "K", "shelf_code": "1"})
    assert shelf.status_code == 201
    product = http.post("/api/products", json={"sku": "CAN-1", "product_name": "Kalem"})
    location_id = shelf.json()["data"]["location"]["id"]
    product_id = product.json()["data"]["product"]["id"]
    saved = http.post(
        "/api/stock/add",
        json={"product_id": product_id, "location_id": location_id, "quantity": 4, "confirmed": True},
    )
    assert saved.status_code == 200
    key = http.get("/api/me").json()["data"]["user"]["intake_key"]
    assert http.post("/api/webhooks/in/yok", json={"source": "Pazar yeri", "order_id": "100", "product_name": "Kalem", "quantity": 2}).status_code == 404
    created = http.post(
        f"/api/webhooks/in/{key}",
        json={"source": "Pazar yeri", "order_id": "100", "product_name": "Kalem", "quantity": 2},
    )
    assert created.status_code == 201
    order = created.json()["results"][0]["data"]["order"]
    assert order["order_id"] == "100"
    assert order["source"] == "Pazar yeri"
    assert order["status"] == "beklemede"
    assert order["needs_manual_match"] is False
    approved = http.post(f"/api/orders/{order['id']}/approve")
    assert approved.status_code == 200
    rows = http.get("/api/stock").json()["data"]["rows"]
    assert any(row["sku"] == "CAN-1" and row["quantity"] == 2 for row in rows)
    http.post("/api/logout")
    http.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    admin_rows = http.get("/api/orders").json()["data"]["rows"]
    assert all(row["order_id"] != "100" for row in admin_rows)


def test_member_can_write_admin_and_admin_can_delete_member(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={
            "first_name": "Eda",
            "last_name": "Koç",
            "email": "eda@ornek.com",
            "company_name": "Koç",
            "password": "gizli123",
        },
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "eda@ornek.com", "password": "gizli123"})
    http.post("/api/locations", json={"block": "E", "shelf_code": "1"})
    sent = http.post("/api/support", json={"channel": "trendyol", "body": "API secret nerede?"})
    assert sent.status_code == 201
    http.post("/api/logout")
    http.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    inbox = http.get("/api/support").json()["data"]["rows"]
    assert inbox[0]["body"] == "API secret nerede?"
    assert inbox[0]["channel"] == "Trendyol"
    assert inbox[0]["sender_email"] == "eda@ornek.com"
    answer = http.post(f"/api/support/{inbox[0]['id']}/reply", json={"body": "Entegrasyon bilgilerinde."})
    assert answer.status_code == 201
    assert http.get("/api/support").json()["data"]["rows"][0]["reply"] == "Entegrasyon bilgilerinde."
    http.post("/api/logout")
    http.post("/api/login", json={"username": "eda@ornek.com", "password": "gizli123"})
    assert http.get("/api/messages").json()["data"]["rows"][0]["body"] == "Entegrasyon bilgilerinde."
    http.post("/api/logout")
    http.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    removed = http.delete(f"/api/members/{member_id}")
    assert removed.status_code == 200
    assert all(row["id"] != member_id for row in http.get("/api/members").json()["data"]["rows"])
    assert http.get("/api/support").json()["data"]["rows"][0]["body"] == "API secret nerede?"
    http.post("/api/logout")
    blocked = http.post("/api/login", json={"username": "eda@ornek.com", "password": "gizli123"})
    assert blocked.status_code == 401
