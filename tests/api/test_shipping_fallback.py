"""Shipping quotes and the fallback path when the upstream carrier misbehaves.

Fault injection uses the service's test hooks, so this tier is skipped on targets without them.
"""
import time
from decimal import Decimal

import pytest

from apitest.client import expect

QUOTE = "/v1/shipping/quote"


@pytest.mark.case("API-TC-043")
@pytest.mark.positive
@pytest.mark.smoke
def test_quote_from_carrier(acme):
    """Healthy carrier → source=carrier, no fallback header"""
    r = acme.get(QUOTE, params={"country": "IN", "weight_g": 1200})
    q = expect(r, 200)
    assert q["source"] == "carrier" and Decimal(q["amount"]) > 0
    assert "x-fallback-reason" not in r.headers


@pytest.mark.case("API-TC-044")
@pytest.mark.negative
@pytest.mark.parametrize("params", [{"country": "FR", "weight_g": 500}, {"country": "IN"},
                                    {"country": "IN", "weight_g": "heavy"}], ids=["country", "no-weight", "text"])
def test_quote_validation(acme, params):
    """Unsupported country, missing or non-numeric weight → 422"""
    expect(acme.get(QUOTE, params=params), 422, "validation_error")


@pytest.mark.case("API-TC-045")
@pytest.mark.edge
@pytest.mark.parametrize("weight, status", [(1, 200), (30000, 200), (0, 422), (30001, 422)])
def test_quote_weight_boundaries(acme, weight, status):
    """weight_g accepts 1..30000"""
    expect(acme.get(QUOTE, params={"country": "DE", "weight_g": weight}), status)


@pytest.mark.case("API-TC-046")
@pytest.mark.fallback
@pytest.mark.parametrize("fault, reason", [("timeout", "carrier_timeout"), ("error", "carrier_error"),
                                           ("malformed", "carrier_bad_response")])
def test_fallback_quote(acme, hooks, fault, reason):
    """Carrier timeout, 5xx or malformed payload → flat-rate fallback with X-Fallback-Reason"""
    hooks(fault)
    r = acme.get(QUOTE, params={"country": "IN", "weight_g": 900})
    q = expect(r, 200)
    assert q["source"] == "fallback"
    assert r.headers["x-fallback-reason"] == reason
    assert Decimal(q["amount"]) == Decimal("80.00")


@pytest.mark.case("API-TC-047")
@pytest.mark.fallback
def test_fallback_respects_latency_budget(acme, hooks):
    """A hanging carrier is cut off by the service timeout: the fallback answer comes back in under 1 s"""
    hooks("timeout")
    start = time.perf_counter()
    expect(acme.get(QUOTE, params={"country": "US", "weight_g": 400}), 200)
    assert time.perf_counter() - start < 1.0


@pytest.mark.case("API-TC-048")
@pytest.mark.fallback
@pytest.mark.smoke
def test_orders_survive_a_carrier_outage(acme, hooks, place_order):
    """Orders placed while the carrier is down use the fallback rate and say so"""
    hooks("error")
    order = place_order(("ACME-0011", 1))
    assert order["status"] == "confirmed"
    assert order["shipping_source"] == "fallback" and order["shipping_fallback_reason"] == "carrier_error"


@pytest.mark.case("API-TC-049")
@pytest.mark.fallback
def test_circuit_breaker_opens(acme, hooks):
    """After 3 consecutive carrier failures the circuit opens and the carrier is no longer called"""
    hooks("error")
    for _ in range(3):
        acme.get(QUOTE, params={"country": "IN", "weight_g": 500})
    calls = hooks.status()["calls"]
    r = acme.get(QUOTE, params={"country": "IN", "weight_g": 500})
    assert r.headers["x-fallback-reason"] == "circuit_open"
    assert hooks.status() == {"carrier": "error", "calls": calls, "consecutive_failures": 3, "circuit": "open"}


@pytest.mark.case("API-TC-050")
@pytest.mark.fallback
def test_recovery_after_reset(acme, hooks):
    """Once the carrier is healthy again, quotes come from the carrier and the failure count clears"""
    hooks("error")
    acme.get(QUOTE, params={"country": "IN", "weight_g": 500})
    hooks("ok")
    q = expect(acme.get(QUOTE, params={"country": "IN", "weight_g": 500}), 200)
    assert q["source"] == "carrier"
    assert hooks.status()["consecutive_failures"] == 0


@pytest.mark.case("API-TC-051")
@pytest.mark.fallback
@pytest.mark.negative
def test_fault_mode_is_validated(hooks, admin):
    """The fault hook itself rejects unknown modes"""
    expect(admin.put("/_test/faults", json={"carrier": "on-fire"}), 422, "validation_error")
