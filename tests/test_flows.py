def qty(http, sku, block, shelf):
    rows = http.get("/api/stock").json()["data"]["rows"]
    for row in rows:
        if row["sku"] == sku and row["block"] == block and row["shelf_code"] == shelf:
            return row["quantity"]
    return 0


def snapshot(http):
    rows = http.get("/api/stock").json()["data"]["rows"]
    return {(row["sku"], row["block"], row["shelf_code"]): row["quantity"] for row in rows}


def test_login_required_and_rejected(app_client):
    http, _app = app_client
    assert http.get("/api/stock").status_code == 401
    assert http.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401


def test_order_waits_for_approval_and_is_idempotent(client):
    http, _app = client
    before = snapshot(http)
    created = http.post("/api/webhooks/website", json={"orderNumber": "WEB-1", "productName": "Aula F75 klavye", "quantity": 1})
    assert created.status_code == 201
    body = created.json()["results"][0]
    assert body["data"]["notification"]["body"].startswith("1 adet Aula F75 siparişi — Konum: A Blok 101 No'lu Raf")
    assert snapshot(http) == before

    token_path = body["data"]["notification"]["approval_url"].replace("http://testserver", "")
    preview = http.get(token_path)
    assert preview.status_code == 200
    assert "Onayla ve stoktan düş" in preview.text
    assert snapshot(http) == before

    approved = http.post(token_path)
    assert "Stok düşüldü" in approved.text
    assert qty(http, "AULA-F75", "A", "101") == before[("AULA-F75", "A", "101")] - 1
    assert qty(http, "AULA-F75", "B", "109") == before[("AULA-F75", "B", "109")]

    again = http.post(token_path)
    assert "zaten işlendi" in again.text
    assert qty(http, "AULA-F75", "A", "101") == before[("AULA-F75", "A", "101")] - 1


def test_barcode_exact_and_low_confidence_does_not_touch_stock(client):
    http, _app = client
    before = snapshot(http)
    exact = http.post(
        "/api/webhooks/trendyol",
        json={"orderNumber": "TY-1", "lines": [{"productName": "farkli yazim", "quantity": 1, "barcode": "8690000000035"}]},
    )
    assert exact.json()["results"][0]["data"]["order"]["match_method"] == "barcode"
    assert exact.json()["results"][0]["data"]["order"]["match_confidence"] == 100

    low = http.post("/api/webhooks/amazon", json={"AmazonOrderId": "AMZ-1", "Title": "tamamen bilinmeyen urun qq", "QuantityOrdered": 1})
    order = low.json()["results"][0]["data"]["order"]
    assert order["needs_manual_match"] is True
    assert "Emin değilim, olası adaylar:" in low.json()["results"][0]["data"]["notification"]["body"]
    token = low.json()["results"][0]["data"]["notification"]["approval_url"].rsplit("/", 1)[-1]
    refused = http.post(f"/api/approve/order/{token}")
    assert refused.status_code == 409
    assert snapshot(http) == before


def test_duplicate_webhook_does_not_double_create(client):
    http, _app = client
    payload = {"orderNumber": "WEB-2", "productName": "Keychron K2", "quantity": 1, "sku": "KEY-K2"}
    assert http.post("/api/webhooks/website", json=payload).status_code == 201
    again = http.post("/api/webhooks/website", json=payload)
    assert again.status_code == 200
    assert again.json()["results"][0]["code"] == "duplicate"


def test_return_restocks_original_shelf_only(client):
    http, _app = client
    http.post("/api/webhooks/website", json={"orderNumber": "WEB-3", "sku": "AULA-F75", "productName": "Aula F75", "quantity": 1})
    order = http.get("/api/orders").json()["data"]["rows"][0]
    http.post(f"/api/orders/{order['id']}/approve")
    assert qty(http, "AULA-F75", "A", "101") == 5

    opened = http.post("/api/webhooks/returns", json={"order_id": "WEB-3", "return_reason": "fikir değişikliği"})
    assert opened.status_code == 201
    assert qty(http, "AULA-F75", "A", "101") == 5
    item = opened.json()["data"]["return"]
    approved = http.post(f"/api/returns/{item['id']}/approve")
    assert approved.json()["data"]["return"]["status"] == "kontrol bekliyor"
    assert qty(http, "AULA-F75", "A", "101") == 5

    restocked = http.post(f"/api/returns/{item['id']}/restock", json={})
    assert restocked.json()["code"] == "restocked"
    assert qty(http, "AULA-F75", "A", "101") == 6
    assert qty(http, "AULA-F75", "B", "109") == 4
    again = http.post(f"/api/returns/{item['id']}/restock", json={})
    assert "zaten stoğa eklendi" in again.json()["message"]
    duplicate = http.post("/api/returns", json={"order_id": "WEB-3"})
    assert duplicate.status_code == 409


def test_unsuitable_shelf_does_not_auto_move(client):
    http, app = client
    http.post("/api/webhooks/website", json={"orderNumber": "WEB-4", "sku": "SS-QCK", "productName": "SteelSeries QcK Large", "quantity": 1})
    order = next(row for row in http.get("/api/orders").json()["data"]["rows"] if row["order_id"] == "WEB-4")
    http.post(f"/api/orders/{order['id']}/approve")
    assert qty(http, "SS-QCK", "C", "105") == 7
    opened = http.post("/api/returns", json={"order_id": "WEB-4", "return_reason": "defolu"})
    item_id = opened.json()["data"]["return"]["id"]
    http.post(f"/api/returns/{item_id}/approve")

    location = app.state.store.find_location("C", "105")
    location.capacity = 7
    app.state.store.save_location(location)
    blocked = http.post(f"/api/returns/{item_id}/restock", json={})
    assert blocked.status_code == 409
    assert "alternatif seçin" in blocked.json()["message"]
    assert qty(http, "SS-QCK", "C", "105") == 7
    assert qty(http, "LOGI-MX3", "B", "101") == 10

    other = app.state.store.find_location("B", "101")
    moved = http.post(f"/api/returns/{item_id}/restock", json={"location_id": other.id})
    assert moved.status_code == 200
    assert qty(http, "SS-QCK", "C", "105") == 7
    assert qty(http, "SS-QCK", "B", "101") == 1


def test_manual_add_needs_summary_confirmation(client):
    http, _app = client
    product = next(item for item in http.get("/api/options").json()["data"]["products"] if item["sku"] == "HX-C2")
    location = next(item for item in http.get("/api/options").json()["data"]["locations"] if item["block"] == "E" and item["shelf_code"] == "101")
    before = qty(http, "HX-C2", "E", "101")
    skipped = http.post("/api/stock/add", json={"product_id": product["id"], "location_id": location["id"], "quantity": 2, "confirmed": False})
    assert skipped.status_code == 400
    assert qty(http, "HX-C2", "E", "101") == before
    preview = http.post("/api/stock/preview", json={"product_id": product["id"], "location_id": location["id"], "quantity": 2})
    assert "onaylıyor musun?" in preview.json()["message"]
    added = http.post("/api/stock/add", json={"product_id": product["id"], "location_id": location["id"], "quantity": 2, "confirmed": True})
    assert added.status_code == 200
    assert qty(http, "HX-C2", "E", "101") == before + 2
