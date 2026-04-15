"""LiveEngine のテスト。"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings, LiveSettings
from cryptbot.data.storage import Storage
from cryptbot.execution.base import ExecutionResult
from cryptbot.execution.live_executor import DuplicateOrderError, LiveExecutor
from cryptbot.live.engine import LiveEngine, LiveRunResult
from cryptbot.live.state import LiveState, LiveStateStore
from cryptbot.risk.kill_switch import KillSwitch
from cryptbot.risk.manager import PortfolioState, RiskManager
from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# 合成戦略
# ------------------------------------------------------------------ #

class AlwaysHoldStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("always_hold")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return self._hold("always hold", datetime.now(JST))


class AlwaysBuyStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("always_buy")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(Direction.BUY, 1.0, "always_buy", datetime.now(JST))


class AlwaysSellStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("always_sell")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(Direction.SELL, 1.0, "always_sell", datetime.now(JST))


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_ohlcv(rows: int = 60, start_price: float = 5_000_000.0) -> pd.DataFrame:
    base = datetime(2024, 1, 1, 9, 0, tzinfo=JST)
    timestamps = [base + timedelta(hours=i) for i in range(rows)]
    prices = [start_price + i * 1000.0 for i in range(rows)]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.002 for p in prices],
        "low": [p * 0.998 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [10.0] * rows,
    })


def _make_portfolio(balance: float = 1_000_000.0) -> PortfolioState:
    today = date(2024, 1, 1)
    return PortfolioState(
        balance=balance,
        peak_balance=balance,
        position_size=0.0,
        entry_price=0.0,
        daily_loss=0.0,
        weekly_loss=0.0,
        monthly_loss=0.0,
        consecutive_losses=0,
        last_reset_date=today,
        last_weekly_reset=today,
        last_monthly_reset=today,
    )


def _make_submitted_result(pair: str = "btc_jpy", side: str = "buy") -> ExecutionResult:
    return ExecutionResult(
        pair=pair,
        side=side,
        order_type="market",
        requested_size=0.0001,
        fill_price=0.0,
        fill_size=0.0,
        fee=0.0,
        slippage_pct=0.0,
        status="SUBMITTED",
        order_id=1,
    )


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(db_path=tmp_path / "test.db", data_dir=tmp_path / "data")
    s.initialize()
    return s


@pytest.fixture()
def kill_switch(storage: Storage) -> KillSwitch:
    return KillSwitch(storage)


@pytest.fixture()
def risk_manager(kill_switch: KillSwitch) -> RiskManager:
    return RiskManager(
        settings=CircuitBreakerSettings(),
        cvar_settings=CvarSettings(),
        kill_switch=kill_switch,
    )


@pytest.fixture()
def state_store(storage: Storage) -> LiveStateStore:
    st = LiveStateStore(storage)
    st.initialize()
    return st


@pytest.fixture()
def mock_executor() -> MagicMock:
    ex = MagicMock(spec=LiveExecutor)
    ex.place_order = AsyncMock(return_value=_make_submitted_result())
    ex.get_open_orders = AsyncMock(return_value=[])
    ex.cancel_order = AsyncMock(return_value=True)
    ex.handle_partial_fill = AsyncMock()
    return ex


@pytest.fixture()
def settings() -> LiveSettings:
    return LiveSettings(
        pair="btc_jpy",
        timeframe="1hour",
        warmup_bars=5,
        strategy_name="ma_cross",
        partial_fill_poll_interval_sec=60,
        min_order_size_btc=0.0001,
    )


def _make_engine(
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    state_store: LiveStateStore,
    storage: Storage,
    executor: MagicMock,
    settings: LiveSettings,
) -> LiveEngine:
    return LiveEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        executor=executor,
        settings=settings,
    )


def _seed_state(state_store: LiveStateStore, balance: float = 1_000_000.0) -> None:
    """テスト用に有効な初期残高をもつ LiveState を state_store に保存する。

    balance=0 のまま run_one_bar() を呼ぶと
    monthly_limit=0 のため circuit breaker が即時発動するのを防ぐ。
    """
    state = LiveStateStore.from_portfolio_state(
        portfolio=_make_portfolio(balance),
        pending_signal=None,
        pending_cvar_action="normal",
        recent_trade_returns=[],
        position_entry_time=None,
        position_entry_price=0.0,
        last_processed_bar_ts=None,
    )
    state_store.save(state)


# ------------------------------------------------------------------ #
# run_one_bar: 基本動作
# ------------------------------------------------------------------ #

class TestRunOneBarBasic:
    @pytest.mark.asyncio
    async def test_empty_data_returns_skipped(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """空 DataFrame の場合は skipped=True を返す。"""
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        result = await engine.run_one_bar(pd.DataFrame())
        assert result.skipped is True
        assert result.skip_reason == "empty_data"

    @pytest.mark.asyncio
    async def test_first_run_initializes_state(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """初回実行: 状態なしで run_one_bar() が正常終了し状態が保存される。

        balance=0 で circuit breaker が発動しないよう事前に状態を seed する。
        """
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        result = await engine.run_one_bar(data)

        assert result.skipped is False
        assert state_store.load() is not None  # 状態が保存された

    @pytest.mark.asyncio
    async def test_duplicate_bar_skipped(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """同一 bar_ts を2回処理した場合は2回目が skipped=True。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        await engine.run_one_bar(data)
        result = await engine.run_one_bar(data)  # 同じデータ = 同じ bar_ts

        assert result.skipped is True
        assert result.skip_reason == "already_processed"

    @pytest.mark.asyncio
    async def test_kill_switch_active_skips_and_saves(
        self, kill_switch, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """kill switch が active の場合は order_submitted=False でスキップ。"""
        kill_switch.activate(
            reason=__import__("cryptbot.risk.kill_switch", fromlist=["KillSwitchReason"]).KillSwitchReason.MANUAL,
            portfolio_value=1_000_000.0,
            drawdown_pct=0.0,
        )
        engine = _make_engine(
            AlwaysBuyStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        result = await engine.run_one_bar(data)

        assert result.skipped is True
        assert result.skip_reason == "kill_switch_active"
        mock_executor.place_order.assert_not_awaited()


# ------------------------------------------------------------------ #
# run_one_bar: 注文発行
# ------------------------------------------------------------------ #

class TestRunOneBarOrderPlacement:
    @pytest.mark.asyncio
    async def test_buy_signal_pending_places_buy_order(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """BUY pending signal が次のバーで executor.place_order(side='buy') を呼ぶ。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysBuyStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)

        # 1バー目: BUY シグナルを pending に積む（発注なし）
        r1 = await engine.run_one_bar(data)
        assert r1.signal is not None
        assert r1.signal.direction == Direction.BUY
        mock_executor.place_order.assert_not_awaited()

        # 2バー目: 新しい bar_ts で run_one_bar → pending BUY が発注される
        data2 = _make_ohlcv(rows=11)
        r2 = await engine.run_one_bar(data2)
        assert r2.order_submitted is True
        mock_executor.place_order.assert_awaited_once()
        call_kwargs = mock_executor.place_order.call_args
        assert call_kwargs.kwargs.get("side") == "buy" or call_kwargs.args[1] == "buy"

    @pytest.mark.asyncio
    async def test_sell_signal_pending_places_sell_order(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """SELL pending signal が次のバーで executor.place_order(side='sell') を呼ぶ。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysSellStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        # 1バー目: SELL pending を積む
        await engine.run_one_bar(data)

        # 2バー目: SELL 発注
        data2 = _make_ohlcv(rows=11)
        r2 = await engine.run_one_bar(data2)
        assert r2.order_submitted is True
        call_kwargs = mock_executor.place_order.call_args
        assert call_kwargs.kwargs.get("side") == "sell" or call_kwargs.args[1] == "sell"

    @pytest.mark.asyncio
    async def test_hold_signal_does_not_place_order(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """HOLD シグナル pending では発注しない。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        await engine.run_one_bar(data)

        data2 = _make_ohlcv(rows=11)
        r2 = await engine.run_one_bar(data2)
        assert r2.order_submitted is False
        mock_executor.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_order_error_does_not_crash(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """DuplicateOrderError が発生しても run_one_bar() はクラッシュしない。"""
        _seed_state(state_store)
        mock_executor.place_order = AsyncMock(
            side_effect=DuplicateOrderError("duplicate order")
        )
        engine = _make_engine(
            AlwaysBuyStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        # 1バー目: pending 積む
        data = _make_ohlcv(rows=10)
        await engine.run_one_bar(data)

        # 2バー目: DuplicateOrderError が発生してもクラッシュしない
        data2 = _make_ohlcv(rows=11)
        result = await engine.run_one_bar(data2)
        assert result.order_submitted is False


# ------------------------------------------------------------------ #
# run_one_bar: シグナル生成
# ------------------------------------------------------------------ #

class TestRunOneBarSignal:
    @pytest.mark.asyncio
    async def test_returns_generated_signal(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """run_one_bar() は戦略が生成したシグナルを result.signal で返す。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysBuyStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        result = await engine.run_one_bar(data)

        assert result.signal is not None
        assert result.signal.direction == Direction.BUY

    @pytest.mark.asyncio
    async def test_hold_signal_not_saved_as_pending(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """HOLD シグナルは pending に保存されない。"""
        _seed_state(state_store)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        data = _make_ohlcv(rows=10)
        await engine.run_one_bar(data)

        state = state_store.load()
        assert state is not None
        assert state.pending_signal_direction is None


# ------------------------------------------------------------------ #
# _poll_active_orders
# ------------------------------------------------------------------ #

class TestPollPartialFills:
    @pytest.mark.asyncio
    async def test_no_partial_orders_no_calls(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """PARTIAL 注文がない場合は handle_partial_fill を呼ばない。"""
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        # get_active_orders が空を返す
        with patch.object(storage, "get_active_orders", return_value=[]):
            await engine._poll_active_orders()
        mock_executor.handle_partial_fill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_orders_trigger_handle(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """PARTIAL ステータスの注文があれば handle_partial_fill が呼ばれる。"""
        partial_order = {
            "id": 42,
            "status": "PARTIAL",
            "exchange_order_id": "EX_789",
            "created_at": "2024-01-01T09:00:00+09:00",
        }
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        with patch.object(storage, "get_active_orders", return_value=[partial_order]):
            await engine._poll_active_orders()

        mock_executor.handle_partial_fill.assert_awaited_once_with(
            pair="btc_jpy",
            db_order_id=42,
            exchange_order_id="EX_789",
            created_at_iso="2024-01-01T09:00:00+09:00",
            partial_fill_timeout_sec=settings.order_timeout_sec,
        )

    @pytest.mark.asyncio
    async def test_submitted_orders_are_handled(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """SUBMITTED ステータスの注文も handle_partial_fill を呼ぶ。"""
        submitted_order = {
            "id": 10,
            "status": "SUBMITTED",
            "exchange_order_id": "EX_100",
            "created_at": "2024-01-01T09:00:00+09:00",
        }
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        with patch.object(storage, "get_active_orders", return_value=[submitted_order]):
            await engine._poll_active_orders()

        mock_executor.handle_partial_fill.assert_awaited_once_with(
            pair="btc_jpy",
            db_order_id=10,
            exchange_order_id="EX_100",
            created_at_iso="2024-01-01T09:00:00+09:00",
            partial_fill_timeout_sec=settings.order_timeout_sec,
        )

    @pytest.mark.asyncio
    async def test_submitted_order_fully_filled_transitions(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """SUBMITTED 注文が取引所で FULLY_FILLED の場合も handle_partial_fill を呼ぶ。"""
        submitted_order = {
            "id": 11,
            "status": "SUBMITTED",
            "exchange_order_id": "EX_200",
            "created_at": "2024-01-01T10:00:00+09:00",
        }
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        with patch.object(storage, "get_active_orders", return_value=[submitted_order]):
            await engine._poll_active_orders()

        mock_executor.handle_partial_fill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_active_orders_handles_both_statuses(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """SUBMITTED と PARTIAL が混在しても両方 handle_partial_fill を呼ぶ。"""
        orders = [
            {"id": 1, "status": "SUBMITTED", "exchange_order_id": "EX_1", "created_at": "2024-01-01T09:00:00+09:00"},
            {"id": 2, "status": "PARTIAL",   "exchange_order_id": "EX_2", "created_at": "2024-01-01T09:30:00+09:00"},
            {"id": 3, "status": "FILLED",    "exchange_order_id": "EX_3", "created_at": "2024-01-01T08:00:00+09:00"},
        ]
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        with patch.object(storage, "get_active_orders", return_value=orders):
            await engine._poll_active_orders()

        assert mock_executor.handle_partial_fill.await_count == 2

    @pytest.mark.asyncio
    async def test_handle_error_does_not_crash(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """handle_partial_fill が例外を送出しても _poll_active_orders はクラッシュしない。"""
        mock_executor.handle_partial_fill = AsyncMock(
            side_effect=RuntimeError("exchange error")
        )
        partial_order = {
            "id": 1,
            "status": "PARTIAL",
            "exchange_order_id": "EX_1",
            "created_at": "2024-01-01T09:00:00+09:00",
        }
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        with patch.object(storage, "get_active_orders", return_value=[partial_order]):
            await engine._poll_active_orders()  # 例外が伝播しないこと


# ------------------------------------------------------------------ #
# _resolve_timeframe_seconds
# ------------------------------------------------------------------ #

class TestResolveTimeframeSeconds:
    def test_1hour(self, risk_manager, state_store, storage, mock_executor, settings) -> None:
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        assert engine._resolve_timeframe_seconds() == 3600

    def test_unknown_fallback(self, risk_manager, state_store, storage, mock_executor) -> None:
        custom_settings = LiveSettings(timeframe="unknown")
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, custom_settings
        )
        assert engine._resolve_timeframe_seconds() == 3600  # fallback


# ------------------------------------------------------------------ #
# run(): graceful shutdown
# ------------------------------------------------------------------ #

class TestRunGracefulShutdown:
    @pytest.mark.asyncio
    async def test_cancellation_shuts_down_cleanly(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """asyncio.CancelledError で run() が graceful shutdown する。"""
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )

        async def _run_and_cancel() -> None:
            task = asyncio.create_task(engine.run())
            await asyncio.sleep(0)  # タスクを開始させる
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # 期待通り

        await _run_and_cancel()  # 例外が出なければ OK


# ------------------------------------------------------------------ #
# _sync_initial_balance / run(assets)
# ------------------------------------------------------------------ #

class TestSyncInitialBalance:
    """_sync_initial_balance / run(assets) のテスト。"""

    @pytest.mark.asyncio
    async def test_first_run_sets_balance_from_assets(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """初回起動時に assets["jpy"] が LiveState.balance に反映される。"""
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        assets = {"jpy": 500_000.0, "btc": 0.0}
        await engine._sync_initial_balance(assets)

        loaded = state_store.load()
        assert loaded is not None
        assert loaded.balance == 500_000.0

    @pytest.mark.asyncio
    async def test_first_run_with_btc_logs_warning(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """初回起動時に BTC 残高がある場合 WARNING が出る。"""
        import logging
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        assets = {"jpy": 500_000.0, "btc": 0.001}
        with patch("cryptbot.live.engine.logger") as mock_logger:
            await engine._sync_initial_balance(assets)
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_restart_syncs_balance_within_tolerance(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """再起動時に許容差内の乖離は実残高で上書きされる。"""
        _seed_state(state_store, balance=1_000_000.0)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        # balance_sync_tolerance_pct=0.001 → 1000円未満の差は許容
        assets = {"jpy": 1_000_500.0, "btc": 0.0}
        await engine._sync_initial_balance(assets)

        loaded = state_store.load()
        assert loaded is not None
        assert loaded.balance == 1_000_500.0

    @pytest.mark.asyncio
    async def test_restart_raises_on_tolerance_exceeded(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """再起動時に乖離が許容差超過なら LiveGateError を raise する。"""
        from cryptbot.live.gate import LiveGateError
        _seed_state(state_store, balance=1_000_000.0)
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        # 10% 乖離 → tolerance 0.1% を超える
        assets = {"jpy": 900_000.0, "btc": 0.0}
        with pytest.raises(LiveGateError):
            await engine._sync_initial_balance(assets)

    @pytest.mark.asyncio
    async def test_assets_none_skips_sync(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """assets=None の場合は同期をスキップする。"""
        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        await engine._sync_initial_balance(None)

        assert state_store.load() is None

    @pytest.mark.asyncio
    async def test_restart_updates_peak_balance_if_higher(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """再起動時に実残高が peak_balance を超える場合、peak_balance も更新される。"""
        _seed_state(state_store, balance=1_000_000.0)
        # peak_balance を balance と同じ値に設定
        existing = state_store.load()
        existing.peak_balance = 1_000_000.0
        state_store.save(existing)

        engine = _make_engine(
            AlwaysHoldStrategy(), risk_manager, state_store, storage, mock_executor, settings
        )
        # balance=1_000_000 → jpy=1_000_500 (許容差内 0.05%)、peak より高い
        assets = {"jpy": 1_000_500.0, "btc": 0.0}
        await engine._sync_initial_balance(assets)

        loaded = state_store.load()
        assert loaded is not None
        assert loaded.balance == 1_000_500.0
        assert loaded.peak_balance == 1_000_500.0
