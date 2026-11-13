"""Orders: creation, idempotency, stock, cancellation and request validation."""
from decimal import Decimal

import pytest

from apitest.client import expect
from apitest.schemas import Order


def stock(client, sku):
    return expect(client.get(f"/v1/products/{sku}"), 200)["stock"]


@pytest.mark.case("API-TC-024")
@pytest.mark.positive
@pytest.mark.smoke
def test_create_order(acme, idem, order_body):
    """201 with Location, correct totals and stock reserved"""
    before = {s: stock(acme, s) for s in ("ACME-0002", "ACME-0003")}
    r = acme.post("/v1/orders", json=order_body(("ACME-0002", 2), ("ACME-0003", 1)),
                  headers={"Idempotency-Key": idem()})
    order = Order.model_validate(expect(r, 201))
    assert r.headers["location"] == f"/v1/orders/{order.id}"
    assert order.status == "confirmed" and order.tenant_id == "acme"
    subtotal = sum(Decimal(i.unit_price) * i.qty for i in order.items)
    assert Decimal(order.subtotal) == subtotal
    assert Decimal(order.total) == subtotal + Decimal(order.shipping)
    assert stock(acme, "ACME-0002") == before["ACME-0002"] - 2
    assert stock(acme, "ACME-0003") == before["ACME-0003"] - 1


@pytest.mark.case("API-TC-025")
@pytest.mark.positive
def test_read_back_order(acme, place_order):
    """GET returns exactly what POST returned"""
    order = place_order(("ACME-0004", 3))
    assert expect(acme.get(f"/v1/orders/{order['id']}"), 200) == order


@pytest.mark.case("API-TC-026")
@pytest.mark.positive
def test_cancel_restores_stock(acme, place_order):
    """Cancelling sets status cancelled and returns the reserved stock"""
    before = stock(acme, "ACME-0005")
    order = place_order(("ACME-0005", 4))
    assert stock(acme, "ACME-0005") == before - 4
    assert expect(acme.post(f"/v1/orders/{order['id']}/cancel"), 200)["status"] == "cancelled"
    assert stock(acme, "ACME-0005") == before


@pytest.mark.case("API-TC-027")
@pytest.mark.positive
@pytest.mark.smoke
def test_idempotent_replay(acme, idem, order_body):
    """Same Idempotency-Key and body → same order, replay header, stock reserved once"""
    before = stock(acme, "ACME-0006")
    key, body = idem(), order_body(("ACME-0006", 2))
    first = expect(acme.post("/v1/orders", json=body, headers={"Idempotency-Key": key}), 201)
    again = acme.post("/v1/orders", json=body, headers={"Idempotency-Key": key})
    assert expect(again, 201)["id"] == first["id"]
    assert again.headers.get("idempotent-replayed") == "true"
    assert stock(acme, "ACME-0006") == before - 2


@pytest.mark.case("API-TC-028")
@pytest.mark.negative
def test_idempotency_key_required(acme, order_body):
    """POST without Idempotency-Key → 400"""
    expect(acme.post("/v1/orders", json=order_body()), 400, "missing_idempotency_key")


@pytest.mark.case("API-TC-029")
@pytest.mark.negative
def test_idempotency_key_reuse_with_new_body(acme, idem, order_body):
    """Reusing a key with a different body → 422 and no second order"""
    key = idem()
    expect(acme.post("/v1/orders", json=order_body(("ACME-0007", 1)), headers={"Idempotency-Key": key}), 201)
    r = acme.post("/v1/orders", json=order_body(("ACME-0007", 2)), headers={"Idempotency-Key": key})
    expect(r, 422, "idempotency_key_reused")


@pytest.mark.case("API-TC-030")
@pytest.mark.negative
def test_unknown_sku(acme, idem, order_body):
    """Ordering a SKU that does not exist → 422 unknown_sku listing it"""
    r = acme.post("/v1/orders", json=order_body(("ACME-0001", 1), ("NOPE-1", 1)), headers={"Idempotency-Key": idem()})
    assert expect(r, 422, "unknown_sku")["error"]["skus"] == ["NOPE-1"]


@pytest.mark.case("API-TC-031")
@pytest.mark.negative
def test_insufficient_stock(acme, idem, order_body, place_order):
    """Ordering more than is in stock → 409 insufficient_stock, stock unchanged"""
    place_order(("ACME-LOW1", 2))                                   # takes the last two
    r = acme.post("/v1/orders", json=order_body(("ACME-LOW1", 1)), headers={"Idempotency-Key": idem()})
    assert expect(r, 409, "insufficient_stock")["error"]["skus"] == ["ACME-LOW1"]
    assert stock(acme, "ACME-LOW1") == 0


@pytest.mark.case("API-TC-032")
@pytest.mark.negative
def test_cancel_twice(acme, place_order):
    """Second cancel → 409 invalid_state"""
    order = place_order()
    expect(acme.post(f"/v1/orders/{order['id']}/cancel"), 200)
    expect(acme.post(f"/v1/orders/{order['id']}/cancel"), 409, "invalid_state")


@pytest.mark.case("API-TC-033")
@pytest.mark.negative
@pytest.mark.parametrize("body, loc", [
    ({"items": [{"sku": "ACME-0001", "qty": 1}], "shipping": {"country": "IN", "postcode": "160062"}, "coupon": "X"},
     "coupon"),
    ({"items": [{"sku": "ACME-0001", "qty": "2"}], "shipping": {"country": "IN", "postcode": "160062"}},
     "items.0.qty"),
    ({"items": [{"sku": "ACME-0001", "qty": 1}], "shipping": {"country": "FR", "postcode": "75001"}},
     "shipping.country"),
    ({"items": [{"sku": "ACME-0001", "qty": 1}]}, "shipping"),
    ({"items": [{"sku": "ACME-0001", "qty": 1.5}], "shipping": {"country": "IN", "postcode": "160062"}},
     "items.0.qty"),
], ids=["unknown-field", "qty-as-string", "unsupported-country", "missing-shipping", "fractional-qty"])
def test_validation_errors_point_at_the_field(acme, idem, body, loc):
    """Schema violations → 422 validation_error whose errors[].loc names the bad field"""
    data = expect(acme.post("/v1/orders", json=body, headers={"Idempotency-Key": idem()}), 422, "validation_error")
    assert loc in [e["loc"] for e in data["error"]["errors"]]


@pytest.mark.case("API-TC-034")
@pytest.mark.negative
def test_malformed_json(acme, idem):
    """A body that is not JSON → 400 malformed_json"""
    r = acme.post("/v1/orders", content=b'{"items": [', headers={"Idempotency-Key": idem(),
                                                                "Content-Type": "application/json"})
    expect(r, 400, "malformed_json")


@pytest.mark.case("API-TC-035")
@pytest.mark.negative
def test_wrong_content_type(acme, idem):
    """form-encoded body → 415"""
    r = acme.post("/v1/orders", data={"items": "x"}, headers={"Idempotency-Key": idem()})
    expect(r, 415, "unsupported_media_type")


@pytest.mark.case("API-TC-036")
@pytest.mark.edge
@pytest.mark.parametrize("qty, status", [(1, 201), (100, 201), (0, 422), (101, 422), (-1, 422)])
def test_quantity_boundaries(acme, idem, order_body, qty, status):
    """qty accepts 1..100"""
    expect(acme.post("/v1/orders", json=order_body(("ACME-0008", qty)), headers={"Idempotency-Key": idem()}), status)


@pytest.mark.case("API-TC-037")
@pytest.mark.edge
@pytest.mark.parametrize("n, status", [(0, 422), (1, 201), (20, 201), (21, 422)])
def test_line_count_boundaries(acme, idem, order_body, n, status):
    """An order has 1..20 lines"""
    lines = [(f"ACME-{i:04d}", 1) for i in range(1, n + 1)]
    body = order_body(*lines) if n else {"items": [], "shipping": {"country": "IN", "postcode": "160062"}}
    expect(acme.post("/v1/orders", json=body, headers={"Idempotency-Key": idem()}), status)


@pytest.mark.case("API-TC-038")
@pytest.mark.edge
def test_duplicate_sku_lines(acme, idem, order_body):
    """The same SKU twice in one order → 422 (quantities must be combined by the client)"""
    r = acme.post("/v1/orders", json=order_body(("ACME-0009", 1), ("ACME-0009", 2)),
                  headers={"Idempotency-Key": idem()})
    expect(r, 422, "validation_error")


@pytest.mark.case("API-TC-039")
@pytest.mark.edge
@pytest.mark.parametrize("postcode, status", [("123", 201), ("1234567890", 201), ("12", 422), ("12345678901", 422)])
def test_postcode_length(acme, idem, order_body, postcode, status):
    """postcode length 3..10"""
    expect(acme.post("/v1/orders", json=order_body(postcode=postcode), headers={"Idempotency-Key": idem()}), status)


@pytest.mark.case("API-TC-040")
@pytest.mark.edge
def test_payload_too_large(acme, idem):
    """A body over 64 KB → 413 before any parsing"""
    body = b'{"items": [], "pad": "' + b"x" * (65 * 1024) + b'"}'
    r = acme.post("/v1/orders", content=body, headers={"Idempotency-Key": idem(), "Content-Type": "application/json"})
    expect(r, 413, "payload_too_large")


@pytest.mark.case("API-TC-041")
@pytest.mark.edge
def test_money_is_exact(acme, place_order):
    """Totals are exact decimals (no float drift) for awkward prices and quantities"""
    order = place_order(("ACME-0010", 99))
    unit = Decimal(order["items"][0]["unit_price"])
    assert Decimal(order["subtotal"]) == unit * 99
    assert len(order["total"].split(".")[1]) == 2


@pytest.mark.case("API-TC-042")
@pytest.mark.negative
def test_unknown_order(acme):
    """Unknown order id → 404"""
    expect(acme.get("/v1/orders/ord_ffffffffffffffff"), 404, "not_found")
