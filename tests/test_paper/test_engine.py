"""PaperEngine のテスト。"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from cryptbot.backtest.engine import TradeRecord
from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings, PaperSettings
from cryptbot.data.normalizer import normalize
from cryptbot.data.storage import Storage
from cryptbot.paper.engine import PaperEngine, PaperRunResult
from cryptbot.paper.state import PaperStateStore
from cryptbot.risk.kill_switch import KillSwitch
from cryptbot.risk.manager import RiskManager, PortfolioState
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


class BuyAtBarStrategy(BaseStrategy):
    """指定バーインデックスで BUY/SELL を返す合成戦略。

    len(data)-1 がウィンドウ末尾のインデックス。
    """

    def __init__(self, buy_bar: int, sell_bar: int) -> None:
        super().__init__("buy_at_bar")
        self._buy_bar = buy_bar
        self._sell_bar = sell_bar

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        idx = len(data) - 1
        ts = datetime.now(JST)
        if idx == self._buy_bar:
            return Signal(Direction.BUY, 1.0, "test_buy", ts)
        elif idx == self._sell_bar:
            return Signal(Direction.SELL, 1.0, "test_sell", ts)
        return self._hold("hold", ts)


class AlwaysBuyStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("always_buy")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(Direction.BUY, 1.0, "always_buy", datetime.now(JST))


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_ohlcv(rows: int = 60, start_price: float = 5_000_000.0) -> pd.DataFrame:
    """正規化済み合成 OHLCV DataFrame を返す。"""
    base = datetime(2024, 1, 1, 9, 0, tzinfo=JST)
    timestamps = [base + timedelta(hours=i) for i in range(rows)]
    prices = [start_price + i * 1000.0 for i in range(rows)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.002 for p in prices],
        "low": [p * 0.998 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [1.0] * rows,
    })
    return normalize(df)


def _make_engine(
    tmp_path: Path,
    strategy: BaseStrategy,
    initial_balance: float = 1_000_000.0,
) -> tuple[PaperEngine, PaperStateStore, Storage]:
    """テスト用 PaperEngine を組み立てる。"""
    storage = Storage(db_path=tmp_path / "paper.db", data_dir=tmp_path / "data")
    storage.initialize()

    kill_switch = KillSwitch(storage)
    risk_manager = RiskManager(
        settings=CircuitBreakerSettings(),
        cvar_settings=CvarSettings(),
        kill_switch=kill_switch,
    )
    settings = PaperSettings(initial_balance=initial_balance)
    state_store = PaperStateStore(storage)
    state_store.initialize()

    engine = PaperEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        settings=settings,
    )
    return engine, state_store, storage


# ------------------------------------------------------------------ #
# 基本動作
# ------------------------------------------------------------------ #

class TestPaperEngineBasic:
    def test_empty_data_skips(self, tmp_path: Path) -> None:
        """空データはスキップを返す。"""
        engine, _, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        result = engine.run_one_bar(pd.DataFrame())
        assert result.skipped is True
        assert result.skip_reason == "empty_data"

    def test_first_run_no_trade(self, tmp_path: Path) -> None:
        """初回実行は pending_signal がないためトレードなし。"""
        engine, _, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        data = _make_ohlcv(rows=55)
        result = engine.run_one_bar(data)
        assert result.skipped is False
        assert result.trade is None
        assert result.portfolio.balance == pytest.approx(1_000_000.0)

    def test_hold_signal_no_trade(self, tmp_path: Path) -> None:
        """HOLD シグナルではトレードが発生しない。"""
        engine, _, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        data = _make_ohlcv(rows=55)
        # 初回で HOLD を pending に保存
        engine.run_one_bar(data)
        # 2 回目: HOLD pending → 約定なし
        data2 = _make_ohlcv(rows=55, start_price=5_010_000.0)
        result = engine.run_one_bar(data2)
        assert result.trade is None

    def test_state_persisted_after_run(self, tmp_path: Path) -> None:
        """run_one_bar 後に状態が DB に保存される。"""
        engine, state_store, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        data = _make_ohlcv(rows=55)
        engine.run_one_bar(data)
        loaded = state_store.load()
        assert loaded is not None
        assert loaded.balance == pytest.approx(1_000_000.0)


# ------------------------------------------------------------------ #
# Buy → Sell ラウンドトリップ
# ------------------------------------------------------------------ #

class TestRoundTrip:
    def test_buy_then_sell_records_trade(self, tmp_path: Path) -> None:
        """BUY → SELL でトレードが記録される。

        PaperEngine は data 全体を渡すため len(data)-1 が最後のインデックス。
        - Run 1: data 54本 → last index=53=buy_bar → BUY pending 生成
        - Run 2: data 55本 → last index=54=sell_bar → BUY 約定 + SELL pending 生成
        - Run 3: data 56本 → SELL 約定 → trade 確定
        """
        buy_bar = 53
        sell_bar = 54
        strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
        engine, _, _ = _make_engine(tmp_path, strategy)

        # Run 1: last index = 53 = buy_bar → BUY シグナル pending
        data1 = _make_ohlcv(rows=buy_bar + 1)
        result1 = engine.run_one_bar(data1)
        assert result1.trade is None
        assert result1.signal is not None
        assert result1.signal.direction == Direction.BUY

        # Run 2: last index = 54 = sell_bar → BUY 約定 + SELL pending
        data2 = _make_ohlcv(rows=sell_bar + 1)
        result2 = engine.run_one_bar(data2)
        assert result2.portfolio.position_size > 0
        assert result2.signal is not None
        assert result2.signal.direction == Direction.SELL

        # Run 3: SELL 約定 → trade 確定
        data3 = _make_ohlcv(rows=sell_bar + 2)
        result3 = engine.run_one_bar(data3)
        assert result3.trade is not None
        assert result3.portfolio.position_size == pytest.approx(0.0)

    def test_trade_balance_update(self, tmp_path: Path) -> None:
        """ラウンドトリップ後の残高が entry_cost - exit_proceeds から計算できる。"""
        buy_bar = 53
        sell_bar = 54
        strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
        engine, _, _ = _make_engine(tmp_path, strategy, initial_balance=1_000_000.0)

        engine.run_one_bar(_make_ohlcv(rows=buy_bar + 1))
        engine.run_one_bar(_make_ohlcv(rows=sell_bar + 1))
        result3 = engine.run_one_bar(_make_ohlcv(rows=sell_bar + 2))

        assert result3.trade is not None
        # 残高が初期と異なる（コストがかかっているはず）
        assert result3.portfolio.balance != pytest.approx(1_000_000.0)

    def test_orders_recorded_in_db(self, tmp_path: Path) -> None:
        """約定が orders テーブルに記録される。"""
        buy_bar = 53
        sell_bar = 54
        strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
        engine, _, storage = _make_engine(tmp_path, strategy)

        engine.run_one_bar(_make_ohlcv(rows=buy_bar + 1))
        engine.run_one_bar(_make_ohlcv(rows=sell_bar + 1))
        engine.run_one_bar(_make_ohlcv(rows=sell_bar + 2))

        with storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status = 'FILLED'"
            ).fetchone()[0]
        assert count >= 2  # BUY FILLED + SELL FILLED


# ------------------------------------------------------------------ #
# 冪等性（同一バーの二重処理防止）
# ------------------------------------------------------------------ #

class TestIdempotency:
    def test_same_bar_skipped(self, tmp_path: Path) -> None:
        """同一データで 2 回 run_one_bar を呼ぶと 2 回目はスキップ。"""
        engine, _, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        data = _make_ohlcv(rows=55)
        result1 = engine.run_one_bar(data)
        result2 = engine.run_one_bar(data)  # 同一 timestamp
        assert result1.skipped is False
        assert result2.skipped is True
        assert result2.skip_reason == "already_processed"

    def test_new_bar_processed(self, tmp_path: Path) -> None:
        """新しいバーは処理される。"""
        engine, _, _ = _make_engine(tmp_path, AlwaysHoldStrategy())
        data1 = _make_ohlcv(rows=55)
        engine.run_one_bar(data1)

        data2 = _make_ohlcv(rows=56)  # 1 本追加
        result = engine.run_one_bar(data2)
        assert result.skipped is False


# ------------------------------------------------------------------ #
# Kill Switch
# ------------------------------------------------------------------ #

class TestKillSwitch:
    def test_kill_switch_active_skips_processing(self, tmp_path: Path) -> None:
        """kill switch が active なら処理をスキップして状態を保存。"""
        storage = Storage(db_path=tmp_path / "paper.db", data_dir=tmp_path / "data")
        storage.initialize()

        kill_switch = KillSwitch(storage)
        kill_switch.activate(
            reason=__import__("cryptbot.risk.kill_switch", fromlist=["KillSwitchReason"]).KillSwitchReason.MANUAL,
            portfolio_value=1_000_000.0,
            drawdown_pct=0.0,
        )
        risk_manager = RiskManager(
            settings=CircuitBreakerSettings(),
            cvar_settings=CvarSettings(),
            kill_switch=kill_switch,
        )
        state_store = PaperStateStore(storage)
        state_store.initialize()
        engine = PaperEngine(
            strategy=AlwaysHoldStrategy(),
            risk_manager=risk_manager,
            state_store=state_store,
            storage=storage,
            settings=PaperSettings(),
        )
        data = _make_ohlcv(rows=55)
        result = engine.run_one_bar(data)
        assert result.skipped is True
        assert result.skip_reason == "kill_switch_active"


# ------------------------------------------------------------------ #
# CVaR フィルター
# ------------------------------------------------------------------ #

class TestCvarFilter:
    def test_cvar_stop_blocks_entry(self, tmp_path: Path) -> None:
        """CVaR が stop 水準のとき BUY が HOLD に変換される。"""
        # CVaR stop に引っかかるほど損失が大きい recent_returns を作る
        cvar_settings = CvarSettings(warn_pct=-0.001, half_pct=-0.002, stop_pct=-0.003)
        storage = Storage(db_path=tmp_path / "paper.db", data_dir=tmp_path / "data")
        storage.initialize()
        kill_switch = KillSwitch(storage)
        risk_manager = RiskManager(
            settings=CircuitBreakerSettings(),
            cvar_settings=cvar_settings,
            kill_switch=kill_switch,
        )
        state_store = PaperStateStore(storage)
        state_store.initialize()
        engine = PaperEngine(
            strategy=AlwaysBuyStrategy(),
            risk_manager=risk_manager,
            state_store=state_store,
            storage=storage,
            settings=PaperSettings(),
        )

        # recent_returns に大きな損失を注入（20本以上必要）
        import json
        from cryptbot.paper.state import PaperStateStore as PSS
        portfolio = engine._make_initial_portfolio()
        bad_returns = [-0.10] * 25  # CVaR = -10% → stop_pct=-0.003 より低い
        dummy_state = PSS.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=bad_returns,
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        state_store.save(dummy_state)

        data = _make_ohlcv(rows=55)
        result = engine.run_one_bar(data)
        # AlwaysBuy が生成しても CVaR でブロックされ HOLD になる
        assert result.signal is not None
        assert result.signal.direction == Direction.HOLD
        assert "cvar_block" in result.signal.reason
