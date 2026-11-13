"""Catalogue: pagination, lookups and parameter boundaries."""
import pytest

from apitest.client import expect
from apitest.schemas import ProductPage


@pytest.mark.case("API-TC-016")
@pytest.mark.positive
@pytest.mark.smoke
def test_first_page(acme):
    """Default page: 10 items, sorted by SKU, a total and a next cursor"""
    page = ProductPage.model_validate(expect(acme.get("/v1/products"), 200))
    assert len(page.items) == 10
    assert [p.sku for p in page.items] == sorted(p.sku for p in page.items)
    assert page.total == 25 and page.next_cursor


@pytest.mark.case("API-TC-017")
@pytest.mark.positive
def test_cursor_walks_every_product_once(acme):
    """Following next_cursor returns every product exactly once"""
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 7, **({"cursor": cursor} if cursor else {})}
        page = expect(acme.get("/v1/products", params=params), 200)
        seen += [p["sku"] for p in page["items"]]
        cursor, pages = page["next_cursor"], pages + 1
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == page["total"]
    assert pages == 4


@pytest.mark.case("API-TC-018")
@pytest.mark.positive
def test_get_product(acme):
    """A product by SKU matches its catalogue entry"""
    listed = expect(acme.get("/v1/products"), 200)["items"][0]
    assert expect(acme.get(f"/v1/products/{listed['sku']}"), 200) == listed


@pytest.mark.case("API-TC-019")
@pytest.mark.negative
def test_unknown_product(acme):
    """Unknown SKU → 404 not_found"""
    expect(acme.get("/v1/products/ACME-9999"), 404, "not_found")


@pytest.mark.case("API-TC-020")
@pytest.mark.edge
@pytest.mark.parametrize("limit, status", [(1, 200), (50, 200), (51, 422), (-1, 422), ("ten", 422)])
def test_limit_boundaries(acme, limit, status):
    """limit accepts 1..50; 51, negative and non-numeric values → 422"""
    expect(acme.get("/v1/products", params={"limit": limit}), status)


@pytest.mark.case("API-TC-021")
@pytest.mark.edge
@pytest.mark.known_defect("DEMO-301", "limit=0 returns 500 instead of 422")
def test_limit_zero_is_rejected(acme):
    """limit=0 → 422 validation_error"""
    expect(acme.get("/v1/products", params={"limit": 0}), 422, "validation_error")


@pytest.mark.case("API-TC-022")
@pytest.mark.edge
@pytest.mark.parametrize("cursor", ["not-base64!", "eyJvIjogLTF9", "e30", "AAAA"])
def test_invalid_cursor(acme, cursor):
    """Garbage, negative-offset and empty cursors → 422 invalid_cursor"""
    expect(acme.get("/v1/products", params={"cursor": cursor}), 422, "invalid_cursor")


@pytest.mark.case("API-TC-023")
@pytest.mark.edge
@pytest.mark.parametrize("sku", ["ACME-0001%20", "%C3%A9t%C3%A9", "..%2F..%2Fetc", "A" * 300])
def test_odd_skus_are_not_found_not_errors(acme, sku):
    """Encoded, unicode, traversal-like and very long SKUs → clean 404, never 5xx"""
    expect(acme.get(f"/v1/products/{sku}"), 404)
