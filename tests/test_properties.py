"""Property-based tests (§32): invariants that must hold for ALL inputs.

Each property maps to a §32 requirement:
- output never exceeds mathematically possible depth (P1),
- increasing fees cannot increase expected profit (P2),
- all quantities/prices satisfy exchange increments (P3, Invariant 6),
- rounding cannot create artificial funds (P4).

``derandomize=True`` keeps CI deterministic: the same examples run every
time (Hypothesis still explores the space; it just does so reproducibly).
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from triarb.pricing.depth import buy_base_with_quote, sell_base_for_quote
from triarb.pricing.edge import evaluate_cycle
from triarb.pricing.feasibility import simulate_rounded_cycle

from tests.conftest import NOW_NS, mk_store, mk_symbol, mk_triangle, profitable_books

settings.register_profile("triarb", derandomize=True, max_examples=200)
settings.load_profile("triarb")

_EPS = 1e-9

# Order-book levels: positive prices/sizes across realistic magnitudes.
_price = st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False)
_size = st.floats(min_value=1e-9, max_value=1e9, allow_nan=False, allow_infinity=False)
_levels = st.lists(st.tuples(_price, _size), min_size=1, max_size=25)

# Venue increments, including the non-power-of-ten shapes §9 warns about.
_steps = st.sampled_from(["0.00000001", "0.000001", "0.001", "0.01", "0.05", "0.25", "0.5", "1"])


class TestP1DepthBounds:
    """P1: a walk can never return more than the visible book contains."""

    @given(asks=_levels, quote=st.floats(min_value=1e-6, max_value=1e12))
    def test_buy_output_never_exceeds_visible_size(
        self, asks: list[tuple[float, float]], quote: float
    ) -> None:
        asks = sorted(asks)
        fill = buy_base_with_quote(asks, quote)
        total_size = sum(s for _, s in asks)
        if fill is None:
            # None is only legal when the book truly cannot absorb the amount.
            total_cost = sum(p * s for p, s in asks)
            assert quote > total_cost * (1.0 - _EPS)
        else:
            base_out, worst = fill
            assert base_out <= total_size * (1.0 + _EPS)
            assert worst >= asks[0][0]  # worst fill price is never better than the top

    @given(bids=_levels, base=st.floats(min_value=1e-9, max_value=1e12))
    def test_sell_proceeds_never_exceed_visible_value(
        self, bids: list[tuple[float, float]], base: float
    ) -> None:
        bids = sorted(bids, reverse=True)
        fill = sell_base_for_quote(bids, base)
        if fill is None:
            assert base > sum(s for _, s in bids) * (1.0 - _EPS)
        else:
            proceeds, worst = fill
            assert proceeds <= sum(p * s for p, s in bids) * (1.0 + _EPS)
            assert worst <= bids[0][0]


class TestP2FeeMonotonicity:
    """P2: a higher per-leg fee can never produce a better net edge."""

    @given(
        f1=st.floats(min_value=0.0, max_value=0.01),
        f2=st.floats(min_value=0.0, max_value=0.01),
        notional=st.floats(min_value=1.0, max_value=1000.0),
    )
    def test_net_edge_non_increasing_in_fee(
        self, f1: float, f2: float, notional: float
    ) -> None:
        lo, hi = sorted((f1, f2))
        store = mk_store(profitable_books())
        tri = mk_triangle()
        r_lo = evaluate_cycle(tri, store, notional, lo, NOW_NS, 1e9)
        r_hi = evaluate_cycle(tri, store, notional, hi, NOW_NS, 1e9)
        assert r_lo.ok and r_hi.ok
        assert r_lo.net_bps >= r_hi.net_bps - _EPS
        # Gross (fee-free) edge must not depend on the fee at all.
        assert abs(r_lo.gross_bps - r_hi.gross_bps) <= _EPS


class TestP3IncrementCompliance:
    """P3 (Invariant 6): every planned order respects venue increments."""

    @given(
        tick=_steps,
        step=_steps,
        notional=st.decimals(min_value="1", max_value="10000", places=2),
        fee=st.decimals(min_value="0", max_value="0.005", places=6),
    )
    def test_all_leg_plans_are_exact_multiples(
        self, tick: str, step: str, notional: Decimal, fee: Decimal
    ) -> None:
        meta = {
            sym: mk_symbol(sym, b, q, price_tick_size=tick, quantity_step_size=step)
            for sym, b, q in (
                ("BTCUSDT", "BTC", "USDT"),
                ("ETHBTC", "ETH", "BTC"),
                ("ETHUSDT", "ETH", "USDT"),
            )
        }
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(profitable_books()), meta, notional, fee, "USDT"
        )
        if not report.ok:
            return  # rejection is always a legal outcome
        for leg in report.legs:
            assert leg.quantity > 0
            assert leg.quantity % Decimal(step) == 0, (leg, step)
            assert leg.limit_price % Decimal(tick) == 0, (leg, tick)


class TestP4RoundingCreatesNoFunds:
    """P4: with zero fees on a multiplier-1 cycle, rounding can only lose or
    preserve value — final settlement can never exceed the starting amount."""

    @given(
        tick=_steps,
        step=_steps,
        notional=st.decimals(min_value="0.01", max_value="100000", places=2),
    )
    def test_identity_cycle_never_profits_from_rounding(
        self, tick: str, step: str, notional: Decimal
    ) -> None:
        # Every market at price 1.0 with effectively infinite depth: the true
        # cycle multiplier is exactly 1, so any reported final_quote above the
        # starting notional would be money invented by rounding.
        books = {
            sym: ([(1.0, 1e15)], [(1.0, 1e15)])
            for sym in ("BTCUSDT", "ETHBTC", "ETHUSDT")
        }
        meta = {
            sym: mk_symbol(sym, b, q, price_tick_size=tick, quantity_step_size=step)
            for sym, b, q in (
                ("BTCUSDT", "BTC", "USDT"),
                ("ETHBTC", "ETH", "BTC"),
                ("ETHUSDT", "ETH", "USDT"),
            )
        }
        report = simulate_rounded_cycle(
            mk_triangle(), mk_store(books), meta, notional, Decimal(0), "USDT"
        )
        if not report.ok:
            return
        assert report.final_quote <= notional
        assert all(v >= 0 for v in report.stranded.values())
        assert report.net_bps_after_rounding <= _EPS
