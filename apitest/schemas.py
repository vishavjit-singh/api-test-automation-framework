"""Response contracts. Unknown fields are allowed (additive changes are not breaking); missing or
mistyped fields fail validation, which is what a consumer would trip over."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Product(BaseModel):
    sku: str
    name: str
    price: str
    currency: Literal["INR"]
    stock: int
    weight_g: int


class ProductPage(BaseModel):
    items: list[Product]
    total: int
    next_cursor: str | None


class OrderLine(BaseModel):
    sku: str
    qty: int
    unit_price: str
    line_total: str


class Order(BaseModel):
    id: str
    tenant_id: str
    status: Literal["confirmed", "cancelled"]
    items: list[OrderLine]
    subtotal: str
    shipping: str
    shipping_source: Literal["carrier", "fallback"]
    total: str
    currency: Literal["INR"]


class Error(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: Error
