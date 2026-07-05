"""Cycle edge math: gross/net bps, staleness, depth exhaustion."""

from __future__ import annotations

import pytest

from triarb.pricing.edge import evaluate_cycle

from tests.conftest import NOW_NS, mk_store, mk_triangle, profitable_books

FEE = 0.001
STALENESS_MS = 300.0


class TestEvaluateCycle:
    def test_gross_and_net_edge(self) -> None:
        # 100 USDT -> 0.002 BTC -> 0.04 ETH -> 104 USDT: gross +400 bps,
        # net = 104 * 0.999^3 per 100.
        store = mk_store(profitable_books())
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, NOW_NS, STALENESS_MS)
        assert res.ok
        assert res.gross_bps == pytest.approx(400.0, rel=1e-12)
        expected_net = (1.04 * (1.0 - FEE) ** 3 - 1.0) * 10_000.0
        assert res.net_bps == pytest.approx(expected_net, rel=1e-12)
        assert res.worst_prices == (50000.0, 0.05, 2600.0)
        assert res.max_age_ms == pytest.approx(0.0)

    def test_zero_fee_net_equals_gross(self) -> None:
        store = mk_store(profitable_books())
        res = evaluate_cycle(mk_triangle(), store, 100.0, 0.0, NOW_NS, STALENESS_MS)
        assert res.net_bps == pytest.approx(res.gross_bps, rel=1e-12)

    def test_losing_cycle_negative_edge(self) -> None:
        books = profitable_books()
        books["ETHUSDT"] = ([(2450.0, 500.0)], [(2451.0, 500.0)])  # 98 USDT out
        store = mk_store(books)
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, NOW_NS, STALENESS_MS)
        assert res.ok
        assert res.gross_bps == pytest.approx(-200.0, rel=1e-12)
        assert res.net_bps < res.gross_bps

    def test_missing_book(self) -> None:
        books = profitable_books()
        del books["ETHBTC"]
        store = mk_store(books)
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, NOW_NS, STALENESS_MS)
        assert not res.ok
        assert res.reason == "missing_book"

    def test_stale_book_rejected(self) -> None:
        store = mk_store(profitable_books(), recv_ns=NOW_NS)
        later = NOW_NS + int(301e6)  # 301ms after receipt
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, later, STALENESS_MS)
        assert not res.ok
        assert res.reason == "stale_book"

    def test_fresh_book_within_bound(self) -> None:
        store = mk_store(profitable_books(), recv_ns=NOW_NS)
        later = NOW_NS + int(299e6)
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, later, STALENESS_MS)
        assert res.ok
        assert res.max_age_ms == pytest.approx(299.0)

    def test_insufficient_depth(self) -> None:
        books = profitable_books()
        books["BTCUSDT"] = ([(49990.0, 5.0)], [(50000.0, 0.000001)])
        store = mk_store(books)
        res = evaluate_cycle(mk_triangle(), store, 100.0, FEE, NOW_NS, STALENESS_MS)
        assert not res.ok
        assert res.reason == "insufficient_depth"

    def test_depth_walk_worse_than_top_of_book(self) -> None:
        # Only 0.001 BTC at 50000; the rest fills at 51000. A top-of-book
        # model would report +400 bps gross; the walk must report less.
        books = profitable_books()
        books["BTCUSDT"] = ([(49990.0, 5.0)], [(50000.0, 0.001), (51000.0, 5.0)])
        store = mk_store(books)
        res = evaluate_cycle(mk_triangle(), store, 100.0, 0.0, NOW_NS, STALENESS_MS)
        assert res.ok
        assert res.gross_bps < 400.0
        assert res.worst_prices[0] == 51000.0
