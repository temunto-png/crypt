"""LiveExecutor のテスト。"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import ExchangeError
from cryptbot.execution.live_executor import DuplicateOrderError, LiveExecutor
from cryptbot.utils.time_utils import JST, now_jst


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    """テスト用 Storage インスタンス（一時ディレクトリ使用）。"""
    s = Storage(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )
    s.initialize()
    return s


@pytest.fixture()
def mock_exchange() -> MagicMock:
    """モック取引所。"""
    exchange = MagicMock()
    exchange.place_order = AsyncMock(return_value={"order_id": "EX-001"})
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.get_order = AsyncMock(return_value={"status": "OPEN"})
    return exchange


@pytest.fixture()
def executor(mock_exchange: MagicMock, storage: Storage) -> LiveExecutor:
    """テスト用 LiveExecutor。"""
    return LiveExecutor(
        exchange=mock_exchange,
        storage=storage,
        strategy_name="test_strategy",
    )


# ------------------------------------------------------------------ #
# テストヘルパー
# ------------------------------------------------------------------ #

def _get_order(storage: Storage, order_id: int) -> dict:
    with storage._connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    assert row is not None, f"order_id={order_id} が存在しません"
    return dict(row)


def _get_events(storage: Storage, order_id: int) -> list[dict]:
    with storage._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM order_events WHERE order_id = ? ORDER BY id",
            (order_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ #
# P4-L01: place_order — 正常系
# ------------------------------------------------------------------ #

class TestPlaceOrderSuccess:
    @pytest.mark.asyncio
    async def test_place_order_creates_then_submits(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """place_order が CREATED → SUBMITTED に遷移し ExecutionResult を返す。"""
        result = await executor.place_order("btc_jpy", "buy", "market", 0.1)

        assert result.status == "SUBMITTED"
        assert result.pair == "btc_jpy"
        assert result.side == "buy"
        assert result.order_type == "market"
        assert result.requested_size == 0.1
        assert result.fill_price == 0.0
        assert result.fill_size == 0.0
        assert result.fee == 0.0
        assert result.slippage_pct == 0.0
        assert result.order_id is not None

        # DB が SUBMITTED になっていること
        order = _get_order(storage, result.order_id)
        assert order["status"] == "SUBMITTED"
        assert order["exchange_order_id"] == "EX-001"
        assert order["pair"] == "btc_jpy"

        # order_events に SUBMITTED が記録されていること
        events = _get_events(storage, result.order_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "SUBMITTED"

    @pytest.mark.asyncio
    async def test_place_order_with_limit_price(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """指値注文で price が DB に記録される。"""
        result = await executor.place_order(
            "btc_jpy", "sell", "limit", 0.05, price=10_000_000.0
        )
        assert result.status == "SUBMITTED"
        order = _get_order(storage, result.order_id)
        assert order["price"] == 10_000_000.0
        assert order["side"] == "SELL"

    @pytest.mark.asyncio
    async def test_place_order_exchange_called_correctly(
        self, executor: LiveExecutor, mock_exchange: MagicMock
    ) -> None:
        """取引所の place_order が正しい引数で呼ばれる。"""
        await executor.place_order("btc_jpy", "buy", "market", 0.2)
        mock_exchange.place_order.assert_awaited_once_with(
            "btc_jpy", "buy", "market", 0.2, None
        )


# ------------------------------------------------------------------ #
# P4-L02: place_order — 二重発注防止
# ------------------------------------------------------------------ #

class TestDuplicateOrderPrevention:
    @pytest.mark.asyncio
    async def test_place_order_prevents_duplicate(
        self, executor: LiveExecutor, storage: Storage
    ) -> None:
        """SUBMITTED 注文が存在する状態で place_order すると DuplicateOrderError。"""
        # 最初の注文は成功
        await executor.place_order("btc_jpy", "buy", "market", 0.1)

        # 2 回目は重複エラー
        with pytest.raises(DuplicateOrderError):
            await executor.place_order("btc_jpy", "buy", "market", 0.05)

    @pytest.mark.asyncio
    async def test_place_order_prevents_duplicate_different_side(
        self, executor: LiveExecutor, storage: Storage
    ) -> None:
        """BUY アクティブ中に SELL も DuplicateOrderError（Long-only MVP）。"""
        await executor.place_order("btc_jpy", "buy", "market", 0.1)

        with pytest.raises(DuplicateOrderError):
            await executor.place_order("btc_jpy", "sell", "market", 0.1)

    @pytest.mark.asyncio
    async def test_different_pair_allowed(
        self, executor: LiveExecutor, storage: Storage
    ) -> None:
        """異なる pair であれば重複エラーなし。"""
        await executor.place_order("btc_jpy", "buy", "market", 0.1)
        # xrp_jpy は別 pair なのでエラーにならない
        result2 = await executor.place_order("xrp_jpy", "buy", "market", 100.0)
        assert result2.status == "SUBMITTED"


# ------------------------------------------------------------------ #
# P4-L03: place_order — 取引所エラー時の SUBMIT_FAILED
# ------------------------------------------------------------------ #

class TestPlaceOrderSubmitFailed:
    @pytest.mark.asyncio
    async def test_place_order_submit_failed_on_exchange_error(
        self,
        storage: Storage,
        mock_exchange: MagicMock,
    ) -> None:
        """取引所エラー → DB が SUBMIT_FAILED に遷移し例外が再送出される。"""
        mock_exchange.place_order = AsyncMock(
            side_effect=ExchangeError("connection timeout", status_code=503)
        )
        executor = LiveExecutor(exchange=mock_exchange, storage=storage)

        with pytest.raises(ExchangeError):
            await executor.place_order("btc_jpy", "buy", "market", 0.1)

        # DB で SUBMIT_FAILED になっていることを確認
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT status FROM orders WHERE pair='btc_jpy'"
            ).fetchone()
        assert row is not None
        assert row["status"] == "SUBMIT_FAILED"

        # order_events にも SUBMIT_FAILED が記録されていること
        with storage._connect() as conn:
            event_row = conn.execute(
                "SELECT event_type FROM order_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert event_row["event_type"] == "SUBMIT_FAILED"

    @pytest.mark.asyncio
    async def test_place_order_sanitizes_error(
        self,
        storage: Storage,
        mock_exchange: MagicMock,
    ) -> None:
        """エラーに含まれる API キー相当文字列が REDACTED に置換されて記録される。"""
        fake_api_key = "a" * 32  # 20 文字以上の英数字 → sanitize_error がマスクする
        mock_exchange.place_order = AsyncMock(
            side_effect=ExchangeError(f"Unauthorized: apikey={fake_api_key}")
        )
        executor = LiveExecutor(exchange=mock_exchange, storage=storage)

        with pytest.raises(ExchangeError):
            await executor.place_order("btc_jpy", "buy", "market", 0.1)

        # order_events の note に API キーが含まれていないこと
        with storage._connect() as conn:
            event = conn.execute(
                "SELECT note FROM order_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert event is not None
        assert fake_api_key not in (event["note"] or ""), (
            f"note にフェイク API キーが含まれています: {event['note']}"
        )
        assert "[REDACTED]" in (event["note"] or "")


# ------------------------------------------------------------------ #
# P4-L04: cancel_order
# ------------------------------------------------------------------ #

class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_updates_db(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """キャンセル成功 → DB が CANCELLED に遷移する。"""
        result = await executor.place_order("btc_jpy", "buy", "market", 0.1)
        order_id = result.order_id
        assert order_id is not None

        cancelled = await executor.cancel_order("btc_jpy", str(order_id))
        assert cancelled is True

        order = _get_order(storage, order_id)
        assert order["status"] == "CANCELLED"

        events = _get_events(storage, order_id)
        event_types = [e["event_type"] for e in events]
        assert "CANCELLED" in event_types

    @pytest.mark.asyncio
    async def test_cancel_order_exchange_called_with_exchange_order_id(
        self, executor: LiveExecutor, mock_exchange: MagicMock
    ) -> None:
        """cancel_order が exchange_order_id（EX-001）を渡して取引所を呼ぶ。"""
        result = await executor.place_order("btc_jpy", "buy", "market", 0.1)
        await executor.cancel_order("btc_jpy", str(result.order_id))

        mock_exchange.cancel_order.assert_awaited_once_with("btc_jpy", "EX-001")

    @pytest.mark.asyncio
    async def test_cancel_order_no_exchange_id_cancels_locally(
        self, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """exchange_order_id が未設定の場合はローカル DB のみ CANCELLED にする。"""
        # CREATED 状態のまま（exchange_order_id なし）の注文を直接挿入
        order_id = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "market",
            "size": 0.1,
            "status": "CREATED",
        })
        executor = LiveExecutor(exchange=mock_exchange, storage=storage)
        cancelled = await executor.cancel_order("btc_jpy", str(order_id))

        assert cancelled is True
        order = _get_order(storage, order_id)
        assert order["status"] == "CANCELLED"
        mock_exchange.cancel_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_order_exchange_returns_false(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """取引所が False を返した場合（既に約定済み）は DB 更新なし。"""
        mock_exchange.cancel_order = AsyncMock(return_value=False)
        result = await executor.place_order("btc_jpy", "buy", "market", 0.1)

        cancelled = await executor.cancel_order("btc_jpy", str(result.order_id))
        assert cancelled is False

        order = _get_order(storage, result.order_id)
        assert order["status"] == "SUBMITTED"  # 変わらない


# ------------------------------------------------------------------ #
# P4-L05: get_open_orders
# ------------------------------------------------------------------ #

class TestGetOpenOrders:
    @pytest.mark.asyncio
    async def test_get_open_orders_returns_active_only(
        self, executor: LiveExecutor, storage: Storage
    ) -> None:
        """SUBMITTED 注文のみ返し、FILLED は含まない。"""
        # SUBMITTED 注文を作成
        result = await executor.place_order("btc_jpy", "buy", "market", 0.1)
        submitted_id = result.order_id

        # FILLED 注文を直接挿入
        filled_id = storage.insert_order({
            "pair": "btc_jpy",
            "side": "SELL",
            "order_type": "market",
            "size": 0.05,
            "status": "FILLED",
        })

        open_orders = await executor.get_open_orders("btc_jpy")
        order_ids = [o["id"] for o in open_orders]

        assert submitted_id in order_ids
        assert filled_id not in order_ids

    @pytest.mark.asyncio
    async def test_get_open_orders_empty_when_none(
        self, executor: LiveExecutor
    ) -> None:
        """アクティブ注文なしの場合は空リストを返す。"""
        open_orders = await executor.get_open_orders("btc_jpy")
        assert open_orders == []

    @pytest.mark.asyncio
    async def test_get_open_orders_different_pair_excluded(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """異なる pair の注文は含まれない。"""
        await executor.place_order("xrp_jpy", "buy", "market", 100.0)

        open_orders = await executor.get_open_orders("btc_jpy")
        assert open_orders == []


# ------------------------------------------------------------------ #
# P4-L06: handle_partial_fill
# ------------------------------------------------------------------ #

class TestHandlePartialFill:
    @pytest.mark.asyncio
    async def test_fully_filled_updates_db(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """FULLY_FILLED 時に FILLED に遷移する。"""
        # PARTIAL 状態の注文を用意
        order_id = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "market",
            "size": 0.1,
            "status": "CREATED",
        })
        storage.update_order_status(order_id, "SUBMITTED", exchange_order_id="EX-001")
        storage.insert_order_event(order_id, "SUBMITTED")
        storage.update_order_status(order_id, "PARTIAL")

        mock_exchange.get_order = AsyncMock(return_value={
            "status": "FULLY_FILLED",
            "executed_amount": "0.1",
            "average_price": "9500000",
        })

        await executor.handle_partial_fill(
            pair="btc_jpy",
            db_order_id=order_id,
            exchange_order_id="EX-001",
            created_at_iso=now_jst().isoformat(),
            partial_fill_timeout_sec=1800,
        )

        order = _get_order(storage, order_id)
        assert order["status"] == "FILLED"
        assert order["fill_price"] == 9_500_000.0
        assert order["fill_size"] == 0.1

    @pytest.mark.asyncio
    async def test_partial_fill_timeout_cancels(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """タイムアウト超過時に cancel_order が呼ばれる。"""
        order_id = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "market",
            "size": 0.1,
            "status": "CREATED",
        })
        storage.update_order_status(order_id, "SUBMITTED", exchange_order_id="EX-001")
        storage.insert_order_event(order_id, "SUBMITTED")

        mock_exchange.get_order = AsyncMock(return_value={"status": "PARTIAL_FILLED"})
        mock_exchange.cancel_order = AsyncMock(return_value=True)

        # 作成時刻を 1 時間前に設定してタイムアウトを発生させる
        from datetime import timedelta
        old_time = (now_jst() - timedelta(hours=1)).isoformat()

        await executor.handle_partial_fill(
            pair="btc_jpy",
            db_order_id=order_id,
            exchange_order_id="EX-001",
            created_at_iso=old_time,
            partial_fill_timeout_sec=1800,  # 30 分 = タイムアウト
        )

        mock_exchange.cancel_order.assert_awaited_once()


# ------------------------------------------------------------------ #
# P4-L07: 後方互換（BaseExecutor のサブクラス確認）
# ------------------------------------------------------------------ #

class TestLiveExecutorInterface:
    def test_is_subclass_of_base_executor(self) -> None:
        from cryptbot.execution.base import BaseExecutor
        assert issubclass(LiveExecutor, BaseExecutor)

    def test_duplicate_order_error_is_runtime_error(self) -> None:
        assert issubclass(DuplicateOrderError, RuntimeError)


# ------------------------------------------------------------------ #
# NF1: handle_partial_fill() が OrderSyncResult を返すテスト
# ------------------------------------------------------------------ #

class TestHandlePartialFillResult:
    """handle_partial_fill() が OrderSyncResult を返すことのテスト。"""

    @pytest.mark.asyncio
    async def test_fully_filled_returns_terminal_result(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """FULLY_FILLED → terminal=True, status="FULLY_FILLED", 正確な数量・価格。"""
        result_order = await executor.place_order("btc_jpy", "buy", "market", 0.05)
        mock_exchange.get_order = AsyncMock(return_value={
            "status": "FULLY_FILLED",
            "executed_amount": "0.05",
            "average_price": "5000000",
        })

        result = await executor.handle_partial_fill(
            pair="btc_jpy",
            db_order_id=result_order.order_id,
            exchange_order_id="EX-001",
            created_at_iso=now_jst().isoformat(),
            partial_fill_timeout_sec=7200,
            side="BUY",
        )

        assert result.terminal is True
        assert result.status == "FULLY_FILLED"
        assert result.executed_amount == pytest.approx(0.05)
        assert result.average_price == pytest.approx(5_000_000.0)
        assert result.side == "BUY"

    @pytest.mark.asyncio
    async def test_waiting_returns_non_terminal_result(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """UNFILLED（待機中）→ terminal=False, status="WAITING"。"""
        result_order = await executor.place_order("btc_jpy", "buy", "market", 0.05)
        mock_exchange.get_order = AsyncMock(return_value={
            "status": "UNFILLED",
            "executed_amount": "0",
            "average_price": "0",
        })

        result = await executor.handle_partial_fill(
            pair="btc_jpy",
            db_order_id=result_order.order_id,
            exchange_order_id="EX-001",
            created_at_iso=now_jst().isoformat(),
            partial_fill_timeout_sec=7200,
            side="BUY",
        )

        assert result.terminal is False
        assert result.status == "WAITING"

    @pytest.mark.asyncio
    async def test_timeout_returns_terminal_cancelled(
        self, executor: LiveExecutor, storage: Storage, mock_exchange: MagicMock
    ) -> None:
        """タイムアウト → terminal=True, status="TIMEOUT_CANCELLED"。"""
        result_order = await executor.place_order("btc_jpy", "buy", "market", 0.05)
        mock_exchange.get_order = AsyncMock(return_value={
            "status": "UNFILLED",
            "executed_amount": "0",
            "average_price": "0",
        })
        mock_exchange.cancel_order = AsyncMock(return_value=True)
        old_time = (now_jst() - timedelta(hours=3)).isoformat()

        result = await executor.handle_partial_fill(
            pair="btc_jpy",
            db_order_id=result_order.order_id,
            exchange_order_id="EX-001",
            created_at_iso=old_time,
            partial_fill_timeout_sec=3600,
            side="BUY",
        )

        assert result.terminal is True
        assert result.status == "TIMEOUT_CANCELLED"
