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


def test_empty_product_keeps_the_choice_open(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={"first_name": "Ada", "last_name": "Yilmaz", "email": "ada@ornek.com", "company_name": "Ada", "password": "gizli123"},
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "ada@ornek.com", "password": "gizli123"})
    shelf = http.post("/api/locations", json={"block": "A", "shelf_code": "1"}).json()["data"]["location"]["id"]
    empty = http.post("/api/products", json={"sku": "BOS", "product_name": "Bos urun"}).json()["data"]["product"]["id"]
    stocked = http.post("/api/products", json={"sku": "DOLU", "product_name": "Dolu urun"}).json()["data"]["product"]["id"]
    http.post("/api/stock/add", json={"product_id": stocked, "location_id": shelf, "quantity": 3, "confirmed": True})
    key = http.get("/api/me").json()["data"]["user"]["intake_key"]
    created = http.post(f"/api/webhooks/in/{key}", json={"source": "Kendi sitem", "order_id": "SEC-1", "product_name": "bilinmeyen urun xyz", "quantity": 1})
    order_id = created.json()["results"][0]["data"]["order"]["id"]
    refused = http.post(f"/api/orders/{order_id}/match", json={"product_id": empty})
    assert refused.status_code == 409
    still = http.get("/api/orders").json()["data"]["rows"][0]
    assert still["needs_manual_match"] is True
    assert "Başka bir ürün seçin" in still["stock_note"]
    matched = http.post(f"/api/orders/{order_id}/match", json={"product_id": stocked})
    assert matched.status_code == 200
    assert matched.json()["data"]["order"]["needs_manual_match"] is False
    stock_id = http.get("/api/stock").json()["data"]["rows"][0]["stock_id"]
    for _ in range(3):
        http.post(f"/api/stock/{stock_id}/adjust", json={"delta": -1})
    blocked = http.post(f"/api/orders/{order_id}/approve", json={})
    assert blocked.status_code == 409
    assert blocked.json()["message"] == "Stokta yok"
    reopened = http.get("/api/orders").json()["data"]["rows"][0]
    assert reopened["needs_manual_match"] is False
    assert reopened["stock_note"] == "Stokta yok"
    assert reopened["location_label"] is None
    assert reopened["status"] == "beklemede"


def test_one_order_waits_until_every_line_is_chosen(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={"first_name": "Eda", "last_name": "Demir", "email": "eda.demir@ornek.com", "company_name": "Demir", "password": "gizli123"},
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "eda.demir@ornek.com", "password": "gizli123"})
    shelf = http.post("/api/locations", json={"block": "D", "shelf_code": "2"}).json()["data"]["location"]["id"]
    product = http.post("/api/products", json={"sku": "KALEM", "product_name": "Kalem"}).json()["data"]["product"]["id"]
    http.post("/api/stock/add", json={"product_id": product, "location_id": shelf, "quantity": 5, "confirmed": True})
    key = http.get("/api/me").json()["data"]["user"]["intake_key"]
    bundled = http.post(
        f"/api/webhooks/in/{key}",
        json={
            "source": "Kendi sitem",
            "order_id": "GRP-1",
            "items": [
                {"product_name": "bilinmeyen aaa", "quantity": 1, "sku": "X1"},
                {"product_name": "bilinmeyen bbb", "quantity": 1, "sku": "X2"},
                {"product_name": "bilinmeyen ccc", "quantity": 1, "sku": "X3"},
            ],
        },
    )
    assert bundled.status_code == 201
    lines = [item["data"]["order"] for item in bundled.json()["results"]]
    assert len(lines) == 3
    assert {item["order_id"] for item in lines} == {"GRP-1"}
    refused = http.post(f"/api/orders/{lines[0]['id']}/approve", json={})
    assert refused.status_code == 409
    assert "seçilmeden onaylanmaz" in refused.json()["message"]
    solo = http.post(
        f"/api/webhooks/in/{key}",
        json={"source": "Kendi sitem", "order_id": "SOLO-1", "product_name": "Kalem", "quantity": 1, "sku": "KALEM"},
    )
    solo_id = solo.json()["results"][0]["data"]["order"]["id"]
    assert http.post(f"/api/orders/{solo_id}/approve", json={}).status_code == 200
    for line in lines:
        assert http.post(f"/api/orders/{line['id']}/match", json={"product_id": product}).status_code == 200
    done = http.post(f"/api/orders/{lines[0]['id']}/approve", json={})
    assert done.status_code == 200
    assert "3 ürün" in done.json()["message"]
    stored = [row for row in http.get("/api/orders").json()["data"]["rows"] if row["order_id"] == "GRP-1"]
    assert [row["status"] for row in stored] == ["tamamlandı", "tamamlandı", "tamamlandı"]
    assert http.get("/api/stock").json()["data"]["rows"][0]["quantity"] == 1


def test_stock_summary_shows_entries_and_exits(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={"first_name": "Naz", "last_name": "Acar", "email": "naz@ornek.com", "company_name": "Acar", "password": "gizli123"},
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "naz@ornek.com", "password": "gizli123"})
    shelf = http.post("/api/locations", json={"block": "N", "shelf_code": "4"}).json()["data"]["location"]["id"]
    product = http.post("/api/products", json={"sku": "NAZ-1", "product_name": "Defter"}).json()["data"]["product"]["id"]
    http.post("/api/stock/add", json={"product_id": product, "location_id": shelf, "quantity": 4, "confirmed": True})
    added = http.get("/api/stock").json()["data"]["summary"]
    assert added["in_total"] == 4
    assert added["out_total"] == 0
    assert added["entries"][0]["product_name"] == "Defter"
    assert added["entries"][0]["reason"] == "Rafa ekleme"
    key = http.get("/api/me").json()["data"]["user"]["intake_key"]
    created = http.post(
        f"/api/webhooks/in/{key}",
        json={"source": "Kendi sitem", "order_id": "NAZ-9", "product_name": "Defter", "quantity": 1, "sku": "NAZ-1"},
    )
    order_id = created.json()["results"][0]["data"]["order"]["id"]
    assert http.post(f"/api/orders/{order_id}/approve", json={}).status_code == 200
    summary = http.get("/api/stock").json()["data"]["summary"]
    assert summary["in_total"] == 4
    assert summary["out_total"] == 1
    assert summary["exits"][0]["reason"] == "Sipariş NAZ-9"
    assert summary["exits"][0]["quantity"] == 1


def test_order_sees_stock_added_later(client):
    http, _app = client
    signup = http.post(
        "/api/register",
        json={"first_name": "Nil", "last_name": "Ak", "email": "nil@ornek.com", "company_name": "Ak", "password": "gizli123"},
    )
    member_id = signup.json()["data"]["account"]["id"]
    http.post(f"/api/members/{member_id}/approve")
    http.post("/api/logout")
    http.post("/api/login", json={"username": "nil@ornek.com", "password": "gizli123"})
    http.post("/api/locations", json={"block": "A", "shelf_code": "100"})
    http.post("/api/products", json={"sku": "TERMOS-2", "product_name": "Kirmizi termos"})
    key = http.get("/api/me").json()["data"]["user"]["intake_key"]
    created = http.post(
        f"/api/webhooks/in/{key}",
        json={"source": "Kendi sitem", "order_id": "AYSE-2", "product_name": "Kirmizi termos", "quantity": 1, "sku": "TERMOS-2"},
    )
    order = created.json()["results"][0]["data"]["order"]
    assert order["location_label"] is None
    assert order["stock_note"] == "Stokta yok"
    http.post("/api/stock/place", json={"product_name": "Kirmizi termos", "block": "A", "shelf_code": "100", "quantity": 18, "confirmed": True})
    refreshed = http.get("/api/orders").json()["data"]["rows"][0]
    assert refreshed["stock_note"] is None
    assert refreshed["location_label"]
    assert refreshed["status"] == "beklemede"
