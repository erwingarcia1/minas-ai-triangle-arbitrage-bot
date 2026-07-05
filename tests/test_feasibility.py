"""Exact rounding/precision feasibility — the "unroundable triangle" gate."""

from __future__ import annotations

from decimal import Decimal

import pytest

from triarb.pricing.feasibility import simulate_rounded_cycle

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
        meta = _meta(
            BTCUSDT=mk_symbol("BTCUSDT", "BTC", "USDT", min_notional="200")
        )
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "USDT"
        )
        assert not report.ok
        assert report.reject_reason == "min_notional"

    def test_min_notional_skipped_for_non_usdt_quote(self) -> None:
        meta = _meta(BTCUSDT=mk_symbol("BTCUSDT", "BTC", "USDT", min_notional="200"))
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, NOTIONAL, FEE, "EUR"
        )
        assert report.ok  # proxy check only applies to USDT-quoted cycles

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
