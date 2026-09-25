from __future__ import annotations

from app.models import IncomingLine

SOURCES = {
    "trendyol": "Trendyol",
    "amazon": "Amazon",
    "kendi site": "Kendi site",
    "website": "Kendi site",
    "web": "Kendi site",
    "kendi_site": "Kendi site",
}

ITEM_KEYS = ("items", "lines", "orderLines", "products", "Products")
ORDER_ID_KEYS = ("order_id", "orderId", "orderNumber", "AmazonOrderId", "shipmentPackageId")
NAME_KEYS = ("product_name", "productName", "raw_product_text", "title", "Title", "productTitle")
QTY_KEYS = ("quantity", "qty", "quantityOrdered", "QuantityOrdered", "adet")
SKU_KEYS = ("sku", "merchantSku", "SellerSKU", "sellerSku", "merchant_sku")
BARCODE_KEYS = ("barcode", "ean", "EAN", "gtin")


class PayloadError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def canonicalize_source(value: str | None) -> str:
    key = (value or "").strip().casefold()
    if key not in SOURCES:
        raise PayloadError("Kaynak Trendyol, Amazon veya Kendi site olmalı")
    return SOURCES[key]


def normalize(payload: dict, source: str | None = None) -> list[IncomingLine]:
    if not isinstance(payload, dict):
        raise PayloadError("Sipariş gövdesi nesne olmalı")
    forced = canonicalize_source(source) if source else None
    raw_source = forced or _first(payload, ("source", "channel", "platform"))
    channel = forced or canonicalize_source(str(raw_source or ""))
    order_id = str(_first(payload, ORDER_ID_KEYS) or "").strip()
    if not order_id:
        raise PayloadError("Sipariş numarası gerekli")
    items = _items(payload)
    lines: list[IncomingLine] = []
    for index, item in enumerate(items, start=1):
        name = str(_first(item, NAME_KEYS) or "").strip()
        sku = _clean(_first(item, SKU_KEYS))
        barcode = _clean(_first(item, BARCODE_KEYS))
        if not name and not sku and not barcode:
            raise PayloadError("Ürün adı, SKU veya barkod gerekli")
        quantity = _quantity(_first(item, QTY_KEYS))
        part = sku or barcode or f"line{index}"
        lines.append(
            IncomingLine(
                source=channel,
                order_id=order_id,
                product_name=name or sku or barcode or "",
                quantity=quantity,
                external_key=f"{channel}|{order_id}|{part}",
                sku=sku,
                barcode=barcode,
            )
        )
    return lines


def _items(payload: dict) -> list[dict]:
    for key in ITEM_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and value:
            if not all(isinstance(item, dict) for item in value):
                raise PayloadError("Sipariş satırları nesne olmalı")
            return value
    return [payload]


def _first(data: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _quantity(value) -> int:
    if value is None or isinstance(value, bool):
        raise PayloadError("Adet gerekli")
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError as exc:
        raise PayloadError("Adet sayı olmalı") from exc
    if number <= 0 or not number.is_integer() or number > 100000:
        raise PayloadError("Adet pozitif bir tam sayı olmalı")
    return int(number)
