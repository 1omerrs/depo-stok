from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _session_secret() -> str:
    configured = env("PANEL_SECRET")
    if configured:
        return configured
    path = Path("data") / "session.secret"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_hex(32)
    path.write_text(generated, encoding="utf-8")
    return generated


@dataclass
class Settings:
    data_backend: str
    sqlite_path: str
    airtable_api_key: str
    airtable_base_id: str
    airtable_products: str
    airtable_locations: str
    airtable_stock: str
    airtable_orders: str
    airtable_returns: str
    panel_username: str
    panel_password: str
    panel_secret: str
    public_base_url: str
    auto_match_threshold: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_provider: str
    notify_email: str
    webhook_secret: str
    inspect_webhook: str
    approval_webhook: str
    signup_form: str
    order_webhook: str = ""
    low_stock_webhook: str = ""
    reply_webhook: str = ""
    channel_webhook: str = ""
    channel_error_webhook: str = ""
    low_stock_at: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        backend = env("DATA_BACKEND", "auto").lower()
        has_airtable = bool(env("AIRTABLE_API_KEY") and env("AIRTABLE_BASE_ID"))
        if backend == "auto":
            backend = "airtable" if has_airtable else "sqlite"
        if backend not in {"sqlite", "airtable"}:
            raise RuntimeError("DATA_BACKEND sqlite, airtable veya auto olmalı")
        try:
            threshold = int(env("AUTO_MATCH_THRESHOLD", "90"))
        except ValueError as exc:
            raise RuntimeError("AUTO_MATCH_THRESHOLD sayı olmalı") from exc
        return cls(
            data_backend=backend,
            sqlite_path=env("SQLITE_PATH", "./data/warehouse.db"),
            airtable_api_key=env("AIRTABLE_API_KEY"),
            airtable_base_id=env("AIRTABLE_BASE_ID"),
            airtable_products=env("AIRTABLE_TABLE_PRODUCTS", "products"),
            airtable_locations=env("AIRTABLE_TABLE_LOCATIONS", "locations"),
            airtable_stock=env("AIRTABLE_TABLE_STOCK", "stock"),
            airtable_orders=env("AIRTABLE_TABLE_ORDERS", "orders"),
            airtable_returns=env("AIRTABLE_TABLE_RETURNS", "returns"),
            panel_username=env("PANEL_USERNAME", "admin"),
            panel_password=env("PANEL_PASSWORD", "depo-panel"),
            panel_secret=_session_secret(),
            public_base_url=env("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            auto_match_threshold=threshold,
            llm_api_key=env("LLM_API_KEY"),
            llm_base_url=env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_model=env("LLM_MODEL", "gpt-4o-mini"),
            llm_provider=env("LLM_PROVIDER", "auto").lower(),
            notify_email=env("NOTIFY_EMAIL", "depo@example.com"),
            webhook_secret=env("WEBHOOK_SECRET"),
            inspect_webhook=env("N8N_INSPECT_WEBHOOK"),
            approval_webhook=env("N8N_APPROVAL_WEBHOOK"),
            signup_form=env("N8N_SIGNUP_FORM"),
            order_webhook=env("N8N_ORDER_WEBHOOK"),
            low_stock_webhook=env("N8N_LOW_STOCK_WEBHOOK"),
            reply_webhook=env("N8N_REPLY_WEBHOOK"),
            channel_webhook=env("N8N_CHANNEL_WEBHOOK"),
            channel_error_webhook=env("N8N_CHANNEL_ERROR_WEBHOOK"),
            low_stock_at=int(env("LOW_STOCK_AT", "5") or "5"),
        )

    @property
    def demo_login(self) -> bool:
        return self.panel_username == "admin" and self.panel_password == "depo-panel"
