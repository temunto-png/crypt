"""BacktestEngine のテスト。

合成 OHLCV データ（300〜500バー）を使用。normalize() 済みデータを使う。
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from cryptbot.backtest.engine import BacktestEngine, BacktestResult, TradeRecord
from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings
from cryptbot.data.normalizer import normalize
from cryptbot.data.storage import Storage
from cryptbot.risk.kill_switch import KillSwitch
from cryptbot.risk.manager import RiskManager, PortfolioState
from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.strategies.buy_and_hold import BuyAndHoldStrategy
from cryptbot.strategies.regime import RegimeDetector, Regime
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー: 合成戦略
# ------------------------------------------------------------------ #

class AlwaysHoldStrategy(BaseStrategy):
    """常に HOLD を返す戦略（取引なし）。"""

    def __init__(self) -> None:
        super().__init__("always_hold")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        ts = datetime.now(JST)
        return self._hold("always hold", ts)


class BuyAtBarStrategy(BaseStrategy):
    """指定バーで BUY、その後指定バーで SELL する合成戦略。

    バーインデックスはウィンドウの最終要素のインデックス（len(data)-1）で判定する。
    look-ahead bias 修正後の動作:
      - signal on bar `buy_bar`   → execution on bar `buy_bar+1` open
      - signal on bar `sell_bar`  → execution on bar `sell_bar+1` open
    """

    def __init__(self, buy_bar: int, sell_bar: int) -> None:
        super().__init__("buy_at_bar")
        self._buy_bar = buy_bar
        self._sell_bar = sell_bar

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        idx = len(data) - 1  # 現在のバーインデックス（0始まり）
        ts = datetime.now(JST)
        if "timestamp" in data.columns:
            raw = data.iloc[-1]["timestamp"]
            if isinstance(raw, datetime):
                ts = raw if raw.tzinfo else raw.replace(tzinfo=JST)
            elif isinstance(raw, pd.Timestamp):
                dt = raw.to_pydatetime()
                ts = dt if dt.tzinfo else dt.replace(tzinfo=JST)

        if idx == self._buy_bar:
            return Signal(Direction.BUY, 1.0, "test buy", ts)
        elif idx == self._sell_bar:
            return Signal(Direction.SELL, 1.0, "test sell", ts)
        return self._hold("hold", ts)


class AlwaysBuyStrategy(BaseStrategy):
    """常に BUY を返す戦略（最初のエントリーのみが成立し、その後ポジション保有中はクローズなし）。"""

    def __init__(self) -> None:
        super().__init__("always_buy")

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        ts = datetime.now(JST)
        if "timestamp" in data.columns:
            raw = data.iloc[-1]["timestamp"]
            if isinstance(raw, datetime):
                ts = raw if raw.tzinfo else raw.replace(tzinfo=JST)
        return Signal(Direction.BUY, 1.0, "always buy", ts)


class FixedPriceStrategy(BaseStrategy):
    """固定シグナルシーケンスを返す合成戦略（価格検証用）。"""

    def __init__(self, signals: list[Direction]) -> None:
        super().__init__("fixed_price")
        self._signals = signals
        self._idx = 0

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        ts = datetime.now(JST)
        if self._idx < len(self._signals):
            d = self._signals[self._idx]
            self._idx += 1
        else:
            d = Direction.HOLD
        return Signal(d, 1.0, "fixed", ts)

    def reset(self) -> None:
        self._idx = 0


# ------------------------------------------------------------------ #
# ヘルパー: 合成データ生成
# ------------------------------------------------------------------ #

def _make_ohlcv(n: int = 400, start: datetime | None = None) -> pd.DataFrame:
    """合成 OHLCV DataFrame を生成する（normalize() 済み）。"""
    if start is None:
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=JST)

    timestamps = [start + timedelta(hours=i) for i in range(n)]
    rng = np.random.default_rng(42)

    close = 5_000_000.0 + np.cumsum(rng.normal(0, 10_000, n))
    close = np.clip(close, 1_000_000.0, 20_000_000.0)
    high = close + rng.uniform(0, 5_000, n)
    low = close - rng.uniform(0, 5_000, n)
    low = np.clip(low, 1.0, None)
    open_ = close + rng.normal(0, 2_000, n)
    open_ = np.clip(open_, 1.0, None)
    volume = rng.uniform(0.1, 10.0, n)

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    return normalize(df)


def _make_flat_ohlcv(n: int, price: float, start: datetime | None = None) -> pd.DataFrame:
    """全バー同一価格の OHLCV DataFrame を生成する（会計検証用）。"""
    if start is None:
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=JST)
    timestamps = [start + timedelta(hours=i) for i in range(n)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": [price] * n,
        "high": [price] * n,
        "low": [price] * n,
        "close": [price] * n,
        "volume": [1.0] * n,
    })
    return normalize(df)


def _make_walk_forward_data(n: int = 500, start: datetime | None = None) -> pd.DataFrame:
    """Walk-forward 用の長期合成データ（約12ヶ月分）。"""
    if start is None:
        start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=JST)
    return _make_ohlcv(n=n, start=start)


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )
    s.initialize()
    return s


@pytest.fixture()
def kill_switch(storage: Storage) -> KillSwitch:
    return KillSwitch(storage)


@pytest.fixture()
def cb_settings() -> CircuitBreakerSettings:
    return CircuitBreakerSettings(
        max_trade_loss_pct=0.02,
        daily_loss_pct=0.50,    # 緩い閾値（テスト用）
        weekly_loss_pct=0.80,
        monthly_loss_pct=0.90,
        max_drawdown_pct=0.50,
        max_consecutive_losses=100,
    )


@pytest.fixture()
def cvar_settings() -> CvarSettings:
    return CvarSettings(
        warn_pct=-0.03,
        half_pct=-0.04,
        stop_pct=-0.05,
    )


@pytest.fixture()
def risk_manager(
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
    kill_switch: KillSwitch,
) -> RiskManager:
    return RiskManager(
        settings=cb_settings,
        cvar_settings=cvar_settings,
        kill_switch=kill_switch,
    )


@pytest.fixture()
def ohlcv_data() -> pd.DataFrame:
    return _make_ohlcv(n=400)


# ------------------------------------------------------------------ #
# テストケース 1: run() が BacktestResult を返すこと
# ------------------------------------------------------------------ #

def test_run_returns_backtest_result(risk_manager: RiskManager, ohlcv_data: pd.DataFrame) -> None:
    """run() が BacktestResult インスタンスを返すこと。"""
    strategy = AlwaysHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=50)

    assert isinstance(result, BacktestResult)
    assert result.strategy_name == "always_hold"
    assert isinstance(result.trades, list)
    assert isinstance(result.equity_curve, pd.Series)
    assert isinstance(result.portfolio_states, list)
    assert result.initial_balance == 100_000.0


# ------------------------------------------------------------------ #
# テストケース 2: equity_curve の長さが有効バー数と一致すること
# ------------------------------------------------------------------ #

def test_equity_curve_length(risk_manager: RiskManager, ohlcv_data: pd.DataFrame) -> None:
    """equity_curve の長さが data の有効バー数（warmup_bars 以降）と一致すること。"""
    warmup = 50
    strategy = AlwaysHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    expected_len = len(ohlcv_data) - warmup
    assert len(result.equity_curve) == expected_len, (
        f"equity_curve の長さが期待値と異なる: {len(result.equity_curve)} != {expected_len}"
    )


# ------------------------------------------------------------------ #
# テストケース 3: ウォームアップ期間がスキップされること（最初の50バーで取引なし）
# ------------------------------------------------------------------ #

def test_warmup_bars_skipped(risk_manager: RiskManager, ohlcv_data: pd.DataFrame) -> None:
    """warmup_bars=50 のとき、最初の50バーでは取引が行われないこと。"""
    warmup = 50
    strategy = BuyAtBarStrategy(buy_bar=50, sell_bar=51)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    assert len(result.equity_curve) == len(ohlcv_data) - warmup

    # Always-hold で取引がないことを確認
    hold_strategy = AlwaysHoldStrategy()
    hold_engine = BacktestEngine(
        strategy=hold_strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    hold_result = hold_engine.run(ohlcv_data, warmup_bars=warmup)
    assert len(hold_result.trades) == 0


# ------------------------------------------------------------------ #
# テストケース 4: 終了時にオープンポジションが強制クローズされること
# ------------------------------------------------------------------ #

def test_open_position_force_closed_at_end(risk_manager: RiskManager, ohlcv_data: pd.DataFrame) -> None:
    """終了バーでオープンポジションが強制クローズされること。"""
    warmup = 50
    buy_bar = 60
    sell_bar = 99999
    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    assert len(result.trades) >= 1

    last_portfolio = result.portfolio_states[-1]
    assert last_portfolio.position_size == 0.0


# ------------------------------------------------------------------ #
# テストケース 5: 手数料・スリッページが正しく計算されること
# ------------------------------------------------------------------ #

def test_fee_and_slippage_calculation(risk_manager: RiskManager, ohlcv_data: pd.DataFrame) -> None:
    """手数料・スリッページが仕様通りに計算されること。"""
    warmup = 50
    buy_bar = 60
    sell_bar = 70
    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    assert len(result.trades) >= 1

    trade = result.trades[0]
    size = trade.size
    entry_price = trade.entry_price
    exit_price = trade.exit_price

    expected_fee_entry = size * entry_price * BacktestEngine.TAKER_FEE_PCT
    expected_fee_exit = size * exit_price * BacktestEngine.TAKER_FEE_PCT
    expected_total_fee = expected_fee_entry + expected_fee_exit

    expected_slip_entry = size * entry_price * BacktestEngine.SLIPPAGE_PCT
    expected_slip_exit = size * exit_price * BacktestEngine.SLIPPAGE_PCT
    expected_total_slippage = expected_slip_entry + expected_slip_exit

    assert abs(trade.fee - expected_total_fee) < 1.0
    assert abs(trade.slippage - expected_total_slippage) < 1.0

    entry_cost = size * entry_price + size * entry_price * (BacktestEngine.TAKER_FEE_PCT + BacktestEngine.SLIPPAGE_PCT)
    exit_proceeds = size * exit_price - size * exit_price * (BacktestEngine.TAKER_FEE_PCT + BacktestEngine.SLIPPAGE_PCT)
    expected_pnl = exit_proceeds - entry_cost

    assert abs(trade.pnl - expected_pnl) < 1.0

    expected_pnl_pct = expected_pnl / entry_cost * 100.0
    assert abs(trade.pnl_pct - expected_pnl_pct) < 0.01

    # exit_proceeds フィールドの検証
    assert abs(trade.exit_proceeds - exit_proceeds) < 1.0


# ------------------------------------------------------------------ #
# テストケース 6: run_walk_forward() が少なくとも1つの BacktestResult を返すこと
# ------------------------------------------------------------------ #

def test_run_walk_forward_returns_results(
    risk_manager: RiskManager,
) -> None:
    """run_walk_forward() が少なくとも1つの BacktestResult を返すこと。"""
    start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=JST)
    data = _make_walk_forward_data(n=2200, start=start)

    strategy = AlwaysHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    results = engine.run_walk_forward(
        data,
        train_months=2,
        validation_months=1,
        step_months=1,
        warmup_bars=20,
    )

    assert len(results) >= 1
    for r in results:
        assert isinstance(r, BacktestResult)


# ------------------------------------------------------------------ #
# テストケース 7: Walk-forward の各ウィンドウが検証期間のみの結果を返すこと
# ------------------------------------------------------------------ #

def test_walk_forward_validation_windows(
    risk_manager: RiskManager,
) -> None:
    """Walk-forward の各ウィンドウが適切な期間の BacktestResult を返すこと。"""
    start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=JST)
    n_bars = 1000
    data = _make_ohlcv(n=n_bars, start=start)

    strategy = AlwaysHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=100_000.0,
    )
    results = engine.run_walk_forward(
        data,
        train_months=1,
        validation_months=1,
        step_months=1,
        warmup_bars=20,
    )

    assert len(results) >= 1
    for r in results:
        assert isinstance(r, BacktestResult)
        assert isinstance(r.equity_curve, pd.Series)
        assert r.strategy_name == "always_hold"


# ------------------------------------------------------------------ #
# テストケース 8: kill switch が active の場合は空結果が返ること
# ------------------------------------------------------------------ #

def test_kill_switch_active_returns_empty(
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
    ohlcv_data: pd.DataFrame,
    tmp_path: Path,
) -> None:
    """kill switch が active の場合、run() は空の BacktestResult を返すこと。"""
    s = Storage(
        db_path=tmp_path / "ks_test.db",
        data_dir=tmp_path / "ks_data",
    )
    s.initialize()
    ks = KillSwitch(s)
    from cryptbot.risk.kill_switch import KillSwitchReason
    ks.activate(
        reason=KillSwitchReason.MAX_DRAWDOWN,
        portfolio_value=100_000.0,
        drawdown_pct=0.20,
    )

    rm = RiskManager(
        settings=cb_settings,
        cvar_settings=cvar_settings,
        kill_switch=ks,
    )
    strategy = AlwaysBuyStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=rm,
        initial_balance=100_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=50)

    assert len(result.trades) == 0
    assert len(result.equity_curve) == 0


# ------------------------------------------------------------------ #
# P0-02: 残高会計の正確性（同値往復トレード）
# ------------------------------------------------------------------ #

def test_balance_accounting_same_price_roundtrip(
    risk_manager: RiskManager,
    tmp_path: Path,
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
) -> None:
    """同値往復トレードの final_balance が initial - 往復コストになること。

    P0-02 受け入れ条件:
    final_balance = initial_balance - entry_fee - entry_slippage - exit_fee - exit_slippage
    """
    price = 5_000_000.0
    n = 110
    warmup = 50
    # warmup後 buy_bar=50（ウォームアップ終了バー）でBUY、sell_bar=60でSELL
    # 実行はそれぞれ1バー後: bar51 open, bar61 open
    buy_bar = 50
    sell_bar = 60
    data = _make_flat_ohlcv(n=n, price=price)

    ks = KillSwitch(Storage(db_path=tmp_path / "db.db", data_dir=tmp_path / "data"))
    ks._storage.initialize()
    rm = RiskManager(cb_settings, cvar_settings, ks)

    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=rm,
        initial_balance=100_000.0,
    )
    result = engine.run(data, warmup_bars=warmup)

    assert len(result.trades) >= 1, "トレードが発生していない"

    trade = result.trades[0]
    size = trade.size

    # 同値往復なので entry_price ≈ exit_price ≈ price
    entry_cost = size * trade.entry_price * (1 + BacktestEngine.TAKER_FEE_PCT + BacktestEngine.SLIPPAGE_PCT)
    exit_proceeds = size * trade.exit_price * (1 - BacktestEngine.TAKER_FEE_PCT - BacktestEngine.SLIPPAGE_PCT)
    expected_final = 100_000.0 - entry_cost + exit_proceeds  # = initial + pnl

    assert abs(result.final_balance - expected_final) < 1.0, (
        f"残高が不正: {result.final_balance:.2f} != {expected_final:.2f}\n"
        f"  (entry_cost={entry_cost:.2f}, exit_proceeds={exit_proceeds:.2f})"
    )

    # 同値往復なので pnl が負（往復手数料分の損失）
    assert trade.pnl < 0, "同値往復でもコスト分の損失があるはず"

    # final_balance が initial より小さいこと
    assert result.final_balance < 100_000.0


# ------------------------------------------------------------------ #
# P0-03: look-ahead bias – バー i のシグナルがバー i+1 で約定すること
# ------------------------------------------------------------------ #

def test_look_ahead_buy_signal_executes_next_bar(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
) -> None:
    """BUY シグナルをバー buy_bar で生成したとき、エントリー価格がバー buy_bar+1 の open であること。"""
    warmup = 50
    buy_bar = 60
    sell_bar = 99999
    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    assert len(result.trades) >= 1, "トレードが発生していない"

    trade = result.trades[0]
    expected_entry_price = float(ohlcv_data.iloc[buy_bar + 1]["open"])
    assert abs(trade.entry_price - expected_entry_price) < 0.01, (
        f"エントリー価格が look-ahead: {trade.entry_price} != bar {buy_bar+1} open {expected_entry_price}"
    )


def test_look_ahead_sell_signal_executes_next_bar(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
) -> None:
    """SELL シグナルをバー sell_bar で生成したとき、クローズ価格がバー sell_bar+1 の open であること。"""
    warmup = 50
    buy_bar = 60
    sell_bar = 70
    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    assert len(result.trades) >= 1

    trade = result.trades[0]
    expected_exit_price = float(ohlcv_data.iloc[sell_bar + 1]["open"])
    assert abs(trade.exit_price - expected_exit_price) < 0.01, (
        f"クローズ価格が look-ahead: {trade.exit_price} != bar {sell_bar+1} open {expected_exit_price}"
    )


def test_last_bar_buy_signal_does_not_execute(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
) -> None:
    """最終バーで BUY シグナルが出ても新規約定しないこと（次バーが存在しないため）。"""
    warmup = 50
    n = len(ohlcv_data)
    last_bar = n - 1
    buy_bar = last_bar
    sell_bar = 99999
    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    # 最終バーの BUY は約定されない（次バーなし）
    assert len(result.trades) == 0
    assert result.final_balance == pytest.approx(500_000.0)


# ------------------------------------------------------------------ #
# P1-04: strategy reset – walk-forward でインスタンスを再利用しても独立すること
# ------------------------------------------------------------------ #

def test_strategy_reset_run_twice_gets_buy_both_times(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
) -> None:
    """同じ BuyAndHoldStrategy で run() を2回実行しても、各 run で初回 BUY が出ること。"""
    strategy = BuyAndHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )

    result1 = engine.run(ohlcv_data, warmup_bars=50)
    result2 = engine.run(ohlcv_data, warmup_bars=50)

    # 両方の run でトレードが発生していること
    assert len(result1.trades) >= 1, "1回目の run でトレードがない"
    assert len(result2.trades) >= 1, "2回目の run でトレードがない（strategy.reset() が効いていない）"


def test_walk_forward_strategy_state_independent(
    risk_manager: RiskManager,
) -> None:
    """walk-forward の各ウィンドウが training の strategy state に影響されないこと。"""
    start = datetime(2023, 1, 1, 0, 0, 0, tzinfo=JST)
    data = _make_ohlcv(n=2200, start=start)

    strategy = BuyAndHoldStrategy()
    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        initial_balance=500_000.0,
    )
    results = engine.run_walk_forward(
        data,
        train_months=2,
        validation_months=1,
        step_months=1,
        warmup_bars=20,
    )

    # すべての検証ウィンドウで少なくとも1トレードがあること
    # （BuyAndHold がリセットされていれば、各ウィンドウの先頭で BUY が出る）
    for i, r in enumerate(results):
        assert len(r.trades) >= 1, f"ウィンドウ {i} でトレードがない（strategy リセット失敗の可能性）"


# ------------------------------------------------------------------ #
# P1-05: Regime – HIGH_VOL でポジションがクローズされること
# ------------------------------------------------------------------ #

def test_regime_high_vol_closes_position(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
    tmp_path: Path,
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
) -> None:
    """RegimeDetector が HIGH_VOL を返すとき、保有ポジションがクローズされること。"""
    from unittest.mock import MagicMock

    warmup = 50
    buy_bar = 52
    sell_bar = 99999

    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=sell_bar)

    # HIGH_VOL を強制的に返す mock regime detector
    mock_regime = MagicMock(spec=RegimeDetector)
    mock_regime.detect.return_value = Regime.HIGH_VOL

    ks = KillSwitch(Storage(db_path=tmp_path / "db.db", data_dir=tmp_path / "data"))
    ks._storage.initialize()
    rm = RiskManager(cb_settings, cvar_settings, ks)

    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=rm,
        initial_balance=500_000.0,
        regime_detector=mock_regime,
    )
    result = engine.run(ohlcv_data, warmup_bars=warmup)

    # ポジションがクローズされている（signal BUY → regime で SELL オーバーライド → その翌バーでクローズ）
    assert result.final_balance == pytest.approx(result.portfolio_states[-1].balance, abs=1.0)
    assert result.portfolio_states[-1].position_size == 0.0


def test_regime_trend_down_prevents_entry(
    risk_manager: RiskManager,
    ohlcv_data: pd.DataFrame,
    tmp_path: Path,
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
) -> None:
    """TREND_DOWN レジームではエントリーがブロックされること。"""
    from unittest.mock import MagicMock

    strategy = AlwaysBuyStrategy()

    mock_regime = MagicMock(spec=RegimeDetector)
    mock_regime.detect.return_value = Regime.TREND_DOWN

    ks = KillSwitch(Storage(db_path=tmp_path / "db.db", data_dir=tmp_path / "data"))
    ks._storage.initialize()
    rm = RiskManager(cb_settings, cvar_settings, ks)

    engine = BacktestEngine(
        strategy=strategy,
        risk_manager=rm,
        initial_balance=500_000.0,
        regime_detector=mock_regime,
    )
    result = engine.run(ohlcv_data, warmup_bars=50)

    # TREND_DOWN でエントリーがブロックされるため取引なし
    assert len(result.trades) == 0


# ------------------------------------------------------------------ #
# P1-01: per-bar risk – max_trade_loss で翌バー強制クローズ
# ------------------------------------------------------------------ #

def test_max_trade_loss_triggers_close(
    tmp_path: Path,
) -> None:
    """価格急落時に max_trade_loss_pct で翌バー強制クローズされること。"""
    # max_trade_loss_pct = 0.005（0.5%）を使用
    cb = CircuitBreakerSettings(
        max_trade_loss_pct=0.005,
        daily_loss_pct=0.50,
        weekly_loss_pct=0.80,
        monthly_loss_pct=0.90,
        max_drawdown_pct=0.50,
        max_consecutive_losses=100,
    )
    cvar = CvarSettings(warn_pct=-0.03, half_pct=-0.04, stop_pct=-0.05)
    ks = KillSwitch(Storage(db_path=tmp_path / "db.db", data_dir=tmp_path / "data"))
    ks._storage.initialize()
    rm = RiskManager(cb, cvar, ks)

    # エントリー後に大幅下落するデータを生成
    price_high = 5_000_000.0
    price_low = 4_900_000.0  # -2% 下落

    n = 110
    warmup = 50
    buy_bar = 52  # signal on 52, execute on bar 53

    timestamps = [datetime(2024, 1, 1, 0, 0, 0, tzinfo=JST) + timedelta(hours=i) for i in range(n)]
    prices = [price_high] * n
    # bar 54 以降を急落させる（エントリー後）
    for i in range(54, n):
        prices[i] = price_low

    from cryptbot.data.normalizer import normalize
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * n,
    })
    data = normalize(df)

    strategy = BuyAtBarStrategy(buy_bar=buy_bar, sell_bar=99999)
    engine = BacktestEngine(strategy=strategy, risk_manager=rm, initial_balance=500_000.0)
    result = engine.run(data, warmup_bars=warmup)

    # max_trade_loss による強制クローズで 1 トレードが発生すること
    assert len(result.trades) >= 1
    assert result.portfolio_states[-1].position_size == 0.0
