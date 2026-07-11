"""Exact rounding/precision feasibility — the "unroundable triangle" gate."""

from __future__ import annotations

from decimal import Decimal

import pytest

from triarb.pricing.feasibility import _down, _up, simulate_rounded_cycle

from tests.conftest import mk_store, mk_symbol, mk_triangle, profitable_books

FEE = Decimal("0.001")
NOTIONAL = Decimal("100")


def _meta(**overrides):
    defaults = {
        "BTCUSDT": mk_symbol("BTCUSDT", "BTC", "USDT", price_precision=2, quantity_precision=6),
        "ETHBTC": mk_symbol("ETHBTC", "ETH", "BTC", price_precision=6, quantity_precision=4),
        "ETHUSDT": mk_symbol("ETHUSDT", "ETH", "USDT", price_precision=2, quantity_precision=4),
    }
    defaults.update(overrides)
    return defaults


class TestHappyPath:
    def test_exact_rounded_pnl(self) -> None:
        # Hand-computed: leg1 buys exactly 0.002 BTC (fee -> 0.001998);
        # leg2 qty rounds 0.03996 -> 0.0399 ETH (0.000003 BTC stranded, fee
        # -> 0.0398601); leg3 qty rounds -> 0.0398 ETH (0.0000601 stranded),
        # proceeds 103.48 * 0.999 = 103.37652 USDT.
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), _meta(), NOTIONAL, FEE, "USDT"
        )
        assert report.ok
        assert report.reject_reason is None
        assert report.final_quote == Decimal("103.37652000")
        assert report.net_bps_after_rounding == pytest.approx(337.652, abs=1e-6)
        assert report.stranded == {
            "BTC": Decimal("0.000003"),
            "ETH": Decimal("0.0000601"),
        }
        assert [leg.side for leg in report.legs] == ["buy", "buy", "sell"]
        assert report.legs[0].quantity == Decimal("0.002")

    def test_rounding_always_costs_edge(self) -> None:
        # Net after rounding can never beat the unrounded float screen.
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), _meta(), NOTIONAL, FEE, "USDT"
        )
        unrounded_net = (1.04 * (1.0 - 0.001) ** 3 - 1.0) * 10_000.0
        assert report.net_bps_after_rounding < unrounded_net


class TestRoundingKills:
    def test_coarse_step_turns_profit_into_loss(self) -> None:
        # ETHBTC step of 0.01 forces 0.03996 -> 0.03 ETH: a +368 bps float
        # edge becomes deeply negative after rounding — reject before firing.
        meta = _meta(
            ETHBTC=mk_symbol("ETHBTC", "ETH", "BTC", price_precision=6, quantity_precision=2)
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert report.ok  # simulable, but…
        assert report.net_bps_after_rounding < 0.0
        assert report.stranded["BTC"] > 0

    def test_qty_rounds_to_zero(self) -> None:
        meta = _meta(
            ETHBTC=mk_symbol("ETHBTC", "ETH", "BTC", price_precision=6, quantity_precision=0)
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "qty_rounds_to_zero"


class TestExchangeMinimums:
    def test_min_notional_rejection(self) -> None:
        # Quote-denominated minimum (Binance/Bybit style, the default):
        # leg 1 spends ~100 USDT on BTCUSDT, below the 200 USDT minimum.
        meta = _meta(
            BTCUSDT=mk_symbol("BTCUSDT", "BTC", "USDT", min_notional="200")
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "min_notional"

    def test_cross_leg_quote_denominated_minimum_enforced(self) -> None:
        # Regression (quant review): a quote-denominated minimum on the
        # CROSS leg is denominated in BTC, not USDT. Leg 2 spends only
        # ~0.002 BTC, far below a 0.01 BTC minimum — the old USDT-proxy
        # comparison (95 < 0.01 == False) silently passed this.
        meta = _meta(
            ETHBTC=mk_symbol("ETHBTC", "ETH", "BTC", min_notional="0.01")
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "min_notional"

    def test_settlement_denominated_minimum_uses_haircut_proxy(self) -> None:
        # Bitget-style minTradeUSDT on a non-USDT-quoted leg of a USDT
        # cycle: valued via the 5% haircut proxy (100 * 0.95 = 95 USDT).
        ok_meta = _meta(
            ETHBTC=mk_symbol(
                "ETHBTC", "ETH", "BTC", min_notional="90", min_notional_asset="USDT"
            )
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), ok_meta, NOTIONAL, FEE, "USDT"
        )
        assert report.ok  # 95 >= 90
        tight_meta = _meta(
            ETHBTC=mk_symbol(
                "ETHBTC", "ETH", "BTC", min_notional="96", min_notional_asset="USDT"
            )
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), tight_meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "min_notional"

    def test_unvaluable_minimum_denomination_is_skipped(self) -> None:
        # A USDT-denominated minimum on a BTC-quoted leg of an EUR cycle
        # cannot be valued without conversion rates — the observer skips it
        # (documented model limit); executor phases must value it.
        meta = _meta(
            ETHBTC=mk_symbol(
                "ETHBTC", "ETH", "BTC", min_notional="1000000", min_notional_asset="USDT"
            )
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "EUR"
        )
        assert report.ok

    def test_min_qty_rejection(self) -> None:
        meta = _meta(
            BTCUSDT=mk_symbol("BTCUSDT", "BTC", "USDT", min_base_amount="0.01")
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "min_qty"

    def test_insufficient_depth_rejection(self) -> None:
        books = profitable_books()
        books["BTCUSDT"] = ([(49990.0, 5.0)], [(50000.0, 0.000001)])
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(books), _meta(), NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "insufficient_depth"

    def test_missing_metadata_rejection(self) -> None:
        meta = _meta()
        del meta["ETHBTC"]
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "missing_metadata"


class TestStepRounding:
    """Venue increments are not always powers of ten (§9): rounding must be
    exact multiples of the published tick/step, never decimal truncation."""

    def test_down_to_half_step(self) -> None:
        assert _down(Decimal("10.3"), Decimal("0.5")) == Decimal("10.0")
        assert _down(Decimal("10.5"), Decimal("0.5")) == Decimal("10.5")

    def test_up_to_half_step(self) -> None:
        assert _up(Decimal("10.3"), Decimal("0.5")) == Decimal("10.5")
        assert _up(Decimal("10.5"), Decimal("0.5")) == Decimal("10.5")

    def test_exact_multiple_is_unchanged_no_float_artifacts(self) -> None:
        assert _down(Decimal("0.3"), Decimal("0.1")) == Decimal("0.3")
        assert _up(Decimal("0.3"), Decimal("0.1")) == Decimal("0.3")

    def test_floor_is_exact_beyond_default_context_precision(self) -> None:
        # Regression (quant review): naive Decimal division rounds the
        # quotient to context precision (28 digits) BEFORE flooring, which
        # rounded 9.999…9/0.25 UP across the integer boundary and returned
        # a "floor" greater than the input.
        value = Decimal("9.999999999999999999999999999")  # 28 significant digits
        assert _down(value, Decimal("0.25")) == Decimal("9.75")
        assert _up(value, Decimal("0.25")) == Decimal("10.00")

    def test_negative_inputs_floor_and_ceil_correctly(self) -> None:
        # Latent-domain contract: _down is a true floor and _up a true
        # ceiling for all signs (ROUND_DOWN/ROUND_UP truncate toward zero
        # and silently swapped roles for negatives).
        assert _down(Decimal("-10.3"), Decimal("0.5")) == Decimal("-10.5")
        assert _up(Decimal("-10.3"), Decimal("0.5")) == Decimal("-10.0")

    def test_full_cycle_with_quarter_qty_step_and_half_price_tick(self) -> None:
        # Zero fee; every symbol has qty step 0.25 and price tick 0.5.
        # Leg 1: 100 / 3.0 = 33.33… -> 33.25 units (0.25 USDT unspent);
        # Leg 2: 1:1 -> 33.25 exactly; Leg 3: 33.25 * 3.1 = 103.075, plus the
        # recovered 0.25 USDT = 103.325.
        books = {
            "BTCUSDT": ([(2.9, 1000.0)], [(3.0, 1000.0)]),
            "ETHBTC": ([(0.9, 1000.0)], [(1.0, 1000.0)]),
            "ETHUSDT": ([(3.1, 1000.0)], [(3.2, 1000.0)]),
        }
        meta = {
            sym: mk_symbol(
                sym, b, q, price_tick_size="0.5", quantity_step_size="0.25"
            )
            for sym, b, q in (
                ("BTCUSDT", "BTC", "USDT"),
                ("ETHBTC", "ETH", "BTC"),
                ("ETHUSDT", "ETH", "USDT"),
            )
        }
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(books), meta, NOTIONAL, Decimal(0), "USDT"
        )
        assert report.ok
        assert report.legs[0].quantity == Decimal("33.25")
        # Sell limit rounds DOWN to the 0.5 tick to stay marketable.
        assert report.legs[2].limit_price == Decimal("3.0")
        assert report.final_quote == Decimal("103.325")
        assert "USDT" not in report.stranded


class TestQuoteDustRecovery:
    def test_unspent_quote_counts_toward_pnl(self) -> None:
        # Whole-unit steps everywhere, zero fee. Leg 1 spends 99 of 100 USDT
        # (33 units at 3); the unspent 1 USDT never left the account and must
        # be counted, while intermediate dust would be valued at zero.
        books = {
            "BTCUSDT": ([(2.9, 1000.0)], [(3.0, 1000.0)]),
            "ETHBTC": ([(0.9, 1000.0)], [(1.0, 1000.0)]),
            "ETHUSDT": ([(3.1, 1000.0)], [(3.2, 1000.0)]),
        }
        meta = {
            "BTCUSDT": mk_symbol("BTCUSDT", "BTC", "USDT", quantity_precision=0),
            "ETHBTC": mk_symbol("ETHBTC", "ETH", "BTC", quantity_precision=0),
            "ETHUSDT": mk_symbol("ETHUSDT", "ETH", "USDT", quantity_precision=0),
        }
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(books), meta, NOTIONAL, Decimal(0), "USDT"
        )
        assert report.ok
        # 33 ETH * 3.1 = 102.3 plus the 1 USDT never spent.
        assert report.final_quote == Decimal("103.3")
        assert report.net_bps_after_rounding == pytest.approx(330.0)
        assert "USDT" not in report.stranded
