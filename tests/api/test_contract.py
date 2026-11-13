"""Cross-cutting contract: error envelope, request tracing, unknown routes and production hardening."""
import pytest

from apitest.client import expect
from apitest.schemas import ErrorEnvelope


@pytest.mark.case("API-TC-052")
@pytest.mark.negative
@pytest.mark.parametrize("call", [
    lambda c, a: a.get("/v1/products"),
    lambda c, a: c.get("/v1/products/NOPE"),
    lambda c, a: c.post("/v1/orders", json={}),
    lambda c, a: c.post("/v1/orders", json={"items": []}, headers={"Idempotency-Key": "qa-envelope"}),
    lambda c, a: c.get("/v1/nothing-here"),
], ids=["401", "404", "400", "422", "unknown-route"])
def test_error_envelope(acme, api, call):
    """Every error uses one envelope: error.code, error.message, error.request_id"""
    r = call(acme, api)
    assert r.status_code >= 400
    err = ErrorEnvelope.model_validate(r.json()).error
    assert err.request_id == r.headers["x-request-id"]


@pytest.mark.case("API-TC-053")
@pytest.mark.positive
def test_request_id_is_echoed(acme):
    """A caller-supplied X-Request-ID comes back unchanged; otherwise one is generated"""
    r = acme.get("/v1/products", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["x-request-id"] == "trace-abc-123"
    r = acme.get("/v1/products", headers={"X-Request-ID": None})
    assert r.headers["x-request-id"].startswith("req_")


@pytest.mark.case("API-TC-054")
@pytest.mark.negative
def test_method_not_allowed(acme):
    """DELETE on a collection → 405 in the standard envelope"""
    expect(acme.delete("/v1/orders"), 405, "method_not_allowed")


@pytest.mark.case("API-TC-055")
@pytest.mark.edge
def test_test_hooks_absent_in_prod(env, tokens):
    """With APP_ENV=prod the fault and reset hooks do not exist (404), even for admins"""
    if env.mode != "inproc":
        pytest.skip("needs an in-process prod-mode instance")
    from starlette.testclient import TestClient

    from demo_service.app import ServiceSettings, create_app

    prod = TestClient(create_app(ServiceSettings(app_env="prod", jwt_secret=env.auth.secret)))
    headers = {"Authorization": f"Bearer {tokens.mint(roles=['admin'])}", "X-Tenant-ID": "acme"}
    assert prod.post("/_test/reset", headers=headers).status_code == 404
    assert prod.put("/_test/faults", headers=headers, json={"carrier": "error"}).status_code == 404
