"""One shared HTTP client per test session, handed out as tenant-scoped views.

* The underlying ``httpx.Client`` (or an in-process ASGI client) is created once: connection reuse,
  one place for timeouts and retries.
* ``api.as_tenant("acme", roles=...)`` returns a light view that adds ``Authorization``,
  ``X-Tenant-ID`` and a fresh ``X-Request-ID`` to every call. Raw headers can still be overridden,
  which is what negative tests do.
* GET/HEAD are retried on 502/503/504 and connection errors; POST/PUT never are, so a retry can't
  create a second order.
* Every call is recorded (route template, status, latency, test) for the run report.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx

from .auth import ALL_ROLES, TokenFactory
from .config import Environment

RETRYABLE_STATUS = {502, 503, 504}
IDEMPOTENT = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Recorder:
    routes: tuple[tuple[str, str], ...] = ()
    calls: list[dict] = field(default_factory=list)
    current_test: str | None = None

    def route(self, path: str) -> str:
        path = path.split("?", 1)[0]
        for pattern, template in self.routes:
            if re.match(pattern, path):
                return template
        return path

    def add(self, method: str, path: str, status: int | None, ms: float, attempt: int) -> None:
        self.calls.append({"test": self.current_test, "method": method, "route": self.route(path), "status": status,
                           "ms": round(ms, 2), "attempt": attempt})


def build_transport_client(env: Environment, app=None) -> httpx.Client:
    if env.mode == "inproc":
        from starlette.testclient import TestClient

        if app is None:
            from demo_service.app import create_app
            app = create_app()
        return TestClient(app, base_url=env.base_url, raise_server_exceptions=False)
    return httpx.Client(base_url=env.base_url, timeout=env.timeout_s)


def redact(headers: dict) -> dict:
    return {k: ("Bearer ***" if k.lower() == "authorization" else v) for k, v in headers.items()}


def describe(resp: httpx.Response, limit: int = 600) -> str:
    """Human-readable request/response summary for assertion messages (tokens redacted)."""
    req = resp.request
    body = resp.text if len(resp.text) <= limit else resp.text[:limit] + "…"
    return (f"{req.method} {req.url} → {resp.status_code}\n"
            f"request headers: {json.dumps(redact(dict(req.headers)))}\n"
            f"request id: {resp.headers.get('x-request-id')}\nresponse: {body}")


def expect(resp: httpx.Response, status: int, code: str | None = None) -> dict:
    """Assert status (and error code) with a useful failure message; return the JSON body."""
    assert resp.status_code == status, f"expected {status}\n{describe(resp)}"
    data = resp.json() if resp.content else {}
    if code is not None:
        actual = data.get("error", {}).get("code")
        assert actual == code, f"expected error code {code!r}, got {actual!r}\n{describe(resp)}"
    return data


class ApiClient:
    def __init__(self, env: Environment, tokens: TokenFactory, http: httpx.Client, recorder: Recorder | None = None,
                 headers: dict | None = None, sleep=time.sleep):
        self.env, self.tokens, self.http = env, tokens, http
        self.recorder = recorder or Recorder(env.routes)
        self.headers = headers or {}
        self._sleep = sleep

    # ------------------------------------------------------------------ scoping
    def as_tenant(self, tenant: str, roles=ALL_ROLES, sub: str = "qa-bot", token: str | None = None,
                  **claims) -> ApiClient:
        token = token or self.tokens.mint(sub=sub, tenant=tenant, roles=roles, **claims)
        return self.with_headers({"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant})

    def with_headers(self, headers: dict) -> ApiClient:
        return ApiClient(self.env, self.tokens, self.http, self.recorder, {**self.headers, **headers}, self._sleep)

    def anonymous(self) -> ApiClient:
        return ApiClient(self.env, self.tokens, self.http, self.recorder, {}, self._sleep)

    # ------------------------------------------------------------------ requests
    def request(self, method: str, path: str, *, headers: dict | None = None, **kw) -> httpx.Response:
        merged = {"X-Request-ID": f"qa-{uuid.uuid4().hex[:12]}", **self.headers, **(headers or {})}
        merged = {k: v for k, v in merged.items() if v is not None}       # None removes a header
        attempts = 1 + (self.env.retries if method.upper() in IDEMPOTENT else 0)
        for attempt in range(1, attempts + 1):
            start = time.perf_counter()
            try:
                resp = self.http.request(method, path, headers=merged, **kw)
            except httpx.TransportError:
                self.recorder.add(method, path, None, (time.perf_counter() - start) * 1000, attempt)
                if attempt == attempts:
                    raise
                self._sleep(0.2 * attempt)
                continue
            self.recorder.add(method, path, resp.status_code, (time.perf_counter() - start) * 1000, attempt)
            if resp.status_code in RETRYABLE_STATUS and attempt < attempts:
                self._sleep(0.2 * attempt)
                continue
            return resp
        raise AssertionError("unreachable")

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> httpx.Response:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.request("DELETE", path, **kw)
