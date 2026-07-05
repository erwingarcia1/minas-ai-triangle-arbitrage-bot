"""Pydantic configuration models.

Failure modes:
- An unknown venue, malformed whitelist entry, or non-positive size fails
  validation at startup with a precise error; the process never starts with a
  half-valid config.
- There is deliberately no ``mode: live`` knob in Phase 1 — the observer has
  no execution path to gate. The paper/live gate ships with the execution
  engine in Phase 2.
"""

from __future__ import annotations

import enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_WHITELIST_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+$")


class Venue(str, enum.Enum):
    """Supported venues. One venue is active at a time."""

    BITGET = "bitget"
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"


class TrianglesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_discovery: bool = True
    whitelist: list[str] = Field(default_factory=list)
    max_triangles: int = Field(default=150, ge=1, le=2000)

    @field_validator("whitelist")
    @classmethod
    def _validate_whitelist(cls, entries: list[str]) -> list[str]:
        for entry in entries:
            if not _WHITELIST_RE.match(entry):
                raise ValueError(
                    f"whitelist entry {entry!r} must look like 'USDT-BTC-ETH' "
                    "(three uppercase currency codes joined by '-')"
                )
        return entries


class FiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_24h_quote_volume_usdt: float = Field(default=2_000_000.0, ge=0)
    max_spread_bps: float = Field(default=15.0, gt=0)


class FeesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taker_bps: float = Field(default=10.0, ge=0, le=100)
    maker_bps: float = Field(default=10.0, ge=0, le=100)
    token_discount_enabled: bool = False
    token_discount_multiplier: float = Field(default=0.8, gt=0, le=1)

    @property
    def effective_taker_rate(self) -> float:
        """Taker fee as a fraction (not bps), with the fee-token discount applied."""
        rate = self.taker_bps / 10_000.0
        if self.token_discount_enabled:
            rate *= self.token_discount_multiplier
        return rate


class ObserverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notional: float = Field(default=100.0, gt=0)
    edge_threshold_bps: float = Field(default=0.0)
    staleness_ms: int = Field(default=300, gt=0)
    depth_channel: Literal["books1", "books5", "books15"] = "books15"
    stats_interval_s: float = Field(default=60.0, gt=0)
    metadata_refresh_s: float = Field(default=3600.0, ge=0)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: Venue = Venue.BITGET
    quote_currency: str = Field(default="USDT", min_length=2, max_length=12)
    triangles: TrianglesConfig = Field(default_factory=TrianglesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    observer: ObserverConfig = Field(default_factory=ObserverConfig)

    @field_validator("quote_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()
