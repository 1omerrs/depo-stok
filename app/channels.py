from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import httpx

from app.models import IncomingLine

CHANNELS = (
    {
        "code": "trendyol",
        "name": "Trendyol",
        "mode": "api",
        "live": True,
        "hint": "Entegrasyon bilgileri ile bağlanır.",
        "lead": "Trendyol mağazanız, satıcı panelindeki entegrasyon bilgileriyle bağlanır. Mağaza şifresi istenmez.",
        "steps": (
            "Satıcı paneline giriş yapın.",
            "Hesap Bilgilerim içinden Entegrasyon Bilgileri sayfasını açın.",
            "Satıcı numarası, API Key ve API Secret değerlerini yandaki alanlara yazın.",
        ),
        "fields": (
            {"name": "seller_id", "label": "Satıcı numarası", "secret": False},
            {"name": "api_key", "label": "API Key", "secret": False},
            {"name": "api_secret", "label": "API Secret", "secret": True},
        ),
    },
    {
        "code": "hepsiburada",
        "name": "Hepsiburada",
        "mode": "api",
        "live": True,
        "hint": "Entegrasyon bilgileri ile bağlanır.",
        "lead": "Hepsiburada mağazanız, satıcı panelindeki entegrasyon bilgileriyle bağlanır. Mağaza şifresi istenmez.",
        "steps": (
            "Hepsiburada satıcı paneline giriş yapın.",
            "Entegrasyon bölümünden Mağaza ID ve servis anahtarını alın.",
            "Bu değerleri yandaki alanlara yazın.",
        ),
        "fields": (
            {"name": "merchant_id", "label": "Mağaza ID", "secret": False},
            {"name": "service_key", "label": "Servis anahtarı", "secret": True},
        ),
    },
    {
        "code": "n11",
        "name": "n11",
        "mode": "api",
        "live": True,
        "hint": "API bilgileri ile bağlanır.",
        "lead": "n11 mağazanız, hesap sayfasındaki API bilgileriyle bağlanır. Mağaza şifresi istenmez.",
        "steps": (
            "n11 hesabınızda API bilgilerim sayfasını açın.",
            "API anahtarı ve API şifresini kopyalayın.",
            "Değerleri yandaki alanlara yazın.",
        ),
        "fields": (
            {"name": "app_key", "label": "API anahtarı", "secret": False},
            {"name": "app_secret", "label": "API şifresi", "secret": True},
        ),
    },
    {
        "code": "amazon",
        "name": "Amazon",
        "mode": "api",
        "live": False,
        "hint": "Satıcı kimliği ile kaydedilir.",
        "lead": "Amazon mağazanız satıcı kimliği ile kaydedilir. Amazon, e-posta ve şifre ile bağlantıya izin vermez.",
        "steps": (
            "Seller Central hesabına giriş yapın.",
            "Ayarlar, Hesap Bilgileri bölümünden satıcı kimliğinizi kopyalayın.",
            "Kimliği yandaki alana yazıp bağlantıyı kaydedin.",
        ),
        "fields": (
            {"name": "seller_id", "label": "Satıcı kimliği", "secret": False},
        ),
    },
    {
        "code": "website",
        "name": "Kendi sitem",
        "mode": "webhook",
        "live": True,
        "hint": "Sipariş geliş adresi ile bağlanır.",
        "lead": "Kendi sitenizden gelen sipariş, yandaki adrese ulaştığında bu listede görünür.",
        "steps": (
            "Sipariş geliş adresini kopyalayın.",
            "Adresi, sitenizi geliştiren kişiye iletin.",
            "Siteniz her yeni siparişte ürün adı, adet ve sipariş numarasını bu adrese gönderir.",
        ),
        "fields": (),
    },
)

OPEN_STATUSES = {"Created", "Picking", "Invoiced"}


class ChannelError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def channel_by_code(code: str) -> dict | None:
    return next((item for item in CHANNELS if item["code"] == code), None)


def pull_trendyol(secrets: dict) -> list[IncomingLine]:
    seller = str(secrets.get("seller_id") or "").strip()
    api_key = str(secrets.get("api_key") or "").strip()
    api_secret = str(secrets.get("api_secret") or "").strip()
    if not seller or not api_key or not api_secret:
        raise ChannelError("Trendyol bilgileri eksik")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=3)
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    try:
        response = httpx.get(
            f"https://apigw.trendyol.com/integration/order/sellers/{seller}/orders",
            params={
                "startDate": int(start.timestamp() * 1000),
                "endDate": int(end.timestamp() * 1000),
                "size": 50,
                "orderByField": "PackageLastModifiedDate",
                "orderByDirection": "DESC",
            },
            headers={
                "Authorization": f"Basic {token}",
                "User-Agent": f"{seller} - SelfIntegration",
            },
            timeout=25,
        )
    except httpx.HTTPError as exc:
        raise ChannelError("Trendyol’a ulaşılamadı") from exc
    if response.status_code in {401, 403}:
        raise ChannelError("Trendyol bilgileri kabul edilmedi")
    if response.status_code >= 400:
        raise ChannelError("Trendyol sipariş listesini vermedi")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChannelError("Trendyol yanıtı okunamadı") from exc
    return _trendyol_lines(payload if isinstance(payload, dict) else {})


def pull_hepsiburada(secrets: dict) -> list[IncomingLine]:
    merchant = str(secrets.get("merchant_id") or "").strip()
    service_key = str(secrets.get("service_key") or "").strip()
    if not merchant or not service_key:
        raise ChannelError("Hepsiburada bilgileri eksik")
    token = base64.b64encode(f"{merchant}:{service_key}".encode()).decode()
    payload = _get_json(
        f"https://oms-external.hepsiburada.com/orders/merchantid/{merchant}",
        params={"limit": 100},
        headers={"Authorization": f"Basic {token}", "User-Agent": "depo-stok", "Accept": "application/json"},
        label="Hepsiburada",
    )
    return _order_lines(payload.get("items") or [], source="Hepsiburada", key="Hepsiburada")


def pull_n11(secrets: dict) -> list[IncomingLine]:
    app_key = str(secrets.get("app_key") or "").strip()
    app_secret = str(secrets.get("app_secret") or "").strip()
    if not app_key or not app_secret:
        raise ChannelError("n11 bilgileri eksik")
    headers = {"appkey": app_key, "appsecret": app_secret, "Accept": "application/json"}
    lines: list[IncomingLine] = []
    for status in ("Created", "Picking"):
        payload = _get_json(
            "https://api.n11.com/rest/delivery/v1/shipmentPackages",
            params={"page": 0, "size": 100, "status": status},
            headers=headers,
            label="n11",
        )
        lines.extend(_order_lines(payload.get("content") or [], source="n11", key="n11"))
    return lines


def _get_json(url: str, *, params: dict, headers: dict, label: str) -> dict:
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=25)
    except httpx.HTTPError as exc:
        raise ChannelError(f"{label}’a ulaşılamadı") from exc
    if response.status_code in {401, 403}:
        raise ChannelError(f"{label} bilgileri kabul edilmedi")
    if response.status_code >= 400:
        raise ChannelError(f"{label} sipariş listesini vermedi")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChannelError(f"{label} yanıtı okunamadı") from exc
    return payload if isinstance(payload, dict) else {}


def _order_lines(rows: list, *, source: str, key: str) -> list[IncomingLine]:
    lines: list[IncomingLine] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("lines")
        items = nested if isinstance(nested, list) and nested else [row]
        order_id = str(row.get("orderNumber") or row.get("orderId") or row.get("id") or "").strip()
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            current_id = order_id or str(item.get("orderNumber") or item.get("orderId") or "").strip()
            if not current_id:
                continue
            name = str(item.get("productName") or "").strip()
            sku = str(item.get("merchantSku") or item.get("merchantSKU") or item.get("stockCode") or item.get("sku") or "").strip() or None
            barcode = str(item.get("barcode") or "").strip() or None
            try:
                quantity = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0 or not (name or sku or barcode):
                continue
            part = sku or barcode or f"line{index}"
            lines.append(
                IncomingLine(
                    source=source,
                    order_id=current_id,
                    product_name=name or sku or barcode or "",
                    quantity=quantity,
                    external_key=f"{key}|{current_id}|{part}",
                    sku=sku,
                    barcode=barcode,
                )
            )
    return lines


def _trendyol_lines(payload: dict) -> list[IncomingLine]:
    lines: list[IncomingLine] = []
    for pack in payload.get("content") or []:
        if not isinstance(pack, dict):
            continue
        status = str(pack.get("status") or "")
        if status and status not in OPEN_STATUSES:
            continue
        order_id = str(pack.get("orderNumber") or pack.get("id") or "").strip()
        if not order_id:
            continue
        for index, item in enumerate(pack.get("lines") or [], start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("productName") or "").strip()
            sku = str(item.get("merchantSku") or "").strip() or None
            barcode = str(item.get("barcode") or "").strip() or None
            try:
                quantity = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0 or not (name or sku or barcode):
                continue
            part = sku or barcode or f"line{index}"
            lines.append(
                IncomingLine(
                    source="Trendyol",
                    order_id=order_id,
                    product_name=name or sku or barcode or "",
                    quantity=quantity,
                    external_key=f"trendyol|{order_id}|{part}",
                    sku=sku,
                    barcode=barcode,
                )
            )
    return lines
