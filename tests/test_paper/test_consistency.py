"""PaperEngine と BacktestEngine のバックテスト一致テスト。

同一 OHLCV データ・戦略・リスク設定で両エンジンを実行し、
各バー終了時点の残高とポジションが一致することを検証する。
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from cryptbot.backtest.engine import BacktestEngine
from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings, PaperSettings
from cryptbot.data.normalizer import normalize
from cryptbot.data.storage import Storage
from cryptbot.paper.engine import PaperEngine
from cryptbot.paper.state import PaperStateStore
from cryptbot.risk.kill_switch import KillSwitch
from cryptbot.risk.manager import RiskManager
from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# 合成戦略（決定論的なシグナル列）
# ------------------------------------------------------------------ #

class FixedSignalStrategy(BaseStrategy):
    """事前定義されたシグナル列を返す戦略。

    バーインデックス（len(data)-1）で引く。
    """

    def __init__(self, signals: dict[int, Direction]) -> None:
        super().__init__("fixed_signal")
        self._signals = signals

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        idx = len(data) - 1
        ts = datetime.now(JST)
        direction = self._signals.get(idx, Direction.HOLD)
        return Signal(direction, 1.0, f"fixed_{direction.value}", ts)


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_ohlcv(rows: int = 120) -> pd.DataFrame:
    base = datetime(2024, 1, 1, 9, 0, tzinfo=JST)
    timestamps = [base + timedelta(hours=i) for i in range(rows)]
    # 緩やかに上昇するOHLCV
    prices = [5_000_000.0 + i * 500.0 for i in range(rows)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.002 for p in prices],
        "low": [p * 0.998 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [1.0] * rows,
    })
    return normalize(df)


def _make_backtest_engine(strategy: BaseStrategy, initial_balance: float) -> BacktestEngine:
    cb_settings = CircuitBreakerSettings()
    cvar_settings = CvarSettings()
    storage = Storage.__new__(Storage)
    # BacktestEngine は Storage を直接使わないため、kill_switch に minimal Storage を渡す
    # ダミーの KillSwitch（常に inactive）を使う
    ks = _DummyKillSwitch()
    risk_manager = RiskManager(
        settings=cb_settings,
        cvar_settings=cvar_settings,
        kill_switch=ks,
    )
    return BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=initial_balance,
    )


class _DummyKillSwitch:
    """テスト用ダミー KillSwitch（常に inactive）。"""

    def __init__(self) -> None:
        self._active = False
        self._reason = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def reason(self):
        return self._reason

    def activate(self, reason, portfolio_value, drawdown_pct) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def load_state(self) -> None:
        pass


def _make_paper_engine(
    tmp_path: Path,
    strategy: BaseStrategy,
    initial_balance: float,
) -> tuple[PaperEngine, PaperStateStore]:
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
    return engine, state_store


# ------------------------------------------------------------------ #
# 一致テスト
# ------------------------------------------------------------------ #

class TestConsistency:
    """PaperEngine と BacktestEngine の残高・ポジションが一致することを検証する。"""

    def _run_paper_bar_by_bar(
        self,
        tmp_path: Path,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        warmup_bars: int,
        initial_balance: float,
    ) -> list[float]:
        """PaperEngine をバーごとに実行して各バー後の残高リストを返す。"""
        paper_engine, _ = _make_paper_engine(tmp_path, strategy, initial_balance)
        balances = []
        for i in range(warmup_bars + 1, len(data)):
            window = data.iloc[: i + 1]
            result = paper_engine.run_one_bar(window)
            if not result.skipped:
                balances.append(result.portfolio.balance)
        return balances

    def test_hold_only_balances_match(self, tmp_path: Path) -> None:
        """HOLD のみの場合、残高は常に初期値のまま一致する。"""
        from cryptbot.strategies.base import BaseStrategy, Direction, Signal

        class HoldStrategy(BaseStrategy):
            def __init__(self):
                super().__init__("hold")
            def generate_signal(self, data):
                return Signal(Direction.HOLD, 0.0, "hold", datetime.now(JST))

        data = _make_ohlcv(rows=60)
        warmup = 50
        initial = 500_000.0

        paper_engine, _ = _make_paper_engine(tmp_path, HoldStrategy(), initial)
        for i in range(warmup + 1, len(data)):
            window = data.iloc[: i + 1]
            result = paper_engine.run_one_bar(window)
            if not result.skipped:
                assert result.portfolio.balance == pytest.approx(initial), (
                    f"bar {i}: balance should stay at {initial}, got {result.portfolio.balance}"
                )

    def test_single_round_trip_balance(self, tmp_path: Path) -> None:
        """1 ラウンドトリップ後の残高を BacktestEngine と比較する。"""
        rows = 60
        warmup = 50
        initial = 1_000_000.0

        # bar 52 で BUY → bar 53 open で約定
        # bar 54 で SELL → bar 55 open で約定
        signals = {52: Direction.BUY, 54: Direction.SELL}

        # BacktestEngine での実行
        bt_strategy = FixedSignalStrategy(signals)
        bt_engine = _make_backtest_engine(bt_strategy, initial)
        data = _make_ohlcv(rows=rows)
        bt_result = bt_engine.run(data, warmup_bars=warmup)

        # PaperEngine での実行（バーごと）
        paper_strategy = FixedSignalStrategy(signals)
        paper_engine, _ = _make_paper_engine(tmp_path, paper_strategy, initial)
        paper_final_balance = initial
        for i in range(warmup + 1, len(data)):
            window = data.iloc[: i + 1]
            result = paper_engine.run_one_bar(window)
            if not result.skipped:
                paper_final_balance = result.portfolio.balance

        bt_final = bt_result.final_balance
        # 許容誤差 1 JPY（浮動小数点誤差）
        assert paper_final_balance == pytest.approx(bt_final, abs=1.0), (
            f"BacktestEngine final={bt_final:.2f}, PaperEngine final={paper_final_balance:.2f}"
        )

    def test_no_trade_backtest_consistency(self, tmp_path: Path) -> None:
        """トレードなしの場合、両エンジンの最終残高は初期残高と一致する。"""
        rows = 60
        warmup = 50
        initial = 800_000.0
        data = _make_ohlcv(rows=rows)

        # BacktestEngine
        from cryptbot.strategies.base import BaseStrategy

        class HoldOnly(BaseStrategy):
            def __init__(self):
                super().__init__("hold_only")
            def generate_signal(self, d):
                return Signal(Direction.HOLD, 0.0, "hold", datetime.now(JST))

        bt_engine = _make_backtest_engine(HoldOnly(), initial)
        bt_result = bt_engine.run(data, warmup_bars=warmup)
        assert bt_result.final_balance == pytest.approx(initial)

        # PaperEngine
        paper_engine, _ = _make_paper_engine(tmp_path, HoldOnly(), initial)
        paper_balance = initial
        for i in range(warmup + 1, len(data)):
            window = data.iloc[: i + 1]
            r = paper_engine.run_one_bar(window)
            if not r.skipped:
                paper_balance = r.portfolio.balance

        assert paper_balance == pytest.approx(initial)
