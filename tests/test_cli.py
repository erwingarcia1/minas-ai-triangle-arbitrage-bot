"""CLI startup gates: the wiring, not just the functions (QA review).

These tests pin the single enforcement points in ``cli._observe`` — the
mode gate and the channel-cadence check — by invoking the real entrypoint
with real config files. Both gates fire before any network connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from triarb import cli
from triarb.config.models import UnsupportedModeError


def _write_config(tmp_path: Path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestModeGateWiring:
    @pytest.mark.parametrize("mode", ["paper_trading", "shadow_trading", "live_trading"])
    def test_observe_refuses_non_market_data_modes(self, tmp_path: Path, mode: str) -> None:
        config = _write_config(tmp_path, f"venue: bitget\nmode: {mode}\n")
        with pytest.raises(UnsupportedModeError):
            cli.main(["observe", "--config", config])


class TestChannelCadenceGate:
    def test_slow_channel_with_tight_staleness_refused_at_startup(
        self, tmp_path: Path
    ) -> None:
        # depth20@1000ms pushes at best every 1000ms; a 300ms staleness bound
        # would mark every evaluation stale and the observer would silently
        # price nothing, forever (adversarial review finding).
        config = _write_config(
            tmp_path,
            "venue: binance\n"
            "observer:\n"
            "  depth_channel: depth20@1000ms\n"
            "  staleness_ms: 300\n",
        )
        with pytest.raises(ValueError, match="pushes at best"):
            cli.main(["observe", "--config", config])

    def test_slow_channel_with_loose_staleness_passes_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same channel with staleness above the cadence must get PAST the
        # cadence gate. Stub the observer bootstrap so the test stays
        # offline: reaching prepare() proves the gate admitted the config.
        config = _write_config(
            tmp_path,
            "venue: binance\n"
            "observer:\n"
            "  depth_channel: depth20@1000ms\n"
            "  staleness_ms: 2500\n",
        )

        class _StopAtPrepare(Exception):
            pass

        async def fake_prepare(self: object) -> None:
            raise _StopAtPrepare

        monkeypatch.setattr(cli.Observer, "prepare", fake_prepare)
        with pytest.raises(_StopAtPrepare):
            cli.main(["observe", "--config", config])
