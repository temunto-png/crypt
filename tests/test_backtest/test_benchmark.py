"""backtest/benchmark モジュールのテスト。"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from cryptbot.backtest.benchmark import (
    BootstrapCI,
    BuyAndHoldResult,
    LiveReadinessResult,
    RandomEntryResult,
    _optimal_block_size,
    calculate_bootstrap_ci,
    calculate_buy_and_hold,
    calculate_random_entry_benchmark,
    check_live_readiness,
)
from cryptbot.backtest.metrics import MetricsResult
from cryptbot.utils.time_utils import JST


# --------------------------------------------------------------------------- #
# ヘルパー
# --------------------------------------------------------------------------- #

def _make_metrics(
    total_trades: int = 35,
    total_pnl: float = 50_000.0,
    max_drawdown_pct: float = -8.0,
    profit_factor: float = 1.5,
    sharpe_ratio: float = 0.8,
    cvar_95: float | None = -2.0,
) -> MetricsResult:
    start = datetime(2024, 1, 1, tzinfo=JST)
    end = datetime(2024, 6, 30, tzinfo=JST)
    return MetricsResult(
        total_pnl=total_pnl,
        total_return_pct=total_pnl / 1_000_000 * 100,
        annual_return_pct=10.0,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=1.2,
        calmar_ratio=1.0,
        profit_factor=profit_factor,
        win_rate=0.55,
        avg_win=5_000.0,
        avg_loss=-3_000.0,
        cvar_95=cvar_95,
        total_trades=total_trades,
        winning_trades=int(total_trades * 0.55),
        losing_trades=int(total_trades * 0.45),
        max_consecutive_losses=3,
        total_fee=5_000.0,
        start_time=start,
        end_time=end,
        duration_days=181.0,
    )


def _make_ohlcv(n: int = 200, trend_pct: float = 10.0) -> pd.DataFrame:
    """テスト用 OHLCV DataFrame を生成する。"""
    start_price = 5_000_000.0
    end_price = start_price * (1 + trend_pct / 100)
    closes = np.linspace(start_price, end_price, n)
    timestamps = pd.date_range(
        "2024-01-01 09:00:00", periods=n, freq="1h", tz="Asia/Tokyo"
    )
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": closes * 0.999,
        "high": closes * 1.002,
        "low": closes * 0.997,
        "close": closes,
        "volume": np.ones(n) * 1.5,
    })


# --------------------------------------------------------------------------- #
# _optimal_block_size
# --------------------------------------------------------------------------- #

class TestOptimalBlockSize:
    def test_small_n(self) -> None:
        """n=8 → block_size=2 (round(8^(1/3))=2)。"""
        assert _optimal_block_size(8) == 2

    def test_n_1(self) -> None:
        """n=1 → block_size=1（最小値）。"""
        assert _optimal_block_size(1) == 1

    def test_large_n(self) -> None:
        """n=1000 → block_size=10 (round(1000^(1/3))=10)。"""
        assert _optimal_block_size(1000) == 10


# --------------------------------------------------------------------------- #
# calculate_bootstrap_ci
# --------------------------------------------------------------------------- #

class TestCalculateBootstrapCI:
    def test_insufficient_samples_returns_none(self) -> None:
        """サンプル数 < 20 → None。"""
        assert calculate_bootstrap_ci([1.0] * 10) is None

    def test_returns_bootstrap_ci(self) -> None:
        """20 サンプル以上 → BootstrapCI を返す。"""
        rng = np.random.default_rng(0)
        returns = list(rng.normal(0.01, 0.05, 50))
        result = calculate_bootstrap_ci(returns, n_bootstrap=200, rng=np.random.default_rng(42))
        assert isinstance(result, BootstrapCI)
        assert result.lower <= result.mean <= result.upper
        assert result.n_bootstrap == 200
        assert result.block_size >= 1

    def test_positive_returns_ci_lower_positive(self) -> None:
        """大きく正のリターン列 → CI 下限が正になる可能性が高い。"""
        returns = [0.1] * 100  # 全て正
        result = calculate_bootstrap_ci(returns, n_bootstrap=500, rng=np.random.default_rng(0))
        assert result is not None
        assert result.lower > 0

    def test_reproducibility_with_same_rng(self) -> None:
        """同じ seed の rng → 結果が一致する。"""
        returns = list(np.random.default_rng(1).normal(0, 1, 50))
        r1 = calculate_bootstrap_ci(returns, n_bootstrap=100, rng=np.random.default_rng(99))
        r2 = calculate_bootstrap_ci(returns, n_bootstrap=100, rng=np.random.default_rng(99))
        assert r1 is not None and r2 is not None
        assert r1.mean == pytest.approx(r2.mean)
        assert r1.lower == pytest.approx(r2.lower)


# --------------------------------------------------------------------------- #
# calculate_buy_and_hold
# --------------------------------------------------------------------------- #

class TestCalculateBuyAndHold:
    def test_uptrend_positive_bah(self) -> None:
        """上昇トレンドデータ → B&H リターンが正。"""
        ohlcv = _make_ohlcv(n=100, trend_pct=20.0)
        result = calculate_buy_and_hold(ohlcv, strategy_return_pct=15.0)
        assert isinstance(result, BuyAndHoldResult)
        assert result.bah_return_pct > 0

    def test_beats_true_when_alpha_positive(self) -> None:
        """戦略リターン > B&H リターン → beats=True。"""
        ohlcv = _make_ohlcv(n=100, trend_pct=5.0)
        result = calculate_buy_and_hold(ohlcv, strategy_return_pct=50.0)
        assert result.beats is True
        assert result.alpha_pct > 0

    def test_beats_false_when_alpha_negative(self) -> None:
        """戦略リターン < B&H リターン → beats=False。"""
        ohlcv = _make_ohlcv(n=100, trend_pct=30.0)
        result = calculate_buy_and_hold(ohlcv, strategy_return_pct=1.0)
        assert result.beats is False
        assert result.alpha_pct < 0

    def test_empty_ohlcv(self) -> None:
        """空の OHLCV → bah_return_pct=0、beats は strategy_return_pct 依存。"""
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        result = calculate_buy_and_hold(empty, strategy_return_pct=5.0)
        assert result.bah_return_pct == 0.0
        assert result.beats is True  # strategy=5% > bah=0%


# --------------------------------------------------------------------------- #
# calculate_random_entry_benchmark
# --------------------------------------------------------------------------- #

class TestCalculateRandomEntryBenchmark:
    def test_insufficient_data_returns_none(self) -> None:
        """データが avg_holding_bars + 1 未満 → None。"""
        ohlcv = _make_ohlcv(n=5)
        result = calculate_random_entry_benchmark(
            ohlcv, avg_holding_bars=10, n_simulations=100
        )
        assert result is None

    def test_returns_random_entry_result(self) -> None:
        """十分なデータ → RandomEntryResult を返す。"""
        ohlcv = _make_ohlcv(n=200)
        result = calculate_random_entry_benchmark(
            ohlcv,
            avg_holding_bars=10,
            n_simulations=500,
            rng=np.random.default_rng(0),
        )
        assert isinstance(result, RandomEntryResult)
        assert result.n_simulations == 500
        assert result.p05_return_pct <= result.mean_return_pct <= result.p95_return_pct

    def test_beats_median_when_high_strategy_return(self) -> None:
        """大幅に高い戦略リターン → beats_median=True。"""
        ohlcv = _make_ohlcv(n=200, trend_pct=5.0)
        result = calculate_random_entry_benchmark(
            ohlcv,
            avg_holding_bars=10,
            n_simulations=500,
            strategy_return_pct=1000.0,
            rng=np.random.default_rng(0),
        )
        assert result is not None
        assert result.beats_median is True


# --------------------------------------------------------------------------- #
# check_live_readiness
# --------------------------------------------------------------------------- #

class TestCheckLiveReadiness:
    def test_all_pass_returns_live_ready(self) -> None:
        """全チェック PASS → live_ready=True。"""
        metrics = _make_metrics()
        result = check_live_readiness(metrics)
        assert isinstance(result, LiveReadinessResult)
        assert result.live_ready is True

    def test_insufficient_trades_fails(self) -> None:
        """取引回数 <= 30 → live_ready=False。"""
        metrics = _make_metrics(total_trades=10)
        result = check_live_readiness(metrics)
        assert result.live_ready is False
        # 失敗したチェックが存在すること
        failed = [name for name, passed, _ in result.checks if not passed]
        assert any("取引回数" in name for name in failed)

    def test_cvar_none_fails(self) -> None:
        """cvar_95=None（サンプル不足）→ live_ready=False。"""
        metrics = _make_metrics(cvar_95=None)
        result = check_live_readiness(metrics)
        assert result.live_ready is False

    def test_negative_total_pnl_fails(self) -> None:
        """総損益 <= 0 → live_ready=False。"""
        metrics = _make_metrics(total_pnl=-1000.0)
        result = check_live_readiness(metrics)
        assert result.live_ready is False

    def test_large_drawdown_fails(self) -> None:
        """最大ドローダウン >= 15% → live_ready=False。"""
        metrics = _make_metrics(max_drawdown_pct=-16.0)
        result = check_live_readiness(metrics)
        assert result.live_ready is False

    def test_bootstrap_ci_check_included(self) -> None:
        """bootstrap_ci が渡された場合、CI 下限チェックが checks に含まれること。"""
        metrics = _make_metrics()
        ci = BootstrapCI(mean=0.01, lower=-0.005, upper=0.02, n_bootstrap=1000, block_size=4)
        result = check_live_readiness(metrics, bootstrap_ci=ci)
        check_names = [name for name, _, _ in result.checks]
        assert any("Bootstrap" in name for name in check_names)
        # CI 下限が負 → live_ready=False
        assert result.live_ready is False

    def test_bah_check_included(self) -> None:
        """bah_result が渡された場合、B&H チェックが checks に含まれること。"""
        metrics = _make_metrics()
        bah = BuyAndHoldResult(
            bah_return_pct=10.0,
            strategy_return_pct=5.0,
            alpha_pct=-5.0,
            beats=False,
        )
        result = check_live_readiness(metrics, bah_result=bah)
        assert result.live_ready is False  # B&H を下回るため

    def test_all_checks_in_result(self) -> None:
        """チェック結果リストは少なくとも 6 件（基本チェック）。"""
        metrics = _make_metrics()
        result = check_live_readiness(metrics)
        assert len(result.checks) >= 6
