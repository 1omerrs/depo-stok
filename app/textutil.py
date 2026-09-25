from __future__ import annotations

import re
from datetime import datetime, timezone

_TR = str.maketrans(
    {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "Ç": "c",
        "Ğ": "g",
        "İ": "i",
        "I": "i",
        "Ö": "o",
        "Ş": "s",
        "Ü": "u",
    }
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(value: str | None) -> str:
    text = (value or "").translate(_TR).lower()
    text = text.replace("i̇", "i")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def shelf_key(code: str) -> tuple:
    text = str(code).strip()
    if text.isdigit():
        return (0, int(text))
    return (1, text.casefold())


def location_label(block: str, shelf: str) -> str:
    return f"{block} Blok {shelf} No'lu Raf"


def order_sentence(
    quantity: int,
    product_name: str,
    block: str | None,
    shelf: str | None,
    confidence: int | None,
    warning: str | None = None,
) -> str:
    if block and shelf:
        place = location_label(block, shelf)
    else:
        place = "stokta bulunamadı"
    score = 0 if confidence is None else confidence
    text = f"{quantity} adet {product_name} siparişi — Konum: {place} (eşleşme güveni: %{score})"
    if warning:
        text = f"{text} — {warning}"
    return text


def manual_sentence(candidates: list[dict]) -> str:
    names = ", ".join(item["product_name"] for item in candidates[:3] if item.get("product_name"))
    if not names:
        names = "yok"
    return f"Emin değilim, olası adaylar: {names}"


def stock_sentence(product_name: str, quantity: int, block: str, shelf: str) -> str:
    return (
        f"{product_name} ürününden {quantity} adet, "
        f"{block} Blok {shelf} No'lu rafına eklenecek — onaylıyor musun?"
    )
