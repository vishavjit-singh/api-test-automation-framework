"""Tenant isolation and role checks."""
import pytest

from apitest.client import expect


@pytest.mark.case("API-TC-009")
@pytest.mark.negative
def test_missing_tenant_header(api, tokens):
    """Valid token but no X-Tenant-ID → 400 missing_tenant"""
    r = api.with_headers({"Authorization": f"Bearer {tokens.mint(tenant='acme')}"}).get("/v1/products")
    expect(r, 400, "missing_tenant")


@pytest.mark.case("API-TC-010")
@pytest.mark.negative
@pytest.mark.smoke
def test_tenant_header_must_match_token(api, tokens):
    """Token for acme with X-Tenant-ID: globex → 403 tenant_mismatch"""
    client = api.as_tenant("acme").with_headers({"X-Tenant-ID": "globex"})
    expect(client.get("/v1/products"), 403, "tenant_mismatch")


@pytest.mark.case("API-TC-011")
@pytest.mark.negative
def test_unknown_tenant(api):
    """A correctly signed token for a tenant that is not provisioned → 403"""
    expect(api.as_tenant("initech").get("/v1/products"), 403, "unknown_tenant")


@pytest.mark.case("API-TC-012")
@pytest.mark.positive
def test_catalogue_is_tenant_scoped(acme, globex):
    """Each tenant sees only its own SKUs"""
    a = expect(acme.get("/v1/products", params={"limit": 50}), 200)["items"]
    g = expect(globex.get("/v1/products", params={"limit": 50}), 200)["items"]
    assert a and g
    assert all(p["sku"].startswith("ACME-") for p in a)
    assert all(p["sku"].startswith("GLBX-") for p in g)


@pytest.mark.case("API-TC-013")
@pytest.mark.negative
@pytest.mark.smoke
def test_other_tenants_order_reads_as_not_found(globex, place_order):
    """An acme order id requested by globex → 404 (not 403: no existence leak)"""
    order = place_order()
    expect(globex.get(f"/v1/orders/{order['id']}"), 404, "not_found")


@pytest.mark.case("API-TC-014")
@pytest.mark.negative
def test_other_tenant_cannot_cancel(globex, acme, place_order):
    """globex cannot cancel an acme order, and the order stays confirmed"""
    order = place_order()
    expect(globex.post(f"/v1/orders/{order['id']}/cancel"), 404, "not_found")
    assert expect(acme.get(f"/v1/orders/{order['id']}"), 200)["status"] == "confirmed"


@pytest.mark.case("API-TC-015")
@pytest.mark.negative
@pytest.mark.parametrize("roles, method, path", [
    (("catalog:read",), "POST", "/v1/orders"),
    (("orders:read",), "POST", "/v1/orders/ord_0000000000000000/cancel"),
    (("orders:read",), "GET", "/v1/products"),
    (("catalog:read",), "GET", "/v1/shipping/quote?country=IN&weight_g=500"),
    (("orders:write", "orders:read", "catalog:read"), "PUT", "/_test/faults"),
], ids=["catalog-reader-creates-order", "order-reader-cancels", "order-reader-lists-catalogue",
        "catalog-reader-quotes", "non-admin-sets-faults"])
def test_missing_role(api, env, roles, method, path):
    """Calls without the required role → 403 insufficient_scope"""
    if path.startswith("/_test") and not env.test_hooks:
        pytest.skip("test hooks are disabled on this target")
    client = api.as_tenant("acme", roles=roles)
    r = client.request(method, path, json={"carrier": "ok"} if method == "PUT" else None,
                       headers={"Idempotency-Key": "qa-role-check"})
    expect(r, 403, "insufficient_scope")
