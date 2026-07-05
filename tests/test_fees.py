"""Fee compounding and the break-even hurdle."""

from __future__ import annotations

import pytest

from triarb.config.models import FeesConfig
from triarb.pricing.fees import hurdle_bps, net_multiplier


class TestHurdle:
    def test_zero_fee(self) -> None:
        assert hurdle_bps(0.0) == 0.0

    def test_ten_bps_taker(self) -> None:
        # 3 legs at 0.1% is ~30.06 bps, not the naive 30.
        assert hurdle_bps(0.001) == pytest.approx(30.0601, abs=1e-3)
        assert hurdle_bps(0.001) > 30.0

    def test_fee_tier_table(self) -> None:
        # The README fee-tier impact table.
        assert hurdle_bps(0.0008) == pytest.approx(24.038, abs=1e-2)
        assert hurdle_bps(0.00075) == pytest.approx(22.533, abs=1e-2)
        assert hurdle_bps(0.0006) == pytest.approx(18.022, abs=1e-2)

    def test_invalid_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            hurdle_bps(1.0)
        with pytest.raises(ValueError):
            hurdle_bps(-0.001)


class TestNetMultiplier:
    def test_compounds_per_leg(self) -> None:
        assert net_multiplier(1.04, 0.001, 3) == pytest.approx(1.04 * 0.999**3)

    def test_break_even_at_hurdle(self) -> None:
        gross = 1.0 + hurdle_bps(0.001) / 10_000.0
        assert net_multiplier(gross, 0.001) == pytest.approx(1.0, abs=1e-12)


class TestFeeConfig:
    def test_effective_rate_no_discount(self) -> None:
        cfg = FeesConfig(taker_bps=10.0, token_discount_enabled=False)
        assert cfg.effective_taker_rate == pytest.approx(0.001)

    def test_effective_rate_with_token_discount(self) -> None:
        cfg = FeesConfig(
            taker_bps=10.0, token_discount_enabled=True, token_discount_multiplier=0.8
        )
        assert cfg.effective_taker_rate == pytest.approx(0.0008)
