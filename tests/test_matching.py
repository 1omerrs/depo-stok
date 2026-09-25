from app.config import Settings
from app.matching import Matcher
from app.models import Product


def settings():
    return Settings(
        data_backend="sqlite",
        sqlite_path=":memory:",
        airtable_api_key="",
        airtable_base_id="",
        airtable_products="products",
        airtable_locations="locations",
        airtable_stock="stock",
        airtable_orders="orders",
        airtable_returns="returns",
        panel_username="admin",
        panel_password="secret",
        panel_secret="x",
        public_base_url="http://testserver",
        auto_match_threshold=90,
        llm_api_key="test-key",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        llm_provider="openai",
        notify_email="depo@example.com",
        webhook_secret="",
        inspect_webhook="",
        approval_webhook="",
        signup_form="",
    )


def products():
    return [
        Product(id="1", sku="AULA-F75", product_name="Aula F75", brand="Aula", category="Klavye", barcode="8690000000011"),
        Product(id="2", sku="AULA-PRO", product_name="Aula F75 RGB", brand="Aula", category="Klavye", barcode="8690000000099"),
        Product(id="3", sku="HX-C2", product_name="HyperX Cloud II", brand="HyperX", category="Kulaklık", barcode="8690000000059"),
    ]


class Spy:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def rank(self, raw_text, catalog):
        self.calls += 1
        return self.payload


def test_barcode_does_not_call_llm():
    spy = Spy([{"sku": "HX-C2", "confidence": 10}])
    outcome = Matcher(settings(), spy).match(products(), "asdf tamamen baska", barcode="8690000000011")
    assert outcome.method == "barcode"
    assert outcome.confidence == 100
    assert outcome.product.sku == "AULA-F75"
    assert spy.calls == 0


def test_non_exact_uses_llm_score_without_fuzzy_upgrade():
    spy = Spy([{"sku": "AULA-F75", "confidence": 62}])
    outcome = Matcher(settings(), spy).match(products(), "Aula F75 klavye")
    assert spy.calls == 1
    assert outcome.method == "llm"
    assert outcome.confidence == 62
    assert outcome.is_auto(90) is False


def test_fuzzy_when_llm_missing():
    bare = settings()
    bare.llm_api_key = ""
    outcome = Matcher(bare, None).match(products()[:1], "Aula F75 klavye")
    assert outcome.method == "fuzzy"
    assert outcome.product.sku == "AULA-F75"
    assert outcome.confidence >= 90


def test_ambiguous_names_stay_manual():
    bare = settings()
    bare.llm_api_key = ""
    outcome = Matcher(bare, None).match(products(), "Aula F75 RGB klavye")
    assert outcome.confidence < 90
    assert {item.product.sku for item in outcome.candidates} >= {"AULA-F75", "AULA-PRO"}
