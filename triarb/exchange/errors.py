"""Exchange-layer exceptions.

Every adapter failure surfaces as one of these types with venue context —
never a bare ``Exception`` and never swallowed silently.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for all exchange-adapter failures."""


class RestError(AdapterError):
    """A REST call failed (HTTP error, venue error code, or malformed payload)."""

    def __init__(self, venue: str, endpoint: str, detail: str) -> None:
        self.venue = venue
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(f"[{venue}] REST {endpoint}: {detail}")


class WsProtocolError(AdapterError):
    """The venue sent a WebSocket message we cannot interpret safely."""


class VenueNotImplementedError(AdapterError):
    """The configured venue's adapter has not shipped yet (see phase plan)."""
