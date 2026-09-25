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
