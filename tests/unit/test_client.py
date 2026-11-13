import httpx
import pytest

from apitest.auth import TokenFactory
from apitest.client import ApiClient, Recorder, describe, expect
from apitest.config import load_environment

ENV = load_environment("local", env={})


def make(handler, retries=1):
    env = ENV if retries == ENV.retries else ENV.__class__(**{**ENV.__dict__, "retries": retries})
    http = httpx.Client(base_url=env.base_url, transport=httpx.MockTransport(handler))
    sleeps = []
    return ApiClient(env, TokenFactory(env.auth), http, Recorder(env.routes), sleep=sleeps.append), sleeps


def test_tenant_view_adds_auth_tenant_and_request_id():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json={})

    client, _ = make(handler)
    client.as_tenant("globex", roles=["orders:read"]).get("/v1/orders/ord_1")
    assert seen["authorization"].startswith("Bearer ")
    assert seen["x-tenant-id"] == "globex" and seen["x-request-id"].startswith("qa-")


def test_header_override_and_removal():
    seen = {}

    def handler(req):
        seen.clear()
        seen.update(req.headers)
        return httpx.Response(200)

    client, _ = make(handler)
    acme = client.as_tenant("acme")
    acme.get("/x", headers={"X-Tenant-ID": "globex"})
    assert seen["x-tenant-id"] == "globex"
    acme.get("/x", headers={"Authorization": None})
    assert "authorization" not in seen


def test_get_is_retried_on_503():
    statuses = iter([503, 200])
    client, sleeps = make(lambda req: httpx.Response(next(statuses)))
    assert client.get("/v1/products").status_code == 200
    assert [c["attempt"] for c in client.recorder.calls] == [1, 2] and sleeps == [0.2]


def test_post_is_never_retried():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(503)

    client, _ = make(handler, retries=3)
    assert client.post("/v1/orders", json={}).status_code == 503
    assert len(calls) == 1


def test_connection_errors_are_retried_then_raised():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    client, sleeps = make(handler, retries=2)
    with pytest.raises(httpx.ConnectError):
        client.get("/health")
    assert len(client.recorder.calls) == 3 and client.recorder.calls[0]["status"] is None


def test_routes_are_templated():
    r = Recorder(ENV.routes)
    assert r.route("/v1/orders/ord_0123456789abcdef/cancel") == "/v1/orders/{id}/cancel"
    assert r.route("/v1/orders/ord_0123456789abcdef") == "/v1/orders/{id}"
    assert r.route("/v1/products/ACME-0001?x=1") == "/v1/products/{sku}"
    assert r.route("/v1/products") == "/v1/products"


def test_failure_messages_redact_the_token():
    client, _ = make(lambda req: httpx.Response(403, json={"error": {"code": "tenant_mismatch"}}))
    resp = client.as_tenant("acme").get("/v1/products")
    text = describe(resp)
    assert "Bearer ***" in text and "eyJ" not in text and "tenant_mismatch" in text
    with pytest.raises(AssertionError, match="expected 200"):
        expect(resp, 200)
    with pytest.raises(AssertionError, match="expected error code 'other'"):
        expect(resp, 403, "other")
    assert expect(resp, 403, "tenant_mismatch")["error"]["code"] == "tenant_mismatch"
