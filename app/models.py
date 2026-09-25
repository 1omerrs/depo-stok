from __future__ import annotations

from dataclasses import dataclass, field


class StockError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Account:
    id: str
    first_name: str
    last_name: str
    email: str
    company_name: str
    password_hash: str
    status: str
    created_at: str = ""
    mail_sent: bool = False


@dataclass
class Product:
    id: str
    sku: str
    product_name: str
    brand: str = ""
    category: str = ""
    barcode: str = ""
    alt_names: str = ""

    def alt_list(self) -> list[str]:
        if not self.alt_names:
            return []
        return [part.strip() for part in self.alt_names.split("|") if part.strip()]


@dataclass
class Location:
    id: str
    block: str
    shelf_code: str
    capacity: int | None = None
    active: bool = True
    owner_id: str = ""


@dataclass
class StockRow:
    id: str
    product_id: str
    location_id: str
    quantity: int


@dataclass
class Order:
    id: str
    external_key: str
    order_id: str
    source: str
    raw_product_text: str
    quantity: int
    status: str
    approval_token: str
    matched_product_id: str | None = None
    match_confidence: int | None = None
    match_method: str | None = None
    candidates: list[dict] = field(default_factory=list)
    proposed_location_id: str | None = None
    source_location_id: str | None = None
    needs_manual_match: bool = False
    stock_note: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ReturnRequest:
    id: str
    original_order_id: str
    status: str
    approval_token: str
    inspect_token: str
    return_reason: str = ""
    restock_note: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class IncomingLine:
    source: str
    order_id: str
    product_name: str
    quantity: int
    external_key: str
    sku: str | None = None
    barcode: str | None = None


@dataclass
class Candidate:
    product: Product
    confidence: int
    reason: str = ""


@dataclass
class MatchOutcome:
    product: Product | None
    confidence: int
    method: str
    candidates: list[Candidate] = field(default_factory=list)

    def is_auto(self, threshold: int) -> bool:
        return self.product is not None and self.confidence >= threshold
