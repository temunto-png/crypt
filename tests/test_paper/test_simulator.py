"""Simulator のテスト。"""
from __future__ import annotations

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from cryptbot.exchanges.base import OrderBook
from cryptbot.paper.simulator import FillResult, Simulator
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _book(
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]],
) -> OrderBook:
    """テスト用 OrderBook を生成する。"""
    return OrderBook(
        pair="btc_jpy",
        asks=asks,
        bids=bids,
        timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=JST),
    )


@pytest.fixture()
def sim() -> Simulator:
    return Simulator()


# ------------------------------------------------------------------ #
# 成行 BUY
# ------------------------------------------------------------------ #

class TestSimulateMarketBuy:
    def test_single_level_full_fill(self, sim: Simulator) -> None:
        """asks の 1 レベルで全量約定。"""
        book = _book(asks=[(10_000_000.0, 0.5)], bids=[(9_990_000.0, 0.5)])
        result = sim.simulate_market_order(book, "buy", 0.3)

        assert result.fill_price == pytest.approx(10_000_000.0)
        assert result.fill_size == pytest.approx(0.3)
        assert result.fee == pytest.approx(0.3 * 10_000_000.0 * 0.001)
        assert not result.is_partial

    def test_vwap_across_multiple_levels(self, sim: Simulator) -> None:
        """複数レベルにまたがる VWAP 計算。"""
        # level1: 10M × 0.2 BTC, level2: 10.1M × 0.3 BTC
        book = _book(
            asks=[(10_000_000.0, 0.2), (10_100_000.0, 0.3)],
            bids=[(9_900_000.0, 1.0)],
        )
        result = sim.simulate_market_order(book, "buy", 0.5)

        expected_cost = 0.2 * 10_000_000.0 + 0.3 * 10_100_000.0
        expected_vwap = expected_cost / 0.5
        assert result.fill_price == pytest.approx(expected_vwap)
        assert result.fill_size == pytest.approx(0.5)
        assert result.fee == pytest.approx(expected_cost * 0.001)
        assert not result.is_partial

    def test_partial_fill_when_book_depth_insufficient(self, sim: Simulator) -> None:
        """板の深さが不足して部分約定になる。"""
        book = _book(asks=[(10_000_000.0, 0.1)], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_market_order(book, "buy", 0.5)

        assert result.fill_size == pytest.approx(0.1)
        assert result.is_partial

    def test_slippage_is_positive_for_buy(self, sim: Simulator) -> None:
        """BUY では fill_price > mid_price となりスリッページは正。"""
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_market_order(book, "buy", 0.1)

        mid_price = (10_100_000.0 + 9_900_000.0) / 2  # 10_000_000
        expected_slippage = (10_100_000.0 - mid_price) / mid_price
        assert result.slippage_pct == pytest.approx(expected_slippage)

    def test_empty_order_book_returns_partial(self, sim: Simulator) -> None:
        """板が空の場合は fill_size=0 かつ is_partial=True。"""
        book = _book(asks=[], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_market_order(book, "buy", 0.1)

        assert result.fill_size == pytest.approx(0.0)
        assert result.is_partial

    def test_invalid_size_raises(self, sim: Simulator) -> None:
        book = _book(asks=[(10_000_000.0, 1.0)], bids=[])
        with pytest.raises(ValueError, match="正の数"):
            sim.simulate_market_order(book, "buy", 0.0)


# ------------------------------------------------------------------ #
# 成行 SELL
# ------------------------------------------------------------------ #

class TestSimulateMarketSell:
    def test_single_level_full_fill(self, sim: Simulator) -> None:
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(10_000_000.0, 0.5)])
        result = sim.simulate_market_order(book, "sell", 0.3)

        assert result.fill_price == pytest.approx(10_000_000.0)
        assert result.fill_size == pytest.approx(0.3)
        assert not result.is_partial

    def test_vwap_across_multiple_levels(self, sim: Simulator) -> None:
        """複数 bid レベル（高値順）にまたがる VWAP。"""
        book = _book(
            asks=[(10_200_000.0, 1.0)],
            bids=[(10_100_000.0, 0.2), (10_000_000.0, 0.3)],
        )
        result = sim.simulate_market_order(book, "sell", 0.5)

        expected_cost = 0.2 * 10_100_000.0 + 0.3 * 10_000_000.0
        expected_vwap = expected_cost / 0.5
        assert result.fill_price == pytest.approx(expected_vwap)
        assert result.fill_size == pytest.approx(0.5)
        assert not result.is_partial

    def test_partial_fill_when_book_depth_insufficient(self, sim: Simulator) -> None:
        book = _book(asks=[(10_200_000.0, 1.0)], bids=[(10_000_000.0, 0.1)])
        result = sim.simulate_market_order(book, "sell", 0.5)

        assert result.fill_size == pytest.approx(0.1)
        assert result.is_partial


# ------------------------------------------------------------------ #
# 指値 BUY
# ------------------------------------------------------------------ #

class TestSimulateLimitBuy:
    def test_fills_when_limit_above_best_ask(self, sim: Simulator) -> None:
        """指値 > best_ask → 約定（Maker 手数料 0%）。"""
        book = _book(asks=[(10_000_000.0, 0.5)], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_limit_order(book, "buy", 0.3, limit_price=10_050_000.0)

        assert result is not None
        assert result.fill_size == pytest.approx(0.3)
        assert result.fee == pytest.approx(0.0)   # Maker = 無料

    def test_no_fill_when_limit_equals_best_ask(self, sim: Simulator) -> None:
        """指値 == best_ask → 未約定（同値は未約定扱い）。"""
        book = _book(asks=[(10_000_000.0, 0.5)], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_limit_order(book, "buy", 0.3, limit_price=10_000_000.0)
        assert result is None

    def test_no_fill_when_limit_below_best_ask(self, sim: Simulator) -> None:
        """指値 < best_ask → 未約定。"""
        book = _book(asks=[(10_000_000.0, 0.5)], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_limit_order(book, "buy", 0.3, limit_price=9_950_000.0)
        assert result is None

    def test_no_fill_when_ask_book_empty(self, sim: Simulator) -> None:
        book = _book(asks=[], bids=[(9_900_000.0, 1.0)])
        result = sim.simulate_limit_order(book, "buy", 0.1, limit_price=10_000_000.0)
        assert result is None

    def test_fills_only_levels_below_limit(self, sim: Simulator) -> None:
        """指値範囲内の板レベルのみで約定。指値超過レベルは使わない。"""
        book = _book(
            asks=[
                (9_990_000.0, 0.1),   # limit 未満 → 使用
                (10_010_000.0, 0.5),  # limit 超過 → 使用しない
            ],
            bids=[(9_800_000.0, 1.0)],
        )
        result = sim.simulate_limit_order(book, "buy", 0.3, limit_price=10_000_000.0)
        # 同値は未約定扱い（limit==10M, best_ask==9.99M < limit → 約定するが level2 は除外）
        # 板に 0.1 BTC しかないので partial
        assert result is not None
        assert result.fill_size == pytest.approx(0.1)
        assert result.is_partial


# ------------------------------------------------------------------ #
# 指値 SELL
# ------------------------------------------------------------------ #

class TestSimulateLimitSell:
    def test_fills_when_limit_below_best_bid(self, sim: Simulator) -> None:
        """指値 < best_bid → 約定（Maker 手数料 0%）。"""
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(10_000_000.0, 0.5)])
        result = sim.simulate_limit_order(book, "sell", 0.3, limit_price=9_950_000.0)

        assert result is not None
        assert result.fill_size == pytest.approx(0.3)
        assert result.fee == pytest.approx(0.0)

    def test_no_fill_when_limit_equals_best_bid(self, sim: Simulator) -> None:
        """指値 == best_bid → 未約定。"""
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(10_000_000.0, 0.5)])
        result = sim.simulate_limit_order(book, "sell", 0.3, limit_price=10_000_000.0)
        assert result is None

    def test_no_fill_when_limit_above_best_bid(self, sim: Simulator) -> None:
        """指値 > best_bid → 未約定。"""
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(10_000_000.0, 0.5)])
        result = sim.simulate_limit_order(book, "sell", 0.3, limit_price=10_050_000.0)
        assert result is None
