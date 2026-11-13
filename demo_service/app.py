"""Demo multi-tenant Orders API: the system under test.

A small but realistic ASGI service (Starlette) with the behaviours an API regression suite has to cover:

* JWT bearer auth (HS256; issuer, audience, expiry, not-before, required claims) and role checks
* tenant isolation: ``X-Tenant-ID`` must match the token, and other tenants' data reads as 404
* cursor pagination, strict request validation (pydantic, unknown fields rejected), body-size limit
* idempotent order creation (``Idempotency-Key``) with stock reservation and cancellation
* a shipping quote that calls an upstream carrier, with timeout, fallback rates and a circuit breaker
* test hooks (fault injection, reset) that exist only outside ``APP_ENV=prod``

One defect is seeded on purpose (``limit=0`` on the product list returns 500) so the suite can
demonstrate how a known bug is tracked: an ``xfail`` linked to a defect key in the Xray export.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import jwt
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .carrier import CarrierClient, CarrierError, CircuitOpen
from .data import seed

MAX_BODY_BYTES = 64 * 1024
DEV_SECRET = "dev-only-secret-change-me-0123456789"     # local/demo only; real deployments set API_JWT_SECRET
FALLBACK_RATES = {"IN": Decimal("80.00"), "US": Decimal("1450.00"), "DE": Decimal("1320.00")}
COUNTRIES = tuple(FALLBACK_RATES)


@dataclass
class ServiceSettings:
    app_env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "local"))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("API_JWT_SECRET", DEV_SECRET))
    issuer: str = field(default_factory=lambda: os.environ.get("API_JWT_ISSUER", "https://auth.example.test"))
    audience: str = field(default_factory=lambda: os.environ.get("API_JWT_AUDIENCE", "orders-api"))
    carrier_timeout_s: float = float(os.environ.get("CARRIER_TIMEOUT_S", "0.3"))

    @property
    def test_hooks(self) -> bool:
        return self.app_env != "prod"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None, **details):
        self.status, self.code, self.message, self.headers, self.details = status, code, message, headers or {}, details


# ------------------------------------------------------------------------------------------- models
class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=40)
    qty: StrictInt = Field(ge=1, le=100)


class Shipping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str
    postcode: str = Field(min_length=3, max_length=10)

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        if v not in COUNTRIES:
            raise ValueError(f"country must be one of {', '.join(COUNTRIES)}")
        return v


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[OrderItem] = Field(min_length=1, max_length=20)
    shipping: Shipping

    @field_validator("items")
    @classmethod
    def _unique_skus(cls, v: list[OrderItem]) -> list[OrderItem]:
        skus = [i.sku for i in v]
        if len(set(skus)) != len(skus):
            raise ValueError("each sku may appear only once per order")
        return v


# -------------------------------------------------------------------------------------------- state
class State:
    def __init__(self, settings: ServiceSettings):
        self.settings = settings
        self.carrier = CarrierClient(timeout_s=settings.carrier_timeout_s)
        self.reset()

    def reset(self) -> None:
        self.products = seed()                         # tenant -> sku -> product dict
        self.orders: dict[str, dict[str, dict]] = {t: {} for t in self.products}
        self.idempotency: dict[tuple[str, str], tuple[str, int, dict]] = {}
        self.carrier.reset()


# --------------------------------------------------------------------------------------------- auth
@dataclass
class Principal:
    sub: str
    tenant: str
    roles: set[str]


def _error(status: int, code: str, message: str, request: Request, headers: dict | None = None, **details):
    body = {"error": {"code": code, "message": message, "request_id": request.state.request_id, **details}}
    return JSONResponse(body, status_code=status, headers=headers)


def authenticate(request: Request, *roles: str) -> Principal:
    s: ServiceSettings = request.app.state.settings
    header = request.headers.get("authorization")
    if not header:
        raise ApiError(401, "missing_token", "Authorization header is required",
                       headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "invalid_token", "expected 'Authorization: Bearer <token>'",
                       headers={"WWW-Authenticate": 'Bearer error="invalid_request"'})
    try:
        claims = jwt.decode(token.strip(), s.jwt_secret, algorithms=["HS256"], audience=s.audience, issuer=s.issuer,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"]})
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "token has expired",
                       headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from None
    except jwt.PyJWTError as exc:
        raise ApiError(401, "invalid_token", f"token rejected: {type(exc).__name__}",
                       headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from None
    tenant_claim = claims.get("tenant_id")
    if not tenant_claim:
        raise ApiError(401, "invalid_token", "token has no tenant_id claim")
    tenant = request.headers.get("x-tenant-id")
    if not tenant:
        raise ApiError(400, "missing_tenant", "X-Tenant-ID header is required")
    if tenant != tenant_claim:
        raise ApiError(403, "tenant_mismatch", "token is not valid for this tenant")
    if tenant not in request.app.state.store.products:
        raise ApiError(403, "unknown_tenant", "tenant is not provisioned")
    granted = set(claims.get("roles", []))
    missing = [r for r in roles if r not in granted and "admin" not in granted]
    if missing:
        raise ApiError(403, "insufficient_scope", f"missing role(s): {', '.join(missing)}")
    return Principal(sub=claims["sub"], tenant=tenant, roles=granted)


# -------------------------------------------------------------------------------------------- helpers
def _product_out(p: dict) -> dict:
    return {"sku": p["sku"], "name": p["name"], "price": str(p["price"]), "currency": "INR",
            "stock": p["stock"], "weight_g": p["weight_g"]}


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    try:
        pad = "=" * (-len(cursor) % 4)
        offset = json.loads(base64.urlsafe_b64decode(cursor + pad))["o"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except Exception:
        raise ApiError(422, "invalid_cursor", "cursor is not valid") from None


async def _read_json(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if not ctype.startswith("application/json"):
        raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ApiError(413, "payload_too_large", f"request body exceeds {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body)
    except ValueError:
        raise ApiError(400, "malformed_json", "request body is not valid JSON") from None
    if not isinstance(data, dict):
        raise ApiError(422, "validation_error", "request body must be a JSON object")
    return data


def _validation_error(exc: ValidationError) -> ApiError:
    errors = [{"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return ApiError(422, "validation_error", "request validation failed", errors=errors)


async def _quote(request: Request, country: str, weight_g: int) -> tuple[Decimal, str, str | None]:
    """(amount, source, fallback_reason). Falls back to a flat rate when the carrier is unavailable."""
    carrier: CarrierClient = request.app.state.store.carrier
    try:
        return await carrier.quote(country, weight_g), "carrier", None
    except CircuitOpen:
        reason = "circuit_open"
    except asyncio.TimeoutError:
        reason = "carrier_timeout"
    except CarrierError as exc:
        reason = exc.reason
    extra = Decimal(max(0, math.ceil((weight_g - 1000) / 500))) * Decimal("20.00")
    return FALLBACK_RATES[country] + extra, "fallback", reason


# --------------------------------------------------------------------------------------------- routes
async def health(request: Request):
    return JSONResponse({"status": "ok", "env": request.app.state.settings.app_env})


async def list_products(request: Request):
    p = authenticate(request, "catalog:read")
    raw_limit = request.query_params.get("limit", "10")
    try:
        limit = int(raw_limit)
    except ValueError:
        raise ApiError(422, "validation_error", "limit must be an integer") from None
    if limit > 50 or limit < 0:
        raise ApiError(422, "validation_error", "limit must be between 1 and 50")
    offset = _decode_cursor(request.query_params["cursor"]) if "cursor" in request.query_params else 0
    items = sorted(request.app.state.store.products[p.tenant].values(), key=lambda x: x["sku"])
    pages = math.ceil(len(items) / limit)          # SEEDED DEFECT: limit=0 is not rejected above → 500
    page = items[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(items) else None
    return JSONResponse({"items": [_product_out(x) for x in page], "total": len(items), "pages": pages,
                         "next_cursor": _encode_cursor(nxt) if nxt is not None else None})


async def get_product(request: Request):
    p = authenticate(request, "catalog:read")
    product = request.app.state.store.products[p.tenant].get(request.path_params["sku"])
    if not product:
        raise ApiError(404, "not_found", "product not found")
    return JSONResponse(_product_out(product))


async def create_order(request: Request):
    p = authenticate(request, "orders:write")
    store: State = request.app.state.store
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required for POST /v1/orders")
    data = await _read_json(request)
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    seen = store.idempotency.get((p.tenant, key))
    if seen:
        if seen[0] != fingerprint:
            raise ApiError(422, "idempotency_key_reused", "Idempotency-Key was already used with a different body")
        return JSONResponse(seen[2], status_code=seen[1], headers={"Idempotent-Replayed": "true"})
    try:
        order_in = OrderIn.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    catalogue = store.products[p.tenant]
    unknown = [i.sku for i in order_in.items if i.sku not in catalogue]
    if unknown:
        raise ApiError(422, "unknown_sku", "one or more SKUs do not exist", skus=unknown)
    short = [i.sku for i in order_in.items if catalogue[i.sku]["stock"] < i.qty]
    if short:
        raise ApiError(409, "insufficient_stock", "not enough stock", skus=short)
    lines, subtotal, weight = [], Decimal("0"), 0
    for i in order_in.items:
        prod = catalogue[i.sku]
        line_total = prod["price"] * i.qty
        subtotal += line_total
        weight += prod["weight_g"] * i.qty
        lines.append({"sku": i.sku, "qty": i.qty, "unit_price": str(prod["price"]), "line_total": str(line_total)})
    shipping, source, reason = await _quote(request, order_in.shipping.country, weight)
    for i in order_in.items:
        catalogue[i.sku]["stock"] -= i.qty
    order = {
        "id": f"ord_{uuid.uuid4().hex[:16]}", "tenant_id": p.tenant, "status": "confirmed", "created_by": p.sub,
        "items": lines, "subtotal": str(subtotal), "shipping": str(shipping), "shipping_source": source,
        "total": str(subtotal + shipping), "currency": "INR", "shipping_address": order_in.shipping.model_dump(),
    }
    if reason:
        order["shipping_fallback_reason"] = reason
    store.orders[p.tenant][order["id"]] = order
    store.idempotency[(p.tenant, key)] = (fingerprint, 201, order)
    return JSONResponse(order, status_code=201, headers={"Location": f"/v1/orders/{order['id']}"})


async def get_order(request: Request):
    p = authenticate(request, "orders:read")
    order = request.app.state.store.orders[p.tenant].get(request.path_params["order_id"])
    if not order:
        raise ApiError(404, "not_found", "order not found")
    return JSONResponse(order)


async def cancel_order(request: Request):
    p = authenticate(request, "orders:write")
    store: State = request.app.state.store
    order = store.orders[p.tenant].get(request.path_params["order_id"])
    if not order:
        raise ApiError(404, "not_found", "order not found")
    if order["status"] != "confirmed":
        raise ApiError(409, "invalid_state", f"order is {order['status']}")
    for line in order["items"]:
        store.products[p.tenant][line["sku"]]["stock"] += line["qty"]
    order["status"] = "cancelled"
    return JSONResponse(order)


async def shipping_quote(request: Request):
    authenticate(request, "orders:read")
    q = request.query_params
    country = q.get("country", "")
    if country not in COUNTRIES:
        raise ApiError(422, "validation_error", f"country must be one of {', '.join(COUNTRIES)}")
    try:
        weight = int(q.get("weight_g", ""))
    except ValueError:
        raise ApiError(422, "validation_error", "weight_g must be an integer") from None
    if not 1 <= weight <= 30000:
        raise ApiError(422, "validation_error", "weight_g must be between 1 and 30000")
    amount, source, reason = await _quote(request, country, weight)
    headers = {"X-Fallback-Reason": reason} if reason else None
    return JSONResponse({"country": country, "weight_g": weight, "amount": str(amount), "currency": "INR",
                         "source": source}, headers=headers)


def _require_hooks(request: Request) -> None:
    if not request.app.state.settings.test_hooks:
        raise ApiError(404, "not_found", "not found")
    authenticate(request, "admin")


async def set_faults(request: Request):
    _require_hooks(request)
    data = await _read_json(request)
    mode = data.get("carrier", "ok")
    try:
        request.app.state.store.carrier.set_fault(mode)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    return JSONResponse(request.app.state.store.carrier.status())


async def get_faults(request: Request):
    _require_hooks(request)
    return JSONResponse(request.app.state.store.carrier.status())


async def clear_faults(request: Request):
    _require_hooks(request)
    request.app.state.store.carrier.reset()
    return JSONResponse(request.app.state.store.carrier.status())


async def reset(request: Request):
    _require_hooks(request)
    request.app.state.store.reset()
    return JSONResponse({"status": "reset"})


# ------------------------------------------------------------------------------------------------ app
class RequestIdMiddleware(BaseHTTPMiddleware):
    """Echo the caller's X-Request-ID (or mint one) so every response and error can be traced."""

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def create_app(settings: ServiceSettings | None = None) -> Starlette:
    settings = settings or ServiceSettings()
    routes = [
        Route("/health", health),
        Route("/v1/products", list_products),
        Route("/v1/products/{sku}", get_product),
        Route("/v1/orders", create_order, methods=["POST"]),
        Route("/v1/orders/{order_id}", get_order),
        Route("/v1/orders/{order_id}/cancel", cancel_order, methods=["POST"]),
        Route("/v1/shipping/quote", shipping_quote),
        Route("/_test/faults", get_faults, methods=["GET"]),
        Route("/_test/faults", set_faults, methods=["PUT"]),
        Route("/_test/faults", clear_faults, methods=["DELETE"]),
        Route("/_test/reset", reset, methods=["POST"]),
    ]

    async def api_error(request: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, request, exc.headers, **exc.details)

    async def http_error(request: Request, exc: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail), request)

    async def server_error(request: Request, exc: Exception):
        return _error(500, "internal_error", "unexpected server error", request)

    app = Starlette(routes=routes, middleware=[Middleware(RequestIdMiddleware)],
                    exception_handlers={ApiError: api_error, HTTPException: http_error, Exception: server_error})
    app.state.settings = settings
    app.state.store = State(settings)
    return app


app = create_app()
