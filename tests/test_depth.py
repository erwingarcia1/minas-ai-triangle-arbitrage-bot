"""Depth-walk math: exact fills, boundary conditions, shallow books."""

from __future__ import annotations

import pytest

from triarb.pricing.depth import buy_base_with_quote, sell_base_for_quote


class TestBuy:
    def test_partial_first_level(self) -> None:
        assert buy_base_with_quote([(100.0, 1.0)], 50.0) == (0.5, 100.0)

    def test_exact_level_boundary(self) -> None:
        assert buy_base_with_quote([(100.0, 1.0)], 100.0) == (1.0, 100.0)

    def test_spans_levels(self) -> None:
        result = buy_base_with_quote([(100.0, 1.0), (101.0, 2.0)], 150.0)
        assert result is not None
        base, worst = result
        assert base == pytest.approx(1.0 + 50.0 / 101.0)
        assert worst == 101.0

    def test_insufficient_depth(self) -> None:
        assert buy_base_with_quote([(100.0, 1.0)], 100.01) is None

    def test_empty_book(self) -> None:
        assert buy_base_with_quote([], 100.0) is None

    def test_non_positive_amount(self) -> None:
        assert buy_base_with_quote([(100.0, 1.0)], 0.0) is None
        assert buy_base_with_quote([(100.0, 1.0)], -5.0) is None

    def test_skips_zero_size_levels(self) -> None:
        assert buy_base_with_quote([(99.0, 0.0), (100.0, 1.0)], 100.0) == (1.0, 100.0)


class TestSell:
    def test_partial_first_level(self) -> None:
        assert sell_base_for_quote([(100.0, 1.0)], 0.5) == (50.0, 100.0)

    def test_exact_level_boundary(self) -> None:
        assert sell_base_for_quote([(100.0, 1.0)], 1.0) == (100.0, 100.0)

    def test_spans_levels(self) -> None:
        result = sell_base_for_quote([(100.0, 1.0), (99.0, 2.0)], 2.0)
        assert result is not None
        quote, worst = result
        assert quote == pytest.approx(100.0 + 99.0)
        assert worst == 99.0

    def test_insufficient_depth(self) -> None:
        assert sell_base_for_quote([(100.0, 1.0)], 1.01) is None

    def test_empty_book(self) -> None:
        assert sell_base_for_quote([], 1.0) is None

    def test_non_positive_amount(self) -> None:
        assert sell_base_for_quote([(100.0, 1.0)], 0.0) is None
