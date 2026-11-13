"""Simulated upstream carrier with fault injection and a circuit breaker.

The carrier is in-process so the demo runs anywhere, but the service talks to it through the same
contract it would use for a real HTTP dependency: a timeout, error responses, malformed payloads.
"""
from __future__ import annotations

import asyncio
import math
import time
from decimal import Decimal

FAULT_MODES = ("ok", "timeout", "error", "malformed")
RATE_PER_500G = {"IN": Decimal("35.00"), "US": Decimal("620.00"), "DE": Decimal("560.00")}


class CarrierError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CircuitOpen(Exception):
    pass


class CarrierClient:
    def __init__(self, timeout_s: float = 0.3, failure_threshold: int = 3, cooldown_s: float = 30.0):
        self.timeout_s = timeout_s
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.reset()

    def reset(self) -> None:
        self.fault = "ok"
        self.calls = 0
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    def set_fault(self, mode: str) -> None:
        if mode not in FAULT_MODES:
            raise ValueError(f"carrier fault must be one of {', '.join(FAULT_MODES)}")
        self.fault = mode

    def status(self) -> dict:
        return {"carrier": self.fault, "calls": self.calls, "consecutive_failures": self.consecutive_failures,
                "circuit": "open" if self._is_open() else "closed"}

    def _is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            self.opened_at = None          # half-open: let the next call through
            return False
        return True

    async def _call_upstream(self, country: str, weight_g: int) -> dict:
        self.calls += 1
        if self.fault == "timeout":
            await asyncio.sleep(self.timeout_s * 3)
        if self.fault == "error":
            raise CarrierError("carrier_error")
        if self.fault == "malformed":
            return {"unexpected": "shape"}
        units = max(1, math.ceil(weight_g / 500))
        return {"amount": str(RATE_PER_500G[country] * units), "currency": "INR"}

    async def quote(self, country: str, weight_g: int) -> Decimal:
        if self._is_open():
            raise CircuitOpen()
        try:
            payload = await asyncio.wait_for(self._call_upstream(country, weight_g), timeout=self.timeout_s)
            if "amount" not in payload:
                raise CarrierError("carrier_bad_response")
            amount = Decimal(payload["amount"])
        except (asyncio.TimeoutError, CarrierError):
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.failure_threshold:
                self.opened_at = time.monotonic()
            raise
        self.consecutive_failures = 0
        return amount
