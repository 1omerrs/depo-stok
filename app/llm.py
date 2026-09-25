from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import Settings
from app.models import Product

log = logging.getLogger(__name__)

SYSTEM = (
    "You match a marketplace product title to catalog SKUs. "
    'Reply with JSON only: {"matches":[{"sku":"...","confidence":0-100}]}. '
    "confidence is an integer. Use only SKUs from the catalog. Up to 3 matches, best first. "
    'If nothing is plausible, return {"matches":[]}.'
)


class LlmClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def rank(self, raw_text: str, products: list[Product]) -> list[dict] | None:
        catalog = [
            {
                "sku": product.sku,
                "product_name": product.product_name,
                "brand": product.brand,
                "category": product.category,
                "barcode": product.barcode,
                "alt_names": product.alt_names[:200],
            }
            for product in products
        ]
        user = json.dumps({"title": raw_text, "catalog": catalog}, ensure_ascii=False)
        try:
            text = self._complete(SYSTEM, user)
            parsed = _parse_json(text)
        except Exception:
            log.exception("LLM eşleştirmesi başarısız, benzerlik yedeğine düşülecek")
            return None
        matches = parsed.get("matches", [])
        if not isinstance(matches, list):
            return None
        return matches

    def _complete(self, system: str, user: str) -> str:
        provider = self.settings.llm_provider
        if provider == "auto":
            provider = "anthropic" if "anthropic" in self.settings.llm_base_url else "openai"
        if provider == "anthropic":
            return self._anthropic(system, user)
        return self._openai(system, user)

    def _openai(self, system: str, user: str) -> str:
        url = f"{self.settings.llm_base_url}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json={**payload, "response_format": {"type": "json_object"}})
            if response.status_code == 400:
                response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _anthropic(self, system: str, user: str) -> str:
        url = "https://api.anthropic.com/v1/messages"
        if "anthropic.com" not in self.settings.llm_base_url:
            url = f"{self.settings.llm_base_url}/messages"
        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.settings.llm_model,
            "max_tokens": 500,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        parts = response.json().get("content", [])
        return "".join(part.get("text", "") for part in parts if part.get("type") == "text")


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON nesne değil")
    return data
