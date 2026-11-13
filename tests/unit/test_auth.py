import jwt
import pytest

from apitest.auth import TokenFactory
from apitest.config import AuthSettings

AUTH = AuthSettings(issuer="https://auth.example.test", audience="orders-api", secret="k" * 32)


@pytest.fixture
def tf():
    return TokenFactory(AUTH)


def decode(token, **kw):
    return jwt.decode(token, AUTH.secret, algorithms=["HS256"], audience=AUTH.audience, issuer=AUTH.issuer,
                      options={"verify_exp": False, "verify_nbf": False, **kw.pop("options", {})}, **kw)


def test_claims(tf):
    c = decode(tf.mint(tenant="globex", roles=["orders:read"], sub="u7"))
    assert c["tenant_id"] == "globex" and c["roles"] == ["orders:read"] and c["sub"] == "u7"
    assert c["exp"] - c["iat"] == 300 and c["jti"]


def test_overrides_and_removal(tf):
    assert "tenant_id" not in decode(tf.mint(tenant=None))
    assert "sub" not in decode(tf.without("sub"))
    assert decode(tf.mint(custom="x"))["custom"] == "x"


@pytest.mark.parametrize("kind, error", [
    ("wrong_audience", jwt.InvalidAudienceError),
    ("wrong_issuer", jwt.InvalidIssuerError),
    ("bad_signature", jwt.InvalidSignatureError),
])
def test_broken_tokens_fail_validation(tf, kind, error):
    with pytest.raises(error):
        decode(getattr(tf, kind)())


def test_expired_and_not_yet_valid(tf):
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(tf.expired(), AUTH.secret, algorithms=["HS256"], audience=AUTH.audience,
                   options={"verify_nbf": False}, leeway=0)
    c = decode(tf.not_yet_valid())
    assert c["nbf"] > c["iat"]


def test_alg_none_is_unsigned(tf):
    token = tf.alg_none()
    assert token.endswith(".") and jwt.get_unverified_header(token)["alg"] == "none"
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode(token)


def test_tampered_keeps_old_signature(tf):
    good = tf.mint(tenant="acme")
    forged = tf.tampered(good, tenant_id="globex")
    assert forged.split(".")[2] == good.split(".")[2]
    assert jwt.decode(forged, options={"verify_signature": False})["tenant_id"] == "globex"
    with pytest.raises(jwt.InvalidSignatureError):
        decode(forged)
