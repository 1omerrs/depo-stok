from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote

import httpx

from app.config import Settings
from app.models import Location, Order, Product, ReturnRequest, StockError, StockRow
from app.textutil import fold

log = logging.getLogger(__name__)


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class AirtableStore:
    """Airtable REST. İlişkiler linked record alanlarıdır; stok satırı ayrıca product_id ve location_id metin alanları tutar."""

    atomic = False

    def __init__(self, settings: Settings):
        self.base_id = settings.airtable_base_id
        self.tables = {
            "products": settings.airtable_products,
            "locations": settings.airtable_locations,
            "stock": settings.airtable_stock,
            "orders": settings.airtable_orders,
            "returns": settings.airtable_returns,
        }
        self._headers = {"Authorization": f"Bearer {settings.airtable_api_key}", "Content-Type": "application/json"}
        self._http = httpx.Client(timeout=30)
        self._lock = threading.RLock()
        self._in_tx = False
        self._undo: list = []
        self._rolling = False

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._in_tx:
                yield
                return
            self._in_tx = True
            self._undo = []
            try:
                yield
            except Exception:
                self._rollback()
                raise
            finally:
                self._in_tx = False
                self._undo = []

    def list_products(self) -> list[Product]:
        return [self._product(rec) for rec in self._list("products")]

    def get_product(self, product_id: str) -> Product | None:
        rec = self._get("products", product_id)
        return self._product(rec) if rec else None

    def save_product(self, product: Product) -> Product:
        self._patch("products", product.id, {"alt_names": product.alt_names})
        return product

    def insert_product(self, product: Product) -> Product:
        rec = self._create("products", self._product_fields(product))
        product.id = rec["id"]
        return product

    def list_block_codes(self, owner_id: str = "") -> list[str]:
        return sorted({item.block for item in self.list_locations() if item.block and item.owner_id == owner_id})

    def insert_block_code(self, code: str, owner_id: str = "") -> None:
        return None

    def delete_block_code(self, code: str, owner_id: str = "") -> None:
        return None

    def delete_stock_row(self, stock_id: str) -> None:
        self._request("DELETE", self._url("stock", stock_id))

    def delete_location(self, location_id: str) -> None:
        for row in self.list_stock():
            if row.location_id == location_id:
                self.delete_stock_row(row.id)
        self._request("DELETE", self._url("locations", location_id))

    def list_locations(self) -> list[Location]:
        return [self._location(rec) for rec in self._list("locations")]

    def get_location(self, location_id: str) -> Location | None:
        rec = self._get("locations", location_id)
        return self._location(rec) if rec else None

    def find_location(self, block: str, shelf_code: str, owner_id: str = "") -> Location | None:
        for location in self.list_locations():
            if location.owner_id == owner_id and fold(location.block) == fold(block) and fold(location.shelf_code) == fold(shelf_code):
                return location
        return None

    def insert_location(self, location: Location) -> Location:
        rec = self._create(
            "locations",
            {"block": location.block, "shelf_code": location.shelf_code, "capacity": location.capacity, "active": location.active},
        )
        location.id = rec["id"]
        return location

    def save_location(self, location: Location) -> Location:
        fields = {"block": location.block, "shelf_code": location.shelf_code, "active": location.active}
        if location.capacity is not None:
            fields["capacity"] = location.capacity
        self._patch("locations", location.id, fields)
        return location

    def list_stock(self) -> list[StockRow]:
        return [self._stock(rec) for rec in self._list("stock")]

    def find_stock(self, product_id: str, location_id: str) -> StockRow | None:
        for row in self.list_stock():
            if row.product_id == product_id and row.location_id == location_id:
                return row
        return None

    def insert_stock(self, row: StockRow) -> StockRow:
        rec = self._create(
            "stock",
            {
                "product": [row.product_id],
                "location": [row.location_id],
                "product_id": row.product_id,
                "location_id": row.location_id,
                "quantity": row.quantity,
            },
        )
        row.id = rec["id"]
        return row

    def adjust_stock(self, product_id: str, location_id: str, delta: int, capacity_check: bool = True) -> int:
        location = self.get_location(location_id)
        if location is None:
            raise StockError("missing")
        current_row = self.find_stock(product_id, location_id)
        current = current_row.quantity if current_row else 0
        new_qty = current + delta
        if new_qty < 0:
            raise StockError("negative")
        if capacity_check and delta > 0 and location.capacity is not None and new_qty > location.capacity:
            raise StockError("capacity")
        if current_row is None:
            self.insert_stock(StockRow(id="", product_id=product_id, location_id=location_id, quantity=new_qty))
        else:
            self._patch("stock", current_row.id, {"quantity": new_qty})
        return new_qty

    def get_order(self, order_id: str) -> Order | None:
        rec = self._get("orders", order_id)
        return self._order(rec) if rec else None

    def get_order_by_token(self, token: str) -> Order | None:
        rec = self._find("orders", f"{{approval_token}}='{_esc(token)}'")
        return self._order(rec) if rec else None

    def find_order_by_external(self, external_key: str) -> Order | None:
        rec = self._find("orders", f"{{external_key}}='{_esc(external_key)}'")
        return self._order(rec) if rec else None

    def list_orders(self) -> list[Order]:
        rows = [self._order(rec) for rec in self._list("orders")]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows

    def list_orders_by_order_id(self, order_id: str) -> list[Order]:
        return [self._order(rec) for rec in self._list("orders") if rec.get("fields", {}).get("order_id") == order_id]

    def save_order(self, order: Order) -> Order:
        fields = self._order_fields(order)
        if self._get("orders", order.id):
            self._patch("orders", order.id, fields)
            return order
        rec = self._create("orders", fields)
        order.id = rec["id"]
        return order

    def get_return(self, return_id: str) -> ReturnRequest | None:
        rec = self._get("returns", return_id)
        return self._return(rec) if rec else None

    def get_return_by_token(self, token: str, kind: str) -> ReturnRequest | None:
        column = "approval_token" if kind == "approval" else "inspect_token"
        rec = self._find("returns", f"{{{column}}}='{_esc(token)}'")
        return self._return(rec) if rec else None

    def list_returns(self) -> list[ReturnRequest]:
        rows = [self._return(rec) for rec in self._list("returns")]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows

    def active_return_for_order(self, order_pk: str) -> ReturnRequest | None:
        for item in self.list_returns():
            if item.original_order_id == order_pk and item.status != "reddedildi":
                return item
        return None

    def save_return(self, item: ReturnRequest) -> ReturnRequest:
        fields = self._return_fields(item)
        if self._get("returns", item.id):
            self._patch("returns", item.id, fields)
            return item
        rec = self._create("returns", fields)
        item.id = rec["id"]
        return item

    def _url(self, table: str, record_id: str | None = None) -> str:
        name = quote(self.tables[table])
        base = f"https://api.airtable.com/v0/{self.base_id}/{name}"
        return f"{base}/{record_id}" if record_id else base

    def _request(self, method: str, url: str, **kwargs):
        response = None
        for attempt in range(2):
            response = self._http.request(method, url, headers=self._headers, **kwargs)
            if response.status_code == 429 and attempt == 0:
                time.sleep(float(response.headers.get("retry-after", "1") or 1))
                continue
            break
        assert response is not None
        if response.status_code >= 400:
            raise RuntimeError(f"Airtable hatası ({response.status_code}): {response.text[:500]}")
        if not response.content:
            return {}
        return response.json()

    def _list(self, table: str) -> list[dict]:
        records = []
        offset = None
        while True:
            params = {"pageSize": 100}
            if offset:
                params["offset"] = offset
            data = self._request("GET", self._url(table), params=params)
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                return records

    def _get(self, table: str, record_id: str) -> dict | None:
        if not record_id or not str(record_id).startswith("rec"):
            return None
        try:
            return self._request("GET", self._url(table, record_id))
        except RuntimeError as exc:
            if "(404)" in str(exc):
                return None
            raise

    def _find(self, table: str, formula: str) -> dict | None:
        data = self._request("GET", self._url(table), params={"filterByFormula": formula, "maxRecords": 1})
        records = data.get("records", [])
        return records[0] if records else None

    def _create(self, table: str, fields: dict) -> dict:
        clean = {key: value for key, value in fields.items() if value is not None and value != []}
        rec = self._request("POST", self._url(table), json={"fields": clean})
        record_id = rec["id"]
        if not self._rolling:
            self._undo.append(lambda rid=record_id, tbl=table: self._request("DELETE", self._url(tbl, rid)))
        return rec

    def _patch(self, table: str, record_id: str, fields: dict) -> dict:
        current = self._get(table, record_id) or {"fields": {}}
        previous = {key: current.get("fields", {}).get(key) for key in fields}
        if not self._rolling:
            self._undo.append(lambda tbl=table, rid=record_id, prev=previous: self._request("PATCH", self._url(tbl, rid), json={"fields": prev}))
        return self._request("PATCH", self._url(table, record_id), json={"fields": fields})

    def _rollback(self) -> None:
        self._rolling = True
        try:
            for action in reversed(self._undo):
                try:
                    action()
                except Exception:
                    log.exception("Airtable geri alma başarısız")
        finally:
            self._rolling = False
            self._undo = []

    def _link(self, fields: dict, key: str) -> str | None:
        value = fields.get(key) or fields.get(f"{key}_id")
        if isinstance(value, list) and value:
            return value[0]
        if isinstance(value, str) and value:
            return value
        return None

    def _product(self, rec: dict) -> Product:
        fields = rec.get("fields", {})
        return Product(
            id=rec["id"],
            sku=str(fields.get("sku", "")),
            product_name=str(fields.get("product_name", "")),
            brand=str(fields.get("brand", "") or ""),
            category=str(fields.get("category", "") or ""),
            barcode=str(fields.get("barcode", "") or ""),
            alt_names=str(fields.get("alt_names", "") or ""),
        )

    def _product_fields(self, product: Product) -> dict:
        return {
            "sku": product.sku,
            "product_name": product.product_name,
            "brand": product.brand,
            "category": product.category,
            "barcode": product.barcode,
            "alt_names": product.alt_names,
        }

    def _location(self, rec: dict) -> Location:
        fields = rec.get("fields", {})
        capacity = fields.get("capacity")
        return Location(
            id=rec["id"],
            block=str(fields.get("block", "")),
            shelf_code=str(fields.get("shelf_code", "")),
            capacity=int(capacity) if capacity not in (None, "") else None,
            active=bool(fields.get("active", True)),
        )

    def _stock(self, rec: dict) -> StockRow:
        fields = rec.get("fields", {})
        return StockRow(
            id=rec["id"],
            product_id=self._link(fields, "product") or "",
            location_id=self._link(fields, "location") or "",
            quantity=int(fields.get("quantity") or 0),
        )

    def _order(self, rec: dict) -> Order:
        fields = rec.get("fields", {})
        try:
            candidates = json.loads(fields.get("candidates") or "[]")
        except json.JSONDecodeError:
            candidates = []
        confidence = fields.get("match_confidence")
        return Order(
            id=rec["id"],
            external_key=str(fields.get("external_key", "")),
            order_id=str(fields.get("order_id", "")),
            source=str(fields.get("source", "")),
            raw_product_text=str(fields.get("raw_product_text", "")),
            quantity=int(fields.get("quantity") or 0),
            status=str(fields.get("status", "")),
            approval_token=str(fields.get("approval_token", "")),
            matched_product_id=self._link(fields, "matched_product"),
            match_confidence=int(confidence) if confidence not in (None, "") else None,
            match_method=fields.get("match_method") or None,
            candidates=candidates if isinstance(candidates, list) else [],
            proposed_location_id=self._link(fields, "proposed_location"),
            source_location_id=self._link(fields, "source_location"),
            needs_manual_match=bool(fields.get("needs_manual_match")),
            stock_note=fields.get("stock_note") or None,
            created_at=str(fields.get("created_at", "")),
            updated_at=str(fields.get("updated_at", "")),
        )

    def _order_fields(self, order: Order) -> dict:
        fields = {
            "external_key": order.external_key,
            "order_id": order.order_id,
            "source": order.source,
            "raw_product_text": order.raw_product_text,
            "match_method": order.match_method or "",
            "candidates": json.dumps(order.candidates, ensure_ascii=False),
            "quantity": order.quantity,
            "status": order.status,
            "needs_manual_match": order.needs_manual_match,
            "approval_token": order.approval_token,
            "stock_note": order.stock_note or "",
            "created_at": order.created_at,
            "updated_at": order.updated_at,
            "matched_product_id": order.matched_product_id or "",
            "proposed_location_id": order.proposed_location_id or "",
            "source_location_id": order.source_location_id or "",
        }
        if order.match_confidence is not None:
            fields["match_confidence"] = order.match_confidence
        if order.matched_product_id:
            fields["matched_product"] = [order.matched_product_id]
        if order.proposed_location_id:
            fields["proposed_location"] = [order.proposed_location_id]
        if order.source_location_id:
            fields["source_location"] = [order.source_location_id]
        return fields

    def _return(self, rec: dict) -> ReturnRequest:
        fields = rec.get("fields", {})
        return ReturnRequest(
            id=rec["id"],
            original_order_id=self._link(fields, "original_order") or str(fields.get("original_order_id") or ""),
            status=str(fields.get("status", "")),
            approval_token=str(fields.get("approval_token", "")),
            inspect_token=str(fields.get("inspect_token", "")),
            return_reason=str(fields.get("return_reason", "") or ""),
            restock_note=fields.get("restock_note") or None,
            created_at=str(fields.get("created_at", "")),
            updated_at=str(fields.get("updated_at", "")),
        )

    def _return_fields(self, item: ReturnRequest) -> dict:
        fields = {
            "return_reason": item.return_reason,
            "status": item.status,
            "approval_token": item.approval_token,
            "inspect_token": item.inspect_token,
            "restock_note": item.restock_note or "",
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "original_order_id": item.original_order_id,
        }
        if item.original_order_id:
            fields["original_order"] = [item.original_order_id]
        return fields
