"""Authentication: every way a bearer token can be wrong must give 401, never data."""
import pytest

from apitest.client import expect

PRODUCTS = "/v1/products"


@pytest.mark.case("API-TC-001")
@pytest.mark.positive
@pytest.mark.smoke
def test_health_is_public(api):
    """Health endpoint answers without a token"""
    assert expect(api.get("/health"), 200)["status"] == "ok"


@pytest.mark.case("API-TC-002")
@pytest.mark.negative
@pytest.mark.smoke
def test_missing_token(api):
    """No Authorization header → 401 missing_token with a Bearer challenge"""
    r = api.with_headers({"X-Tenant-ID": "acme"}).get(PRODUCTS)
    expect(r, 401, "missing_token")
    assert r.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.case("API-TC-003")
@pytest.mark.negative
def test_expired_token(api, tokens):
    """Expired token → 401 token_expired"""
    expect(api.as_tenant("acme", token=tokens.expired()).get(PRODUCTS), 401, "token_expired")


@pytest.mark.case("API-TC-004")
@pytest.mark.negative
@pytest.mark.parametrize("kind", ["wrong_audience", "wrong_issuer", "bad_signature", "alg_none", "not_yet_valid"])
def test_rejected_tokens(api, tokens, kind):
    """Wrong audience, wrong issuer, bad signature, alg=none and nbf in the future → 401 invalid_token"""
    expect(api.as_tenant("acme", token=getattr(tokens, kind)()).get(PRODUCTS), 401, "invalid_token")


@pytest.mark.case("API-TC-005")
@pytest.mark.negative
def test_tampered_payload(api, tokens):
    """A signed token whose payload was edited (tenant swapped) → 401"""
    forged = tokens.tampered(tokens.mint(tenant="acme"), tenant_id="globex")
    expect(api.as_tenant("globex", token=forged).get(PRODUCTS), 401, "invalid_token")


@pytest.mark.case("API-TC-006")
@pytest.mark.negative
@pytest.mark.parametrize("claim", ["exp", "sub", "aud", "tenant_id"])
def test_missing_required_claim(api, tokens, claim):
    """Tokens missing exp, sub, aud or tenant_id → 401"""
    expect(api.as_tenant("acme", token=tokens.without(claim)).get(PRODUCTS), 401, "invalid_token")


@pytest.mark.case("API-TC-007")
@pytest.mark.negative
@pytest.mark.parametrize("header", ["Token abc", "Bearer", "Bearer not.a.jwt", "Basic dXNlcjpwYXNz"])
def test_malformed_authorization_header(api, header):
    """Wrong scheme, empty bearer, garbage token, Basic credentials → 401 invalid_token"""
    expect(api.with_headers({"Authorization": header, "X-Tenant-ID": "acme"}).get(PRODUCTS), 401, "invalid_token")


@pytest.mark.case("API-TC-008")
@pytest.mark.edge
def test_token_valid_for_the_last_second(api, tokens):
    """A token with ttl=5s is still accepted (no clock-skew off-by-one)"""
    expect(api.as_tenant("acme", token=tokens.mint(ttl=5)).get(PRODUCTS), 200)
