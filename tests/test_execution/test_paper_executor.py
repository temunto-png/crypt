"""PaperExecutor のテスト。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import OrderBook
from cryptbot.execution.paper_executor import PaperExecutor
from cryptbot.paper.simulator import Simulator
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _book(
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]],
) -> OrderBook:
    return OrderBook(
        pair="btc_jpy",
        asks=asks,
        bids=bids,
        timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=JST),
    )


def _run(coro):
    """asyncio.run の薄いラッパー。"""
    return asyncio.run(coro)


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(db_path=tmp_path / "test.db", data_dir=tmp_path / "data")
    s.initialize()
    return s


@pytest.fixture()
def executor(storage: Storage) -> PaperExecutor:
    return PaperExecutor(
        storage=storage,
        simulator=Simulator(),
        strategy_name="test_strategy",
    )


# ------------------------------------------------------------------ #
# 成行 BUY（OrderBook あり）
# ------------------------------------------------------------------ #

class TestPlaceOrderMarket:
    def test_market_buy_with_order_book_returns_filled(
        self, executor: PaperExecutor
    ) -> None:
        book = _book(asks=[(10_000_000.0, 1.0)], bids=[(9_900_000.0, 1.0)])
        result = _run(executor.place_order("btc_jpy", "buy", "market", 0.1, order_book=book))

        assert result.status == "FILLED"
        assert result.fill_price == pytest.approx(10_000_000.0)
        assert result.fill_size == pytest.approx(0.1)
        assert result.fee == pytest.approx(0.1 * 10_000_000.0 * 0.001)
        assert result.side == "buy"
        assert result.pair == "btc_jpy"

    def test_market_sell_with_order_book_returns_filled(
        self, executor: PaperExecutor
    ) -> None:
        book = _book(asks=[(10_100_000.0, 1.0)], bids=[(10_000_000.0, 1.0)])
        result = _run(executor.place_order("btc_jpy", "sell", "market", 0.2, order_book=book))

        assert result.status == "FILLED"
        assert result.fill_price == pytest.approx(10_000_000.0)
        assert result.fill_size == pytest.approx(0.2)

    def test_partial_fill_sets_partial_filled_status(
        self, executor: PaperExecutor
    ) -> None:
        """板の深さが不足すると PARTIAL_FILLED になる。"""
        book = _book(asks=[(10_000_000.0, 0.05)], bids=[(9_900_000.0, 1.0)])
        result = _run(executor.place_order("btc_jpy", "buy", "market", 0.1, order_book=book))

        assert result.status == "PARTIAL_FILLED"
        assert result.fill_size == pytest.approx(0.05)

    def test_order_recorded_in_storage(
        self, executor: PaperExecutor, storage: Storage
    ) -> None:
        """約定後に orders テーブルに FILLED レコードが存在する。"""
        book = _book(asks=[(10_000_000.0, 1.0)], bids=[(9_900_000.0, 1.0)])
        result = _run(executor.place_order("btc_jpy", "buy", "market", 0.1, order_book=book))

        assert result.order_id is not None
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT status FROM orders WHERE id = ?", (result.order_id,)
            ).fetchone()
        assert row is not None
        assert row["status"] == "FILLED"


# ------------------------------------------------------------------ #
# フォールバック（price のみ、OrderBook なし）
# ------------------------------------------------------------------ #

class TestPlaceOrderFallback:
    def test_fallback_uses_provided_price(self, executor: PaperExecutor) -> None:
        """OrderBook なし + price 指定 → 固定コストモデル。"""
        result = _run(
            executor.place_order("btc_jpy", "buy", "market", 0.1, price=10_000_000.0)
        )

        assert result.status == "FILLED"
        assert result.fill_price == pytest.approx(10_000_000.0)
        assert result.fill_size == pytest.approx(0.1)
        assert result.fee == pytest.approx(0.1 * 10_000_000.0 * 0.001)
        assert result.slippage_pct == pytest.approx(0.0)

    def test_no_order_book_and_no_price_raises(self, executor: PaperExecutor) -> None:
        with pytest.raises(ValueError, match="必須"):
            _run(executor.place_order("btc_jpy", "buy", "market", 0.1))


# ------------------------------------------------------------------ #
# 指値注文
# ------------------------------------------------------------------ #

class TestPlaceOrderLimit:
    def test_limit_order_fills_when_price_crossed(
        self, executor: PaperExecutor
    ) -> None:
        book = _book(asks=[(10_000_000.0, 1.0)], bids=[(9_900_000.0, 1.0)])
        result = _run(
            executor.place_order(
                "btc_jpy", "buy", "limit", 0.1,
                price=10_050_000.0,
                order_book=book,
            )
        )
        assert result.status == "FILLED"
        assert result.fee == pytest.approx(0.0)  # Maker

    def test_limit_order_cancelled_when_price_not_crossed(
        self, executor: PaperExecutor
    ) -> None:
        """指値が届かない場合は CANCELLED を返す。"""
        book = _book(asks=[(10_000_000.0, 1.0)], bids=[(9_900_000.0, 1.0)])
        result = _run(
            executor.place_order(
                "btc_jpy", "buy", "limit", 0.1,
                price=9_500_000.0,   # best_ask(10M) より安い
                order_book=book,
            )
        )
        assert result.status == "CANCELLED"
        assert result.fill_size == pytest.approx(0.0)
        assert result.order_id is None
