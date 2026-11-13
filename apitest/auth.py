"""JWT helper: mint valid tokens for any tenant and role set, and the broken ones negative tests need."""
from __future__ import annotations

import base64
import json
import time
import uuid

import jwt

from .config import AuthSettings

ALL_ROLES = ("catalog:read", "orders:read", "orders:write")


def _b64(data: dict | bytes) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class TokenFactory:
    def __init__(self, auth: AuthSettings, clock=time.time):
        self.auth = auth
        self.clock = clock

    def claims(self, *, sub: str = "qa-bot", tenant: str | None = "acme", roles=ALL_ROLES, ttl: int = 300,
               **overrides) -> dict:
        now = int(self.clock())
        c = {"iss": self.auth.issuer, "aud": self.auth.audience, "sub": sub, "iat": now, "nbf": now,
             "exp": now + ttl, "jti": uuid.uuid4().hex, "roles": list(roles)}
        if tenant is not None:
            c["tenant_id"] = tenant
        c.update(overrides)
        return {k: v for k, v in c.items() if v is not None}

    def mint(self, secret: str | None = None, **kw) -> str:
        return jwt.encode(self.claims(**kw), secret or self.auth.secret, algorithm="HS256")

    # ---- deliberately broken tokens for negative tests
    def expired(self, **kw) -> str:
        return self.mint(ttl=-60, **kw)

    def not_yet_valid(self, **kw) -> str:
        return self.mint(nbf=int(self.clock()) + 600, **kw)

    def wrong_audience(self, **kw) -> str:
        return self.mint(aud="some-other-api", **kw)

    def wrong_issuer(self, **kw) -> str:
        return self.mint(iss="https://evil.example.test", **kw)

    def bad_signature(self, **kw) -> str:
        return self.mint(secret="not-the-real-secret-0123456789abcdef", **kw)

    def without(self, claim: str, **kw) -> str:
        c = self.claims(**kw)
        c.pop(claim, None)
        return jwt.encode(c, self.auth.secret, algorithm="HS256")

    def alg_none(self, **kw) -> str:
        """Unsigned token (``alg: none``): a classic bypass attempt that must be rejected."""
        return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(self.claims(**kw))}."

    def tampered(self, token: str, **changes) -> str:
        """Edit the payload of a validly signed token and keep the old signature."""
        header, payload, sig = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims.update(changes)
        return f"{header}.{_b64(claims)}.{sig}"
