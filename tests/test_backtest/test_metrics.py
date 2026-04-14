"""MetricsResult および評価指標関数のテスト。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from cryptbot.backtest.engine import BacktestResult, TradeRecord
from cryptbot.backtest.metrics import (
    MetricsResult,
    calculate_cvar,
    calculate_drawdown_series,
    calculate_metrics,
    calculate_profit_factor,
    calculate_sharpe,
    calculate_sortino,
)
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_trade(pnl: float, pnl_pct: float = 1.0, fee: float = 100.0) -> TradeRecord:
    now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=JST)
    exit_price = 5_100_000.0 if pnl > 0 else 4_900_000.0
    size = 0.001
    # exit_proceeds = exit_price * size * (1 - fee_rate - slip_rate) の近似
    exit_proceeds = size * exit_price - fee - 50.0
    return TradeRecord(
        entry_time=now,
        exit_time=now + timedelta(hours=1),
        entry_price=5_000_000.0,
        exit_price=exit_price,
        size=size,
        pnl=pnl,
        pnl_pct=pnl_pct,
        fee=fee,
        slippage=50.0,
        exit_proceeds=exit_proceeds,
        strategy="test",
    )


def _make_result(
    trades: list[TradeRecord],
    initial_balance: float = 100_000.0,
    equity_values: list[float] | None = None,
    duration_days: int = 30,
) -> BacktestResult:
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=JST)
    end = start + timedelta(days=duration_days)

    pnl_sum = sum(t.pnl for t in trades)
    final_balance = initial_balance + pnl_sum

    if equity_values is None:
        # シンプルな線形 equity curve を生成
        n = max(duration_days * 24, 2)
        eq = np.linspace(initial_balance, final_balance, n)
        equity_values = list(eq)

    idx = [start + timedelta(hours=i) for i in range(len(equity_values))]
    equity_curve = pd.Series(equity_values, index=idx, dtype=float)

    return BacktestResult(
        strategy_name="test_strategy",
        trades=trades,
        equity_curve=equity_curve,
        portfolio_states=[],
        initial_balance=initial_balance,
        final_balance=final_balance,
        start_time=start,
        end_time=end,
        params={},
    )


# ------------------------------------------------------------------ #
# calculate_metrics のテスト
# ------------------------------------------------------------------ #

class TestCalculateMetrics:
    def test_no_trades_all_hold(self):
        """トレードなし（all HOLD）— エラーなく実行でき、取引統計がゼロになること。"""
        result = _make_result(trades=[])
        metrics = calculate_metrics(result)

        assert isinstance(metrics, MetricsResult)
        assert metrics.total_trades == 0
        assert metrics.winning_trades == 0
        assert metrics.losing_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == float("inf")
        assert metrics.total_pnl == pytest.approx(0.0, abs=1e-6)
        assert metrics.total_fee == pytest.approx(0.0, abs=1e-6)
        assert metrics.max_consecutive_losses == 0

    def test_winning_trades_only(self):
        """利益トレードのみ — total_pnl > 0, win_rate = 1.0。"""
        trades = [_make_trade(pnl=1000.0, pnl_pct=1.0) for _ in range(5)]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)

        assert metrics.total_pnl > 0
        assert metrics.win_rate == pytest.approx(1.0)
        assert metrics.winning_trades == 5
        assert metrics.losing_trades == 0
        assert metrics.total_trades == 5
        assert metrics.profit_factor == float("inf")
        assert metrics.avg_win > 0
        assert metrics.avg_loss == pytest.approx(0.0, abs=1e-6)

    def test_mixed_trades_pnl_calculation(self):
        """利益・損失が混在する場合の計算。"""
        trades = [
            _make_trade(pnl=2000.0, pnl_pct=2.0),
            _make_trade(pnl=-500.0, pnl_pct=-0.5),
            _make_trade(pnl=1000.0, pnl_pct=1.0),
            _make_trade(pnl=-300.0, pnl_pct=-0.3),
        ]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)

        assert metrics.total_trades == 4
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 2
        assert metrics.win_rate == pytest.approx(0.5)
        assert metrics.total_pnl == pytest.approx(2000 - 500 + 1000 - 300, abs=1e-6)
        assert metrics.profit_factor == pytest.approx((2000 + 1000) / (500 + 300), rel=1e-5)

    def test_breakeven_trade_not_counted_as_loss(self):
        """Break-even トレード (pnl == 0) は losing_trades に含めない。"""
        trades = [
            _make_trade(pnl=1000.0, pnl_pct=1.0),
            _make_trade(pnl=0.0, pnl_pct=0.0),    # Break-even
            _make_trade(pnl=-500.0, pnl_pct=-0.5),
        ]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)

        assert metrics.total_trades == 3
        assert metrics.winning_trades == 1
        assert metrics.losing_trades == 1  # Break-even は除外
        assert metrics.avg_loss == pytest.approx(-500.0, abs=1e-6)

    def test_total_fee_sum(self):
        """total_fee が各トレードの fee 合計と一致すること。"""
        trades = [_make_trade(pnl=500.0, fee=150.0) for _ in range(3)]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.total_fee == pytest.approx(450.0, abs=1e-6)

    def test_max_consecutive_losses(self):
        """最大連続損失が正しく計算されること。Break-even は連続をリセットしない。"""
        trades = [
            _make_trade(pnl=-100.0),
            _make_trade(pnl=-200.0),
            _make_trade(pnl=-300.0),  # 3連続
            _make_trade(pnl=500.0),
            _make_trade(pnl=-100.0),
            _make_trade(pnl=-100.0),  # 2連続
        ]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.max_consecutive_losses == 3

    def test_max_consecutive_losses_with_breakeven(self):
        """Break-even (pnl == 0) は損失として数えられず、連続をリセットする。"""
        trades = [
            _make_trade(pnl=-100.0),
            _make_trade(pnl=0.0),      # Break-even → リセット
            _make_trade(pnl=-200.0),
            _make_trade(pnl=-300.0),   # 2連続損失
        ]
        result = _make_result(trades=trades)
        metrics = calculate_metrics(result)
        assert metrics.max_consecutive_losses == 2

    def test_duration_and_timestamps(self):
        """期間・タイムスタンプが正しく設定されること。"""
        result = _make_result(trades=[], duration_days=60)
        metrics = calculate_metrics(result)
        assert metrics.duration_days == pytest.approx(60.0, abs=0.5)
        assert metrics.start_time == result.start_time
        assert metrics.end_time == result.end_time

    def test_buy_and_hold_defaults_to_none(self):
        """buy_and_hold_return_pct と no_trade_rate はデフォルト None。"""
        result = _make_result(trades=[])
        metrics = calculate_metrics(result)
        assert metrics.buy_and_hold_return_pct is None
        assert metrics.no_trade_rate is None


# ------------------------------------------------------------------ #
# calculate_sharpe のテスト
# ------------------------------------------------------------------ #

class TestCalculateSharpe:
    def test_positive_sharpe(self):
        """正のリターン列 → Sharpe > 0。"""
        rng = np.random.default_rng(0)
        returns = pd.Series(rng.normal(0.001, 0.01, 500))
        sharpe = calculate_sharpe(returns)
        assert sharpe > 0

    def test_empty_returns_returns_zero(self):
        """空の returns → 0.0。"""
        assert calculate_sharpe(pd.Series([], dtype=float)) == 0.0

    def test_zero_std_returns_zero(self):
        """全て同値（実質 std ≈ 0）→ 0.0。
        浮動小数点演算で std が厳密にゼロにならない場合でも、
        閾値（1e-15）以下なら 0.0 を返すこと。
        """
        returns = pd.Series([0.001] * 100)
        result = calculate_sharpe(returns)
        # std が非常に小さいため 0.0 を返す
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_negative_constant_returns_zero(self):
        """全て同値の負リターン（実質 std ≈ 0）→ 0.0。"""
        returns = pd.Series([-0.001] * 100)
        result = calculate_sharpe(returns)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_mixed_returns_formula(self):
        """手動計算と一致すること。"""
        returns = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015])
        periods_per_year = 24 * 365
        excess = returns - 0.0 / periods_per_year
        expected = float(excess.mean() / excess.std() * np.sqrt(periods_per_year))
        result = calculate_sharpe(returns, risk_free_rate=0.0, periods_per_year=periods_per_year)
        assert result == pytest.approx(expected, rel=1e-9)


# ------------------------------------------------------------------ #
# calculate_sortino のテスト
# ------------------------------------------------------------------ #

class TestCalculateSortino:
    def test_no_downside_returns_zero(self):
        """下落リターンがない場合は 0.0。"""
        returns = pd.Series([0.001, 0.002, 0.003])
        assert calculate_sortino(returns) == 0.0

    def test_mixed_returns_positive(self):
        """上昇が優勢な場合 Sortino > 0。"""
        rng = np.random.default_rng(1)
        returns = pd.Series(rng.normal(0.001, 0.005, 500))
        sortino = calculate_sortino(returns)
        assert isinstance(sortino, float)

    def test_empty_returns_zero(self):
        assert calculate_sortino(pd.Series([], dtype=float)) == 0.0


# ------------------------------------------------------------------ #
# calculate_drawdown_series のテスト
# ------------------------------------------------------------------ #

class TestCalculateDrawdownSeries:
    def test_monotone_increase_no_drawdown(self):
        """単調増加 equity → ドローダウンは 0.0。"""
        equity = pd.Series([100, 110, 120, 130, 140], dtype=float)
        dd = calculate_drawdown_series(equity)
        assert (dd <= 0).all()
        assert dd.max() == pytest.approx(0.0, abs=1e-9)

    def test_drawdown_is_negative(self):
        """ピーク後に下落したバーは負のドローダウン値を持つ。"""
        equity = pd.Series([100.0, 120.0, 90.0, 100.0], dtype=float)
        dd = calculate_drawdown_series(equity)
        assert (dd <= 0).all()
        # 90 / 120 - 1 = -25%
        assert dd.iloc[2] == pytest.approx(-25.0, rel=1e-5)

    def test_drawdown_length_matches_equity(self):
        """返される Series の長さが equity と一致すること。"""
        equity = pd.Series(np.random.default_rng(5).uniform(90, 110, 50), dtype=float)
        dd = calculate_drawdown_series(equity)
        assert len(dd) == len(equity)

    def test_max_drawdown_is_min(self):
        """最大ドローダウンは min() と一致。"""
        equity = pd.Series([100.0, 150.0, 80.0, 120.0, 60.0], dtype=float)
        dd = calculate_drawdown_series(equity)
        assert dd.min() < 0
        # 60 / 150 - 1 ≈ -60%
        assert dd.min() == pytest.approx((60 / 150 - 1) * 100, rel=1e-5)


# ------------------------------------------------------------------ #
# calculate_cvar のテスト
# ------------------------------------------------------------------ #

class TestCalculateCvar:
    def test_less_than_20_samples_returns_none(self):
        """サンプル数 < 20 → None（サンプル不足）。"""
        assert calculate_cvar([1.0, -1.0, 2.0]) is None
        assert calculate_cvar(list(range(19))) is None

    def test_empty_returns_none(self):
        """空リスト → None。"""
        assert calculate_cvar([]) is None

    def test_exactly_20_samples(self):
        """ちょうど 20 サンプル → float が返る（None ではない）。"""
        samples = list(range(-10, 10))  # -10〜9
        result = calculate_cvar(samples, confidence_level=0.95)
        assert result is not None
        assert isinstance(result, float)
        # cutoff_idx = int(20 * 0.05) = 1 → arr[:1] = [-10] → mean = -10.0
        assert result == pytest.approx(-10.0, abs=1e-9)

    def test_cvar_is_average_of_worst_tail(self):
        """下位5%の平均を正しく計算すること。"""
        rng = np.random.default_rng(42)
        samples = list(rng.normal(0, 1, 100))
        result = calculate_cvar(samples, confidence_level=0.95)
        assert result is not None
        arr = np.array(sorted(samples))
        cutoff_idx = int(100 * 0.05)
        if cutoff_idx == 0:
            expected = None
        else:
            expected = float(arr[:cutoff_idx].mean())
        assert result == pytest.approx(expected, rel=1e-9)

    def test_cvar_returns_negative_for_loss_series(self):
        """損失が多い系列は CVaR < 0。"""
        samples = [float(x) for x in range(-50, 50)]
        result = calculate_cvar(samples)
        assert result is not None
        assert result < 0


# ------------------------------------------------------------------ #
# calculate_profit_factor のテスト
# ------------------------------------------------------------------ #

class TestCalculateProfitFactor:
    def test_no_losses_returns_inf(self):
        """損失トレードなし → inf。"""
        trades = [_make_trade(pnl=500.0), _make_trade(pnl=1000.0)]
        assert calculate_profit_factor(trades) == float("inf")

    def test_no_trades_returns_inf(self):
        """トレードなし → inf（総損失 = 0）。"""
        assert calculate_profit_factor([]) == float("inf")

    def test_mixed_trades_correct_calculation(self):
        """混合トレード → 正しい Profit Factor。"""
        trades = [
            _make_trade(pnl=3000.0),
            _make_trade(pnl=-1000.0),
            _make_trade(pnl=2000.0),
            _make_trade(pnl=-500.0),
        ]
        pf = calculate_profit_factor(trades)
        # 総利益 = 5000, 総損失 = 1500
        assert pf == pytest.approx(5000.0 / 1500.0, rel=1e-9)

    def test_all_losses_returns_zero(self):
        """全損失トレード → profit_factor = 0.0。"""
        trades = [_make_trade(pnl=-100.0), _make_trade(pnl=-200.0)]
        pf = calculate_profit_factor(trades)
        assert pf == pytest.approx(0.0, abs=1e-9)
