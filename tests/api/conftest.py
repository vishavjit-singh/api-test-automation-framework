import uuid

import pytest


@pytest.fixture(scope="session", autouse=True)
def _fresh_service_state(env, admin):
    """Reseed the demo service once per run when the target allows test hooks (never on staging)."""
    if env.test_hooks:
        resp = admin.post("/_test/reset")
        assert resp.status_code == 200, f"could not reset the service: {resp.status_code} {resp.text}"


@pytest.fixture
def idem():
    """A fresh Idempotency-Key per call."""
    return lambda: f"qa-{uuid.uuid4()}"


@pytest.fixture
def order_body():
    def make(*items, country="IN", postcode="160062"):
        lines = items or (("ACME-0001", 1),)
        return {"items": [{"sku": s, "qty": q} for s, q in lines], "shipping": {"country": country,
                                                                              "postcode": postcode}}
    return make


@pytest.fixture
def place_order(acme, idem, order_body):
    """Create an order for tenant acme and return its JSON."""
    def place(*items, client=None, **kw):
        r = (client or acme).post("/v1/orders", json=order_body(*items, **kw), headers={"Idempotency-Key": idem()})
        assert r.status_code == 201, r.text
        return r.json()
    return place
