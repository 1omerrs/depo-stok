from __future__ import annotations

import uuid

from app.models import Location, Product, StockRow

PRODUCTS = [
    ("AULA-F75", "Aula F75", "Aula", "Klavye", "8690000000011"),
    ("KEY-K2", "Keychron K2", "Keychron", "Klavye", "8690000000028"),
    ("LOGI-MX3", "Logitech MX Master 3S", "Logitech", "Mouse", "8690000000035"),
    ("SS-QCK", "SteelSeries QcK Large", "SteelSeries", "Mousepad", "8690000000042"),
    ("HX-C2", "HyperX Cloud II", "HyperX", "Kulaklık", "8690000000059"),
]

LOCATIONS = [
    ("A", "101", 30),
    ("A", "103", 15),
    ("B", "101", 40),
    ("B", "109", None),
    ("C", "105", 8),
    ("E", "101", 20),
]

# sku, block, shelf, qty
STOCK = [
    ("AULA-F75", "A", "101", 6),
    ("AULA-F75", "B", "109", 4),
    ("KEY-K2", "A", "103", 2),
    ("LOGI-MX3", "B", "101", 10),
    ("SS-QCK", "C", "105", 8),
    ("HX-C2", "E", "101", 3),
]


def seed_if_empty(store) -> None:
    if store.list_products():
        return
    products = {}
    for sku, name, brand, category, barcode in PRODUCTS:
        product = Product(
            id=str(uuid.uuid4()),
            sku=sku,
            product_name=name,
            brand=brand,
            category=category,
            barcode=barcode,
        )
        store.insert_product(product)
        products[sku] = product
    locations = {}
    for block, shelf, capacity in LOCATIONS:
        location = Location(id=str(uuid.uuid4()), block=block, shelf_code=shelf, capacity=capacity, active=True)
        store.insert_location(location)
        locations[(block, shelf)] = location
    for sku, block, shelf, qty in STOCK:
        store.insert_stock(
            StockRow(
                id=str(uuid.uuid4()),
                product_id=products[sku].id,
                location_id=locations[(block, shelf)].id,
                quantity=qty,
            )
        )
