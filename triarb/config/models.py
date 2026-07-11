"""Pydantic configuration models.

Failure modes:
- An unknown venue, malformed whitelist entry, or non-positive size fails
  validation at startup with a precise error; the process never starts with a
  half-valid config.
- All four §3 operating modes parse, but ``ensure_supported_mode`` refuses
  everything except ``market_data_only`` in this build: there is no
  execution path to gate yet, and ``live_trading`` can never be enabled by
  configuration alone (see CLAUDE.md §3 for the authorization chain).
"""

from __future__ import annotations

import enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_WHITELIST_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+$")


class Venue(str, enum.Enum):
    """Supported venues. One venue is active at a time."""

    BITGET = "bitget"
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"


class Mode(str, enum.Enum):
    """Operating modes, in order of increasing consequence.

    Only ``MARKET_DATA_ONLY`` is implemented in this build — there is no
    execution path of any kind. The other members exist so configs written
    for later phases parse cleanly and fail at the mode gate with a precise
    message instead of an unknown-key error.
    """

    MARKET_DATA_ONLY = "market_data_only"
    PAPER_TRADING = "paper_trading"
    SHADOW_TRADING = "shadow_trading"
    LIVE_TRADING = "live_trading"


class UnsupportedModeError(RuntimeError):
    """The configured mode is not available in this build."""


def ensure_supported_mode(settings: Settings) -> None:
    """Startup gate: refuse any mode this build cannot honor.

    ``LIVE_TRADING`` gets a distinct message because enabling it is never a
    code-only change: it requires the full authorization chain (paper and
    shadow qualification, security and risk review, recorded human approval)
    on top of an execution engine that does not exist here.
    """
    if settings.mode is Mode.MARKET_DATA_ONLY:
        return
    if settings.mode is Mode.LIVE_TRADING:
        raise UnsupportedModeError(
            "mode 'live_trading' is disabled: this build contains no order code, and "
            "live trading additionally requires paper/shadow qualification, security "
            "and risk review, and recorded human authorization (see CLAUDE.md §3)"
        )
    raise UnsupportedModeError(
        f"mode '{settings.mode.value}' requires the execution engine planned for a "
        "later phase; this build supports only 'market_data_only'"
    )


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
    # Venue-native depth-channel name, or "auto" for the adapter's default
    # snapshot channel (bitget: books15, binance: depth20@100ms, okx: books5,
    # bybit: orderbook.50). Adapters validate the value and reject channels
    # they do not support in this phase.
    depth_channel: str = Field(default="auto", min_length=1)
    stats_interval_s: float = Field(default=60.0, gt=0)
    metadata_refresh_s: float = Field(default=3600.0, ge=0)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: Venue = Venue.BITGET
    mode: Mode = Mode.MARKET_DATA_ONLY
    quote_currency: str = Field(default="USDT", min_length=2, max_length=12)
    triangles: TrianglesConfig = Field(default_factory=TrianglesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    observer: ObserverConfig = Field(default_factory=ObserverConfig)

    @field_validator("quote_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()
