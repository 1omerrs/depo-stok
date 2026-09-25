from __future__ import annotations

from app.config import Settings
from app.llm import LlmClient
from app.models import Candidate, MatchOutcome, Product
from app.textutil import fold


class Matcher:
    def __init__(self, settings: Settings, llm: LlmClient | None = None):
        self.threshold = settings.auto_match_threshold
        if llm is not None:
            self.llm = llm
        elif settings.llm_api_key:
            self.llm = LlmClient(settings)
        else:
            self.llm = None

    def match(
        self,
        products: list[Product],
        raw_text: str,
        sku: str | None = None,
        barcode: str | None = None,
    ) -> MatchOutcome:
        exact = self._exact(products, raw_text, sku, barcode)
        if exact is not None:
            return exact
        if self.llm is not None:
            ranked = self.llm.rank(raw_text, products)
            if ranked is not None:
                return self._from_ranked(products, ranked, method="llm")
        return self._fuzzy(products, raw_text)

    def _exact(
        self,
        products: list[Product],
        raw_text: str,
        sku: str | None,
        barcode: str | None,
    ) -> MatchOutcome | None:
        if sku:
            found = self._unique(products, lambda p: fold(p.sku) == fold(sku))
            if found is not None:
                return self._hit(found, 100, "sku")
        if barcode:
            found = self._unique(products, lambda p: bool(p.barcode) and fold(p.barcode) == fold(barcode))
            if found is not None:
                return self._hit(found, 100, "barcode")
        folded = fold(raw_text)
        if not folded:
            return None
        found = self._unique(products, lambda p: fold(p.sku) == folded)
        if found is not None:
            return self._hit(found, 100, "sku")
        found = self._unique(products, lambda p: bool(p.barcode) and fold(p.barcode) == folded)
        if found is not None:
            return self._hit(found, 100, "barcode")
        found = self._unique(products, lambda p: fold(p.product_name) == folded)
        if found is not None:
            return self._hit(found, 100, "name")
        found = self._unique(products, lambda p: any(fold(alt) == folded for alt in p.alt_list()))
        if found is not None:
            return self._hit(found, 100, "alt_name")
        return None

    def _unique(self, products: list[Product], pred) -> Product | None:
        hits = [product for product in products if pred(product)]
        if len(hits) == 1:
            return hits[0]
        return None

    def _hit(self, product: Product, confidence: int, method: str) -> MatchOutcome:
        candidate = Candidate(product=product, confidence=confidence, reason=method)
        return MatchOutcome(product=product, confidence=confidence, method=method, candidates=[candidate])

    def _from_ranked(self, products: list[Product], ranked: list[dict], method: str) -> MatchOutcome:
        by_sku = {fold(product.sku): product for product in products}
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for item in ranked:
            product = by_sku.get(fold(str(item.get("sku", ""))))
            if product is None or product.id in seen:
                continue
            seen.add(product.id)
            try:
                confidence = int(round(float(item.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0
            confidence = max(0, min(100, confidence))
            candidates.append(Candidate(product=product, confidence=confidence, reason=str(item.get("reason") or "")))
            if len(candidates) == 3:
                break
        if not candidates:
            return MatchOutcome(product=None, confidence=0, method=method, candidates=[])
        best = candidates[0]
        return MatchOutcome(product=best.product, confidence=best.confidence, method=method, candidates=candidates)

    def _fuzzy(self, products: list[Product], raw_text: str) -> MatchOutcome:
        from difflib import SequenceMatcher

        query = fold(raw_text)
        tokens = set(query.split())
        scored: list[tuple[int, Product]] = []
        for product in products:
            score = int(round(SequenceMatcher(None, query, fold(product.product_name)).ratio() * 100))
            branded = fold(f"{product.brand} {product.product_name}")
            score = max(score, int(round(SequenceMatcher(None, query, branded).ratio() * 100)))
            score = max(score, self._contains_score(tokens, product.product_name))
            for alt in product.alt_list():
                score = max(score, int(round(SequenceMatcher(None, query, fold(alt)).ratio() * 100)))
                score = max(score, self._contains_score(tokens, alt))
            scored.append((score, product))
        scored.sort(key=lambda item: (-item[0], -len(fold(item[1].product_name).split()), item[1].sku))
        if (
            len(scored) >= 2
            and scored[0][0] >= self.threshold
            and scored[1][0] >= self.threshold
            and scored[0][0] - scored[1][0] <= 3
        ):
            scored[0] = (self.threshold - 1, scored[0][1])
        top = scored[:3]
        candidates = [Candidate(product=product, confidence=score, reason="fuzzy") for score, product in top if score > 0]
        if not candidates:
            return MatchOutcome(product=None, confidence=0, method="fuzzy", candidates=[])
        best = candidates[0]
        return MatchOutcome(product=best.product, confidence=best.confidence, method="fuzzy", candidates=candidates)

    def _contains_score(self, query_tokens: set[str], label: str) -> int:
        tokens = [token for token in fold(label).split() if len(token) > 1]
        if len(tokens) < 2:
            return 0
        if all(token in query_tokens for token in tokens):
            return 96
        return 0
