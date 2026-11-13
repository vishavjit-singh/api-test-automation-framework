"""Seed catalogue for the two demo tenants (fictional companies, synthetic products)."""
from __future__ import annotations

import random
from decimal import Decimal

TENANTS = {"acme": "ACME", "globex": "GLBX"}
NOUNS = ["Bottle", "Lamp", "Notebook", "Backpack", "Charger", "Mug", "Headphones", "Keyboard", "Mouse", "Cable"]
ADJ = ["Steel", "Compact", "Classic", "Travel", "Pro", "Eco"]


def seed(products_per_tenant: int = 24) -> dict[str, dict[str, dict]]:
    rng = random.Random(4)
    out: dict[str, dict[str, dict]] = {}
    for tenant, prefix in TENANTS.items():
        items = {}
        for i in range(1, products_per_tenant + 1):
            sku = f"{prefix}-{i:04d}"
            items[sku] = {"sku": sku, "name": f"{rng.choice(ADJ)} {rng.choice(NOUNS)}",
                          "price": Decimal(rng.randrange(9900, 499900)) / 100, "stock": 500,
                          "weight_g": rng.randrange(100, 2500, 50)}
        low = f"{prefix}-LOW1"
        items[low] = {"sku": low, "name": "Limited Edition Mug", "price": Decimal("749.50"), "stock": 2,
                      "weight_g": 400}
        out[tenant] = items
    return out
