from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from cryptbot.backtest.benchmark import BootstrapCI, BuyAndHoldResult, RandomEntryResult
from cryptbot.backtest.engine import BacktestResult
from cryptbot.backtest.metrics import MetricsResult
from cryptbot.reports.generator import ReportGenerator
from cryptbot.utils.time_utils import JST


def _make_result() -> BacktestResult:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=JST)
    end = datetime(2024, 3, 31, 0, 0, tzinfo=JST)
    return BacktestResult(
        strategy_name="TestStrategy",
        trades=[],
        equity_curve=pd.Series(dtype=float),
        portfolio_states=[],
        initial_balance=1_000_000.0,
        final_balance=1_150_000.0,
        start_time=start,
        end_time=end,
        params={},
    )


def _make_metrics() -> MetricsResult:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=JST)
    end = datetime(2024, 3, 31, 0, 0, tzinfo=JST)
    return MetricsResult(
        total_pnl=150_000.0,
        total_return_pct=15.0,
        annual_return_pct=62.5,
        max_drawdown_pct=-8.5,
        sharpe_ratio=1.23,
        sortino_ratio=1.85,
        calmar_ratio=7.35,
        profit_factor=1.75,
        win_rate=0.58,
        avg_win=12_000.0,
        avg_loss=-7_000.0,
        cvar_95=-2.5,
        total_trades=45,
        winning_trades=26,
        losing_trades=19,
        max_consecutive_losses=4,
        total_fee=22_500.0,
        start_time=start,
        end_time=end,
        duration_days=90.0,
    )


class TestGenerate:
    def test_returns_string(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        md = gen.generate(_make_result(), _make_metrics(), save=False)
        assert isinstance(md, str)

    def test_contains_strategy_name(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        md = gen.generate(_make_result(), _make_metrics(), save=False)
        assert "TestStrategy" in md

    def test_contains_key_metrics(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        md = gen.generate(_make_result(), _make_metrics(), save=False)
        # 総損益
        assert "+150,000" in md
        # Sharpe
        assert "1.230" in md
        # 勝率 58.0%
        assert "58.0%" in md
        # Profit Factor
        assert "1.75" in md
        # 最大連敗
        assert "4" in md

    def test_save_true_creates_file(self, tmp_path: Path) -> None:
        gen = ReportGenerator(output_dir=tmp_path)
        gen.generate(_make_result(), _make_metrics(), save=True)
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1

    def test_save_false_no_file(self, tmp_path: Path) -> None:
        gen = ReportGenerator(output_dir=tmp_path)
        gen.generate(_make_result(), _make_metrics(), save=False)
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 0


class TestGenerateP2:
    """P2-03c: benchmark セクション・live readiness・cvar_95=None の表示テスト。"""

    def test_cvar_none_shows_not_available(self) -> None:
        """cvar_95=None の場合、レポートに 'N/A' が含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        metrics = _make_metrics()
        metrics_none_cvar = MetricsResult(
            **{**metrics.__dict__, "cvar_95": None}
        )
        md = gen.generate(_make_result(), metrics_none_cvar, save=False)
        assert "N/A" in md

    def test_live_readiness_not_ready_shown(self) -> None:
        """live readiness FAIL の場合、'NOT READY' が含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        # total_trades=0 で必ず FAIL
        start = datetime(2024, 1, 1, 0, 0, tzinfo=JST)
        end = datetime(2024, 3, 31, 0, 0, tzinfo=JST)
        metrics_fail = MetricsResult(
            total_pnl=-10_000.0,
            total_return_pct=-1.0,
            annual_return_pct=-4.0,
            max_drawdown_pct=-20.0,
            sharpe_ratio=0.1,
            sortino_ratio=0.1,
            calmar_ratio=0.0,
            profit_factor=0.8,
            win_rate=0.3,
            avg_win=1_000.0,
            avg_loss=-2_000.0,
            cvar_95=None,
            total_trades=5,
            winning_trades=2,
            losing_trades=3,
            max_consecutive_losses=3,
            total_fee=500.0,
            start_time=start,
            end_time=end,
            duration_days=90.0,
        )
        md = gen.generate(_make_result(), metrics_fail, save=False)
        assert "NOT READY" in md

    def test_live_readiness_ready_shown(self) -> None:
        """全条件 PASS の場合、'READY' が含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        md = gen.generate(_make_result(), _make_metrics(), save=False)
        assert "READY" in md

    def test_benchmark_section_with_bootstrap_ci(self) -> None:
        """bootstrap_ci を渡した場合、Bootstrap CI セクションが含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        ci = BootstrapCI(mean=0.01, lower=0.001, upper=0.02, n_bootstrap=1000, block_size=4)
        md = gen.generate(_make_result(), _make_metrics(), save=False, bootstrap_ci=ci)
        assert "Bootstrap CI" in md

    def test_benchmark_section_with_bah(self) -> None:
        """bah_result を渡した場合、Buy & Hold セクションが含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        bah = BuyAndHoldResult(
            bah_return_pct=8.0,
            strategy_return_pct=15.0,
            alpha_pct=7.0,
            beats=True,
        )
        md = gen.generate(_make_result(), _make_metrics(), save=False, bah_result=bah)
        assert "Buy & Hold" in md

    def test_benchmark_section_with_random_entry(self) -> None:
        """random_result を渡した場合、ランダムエントリーセクションが含まれること。"""
        gen = ReportGenerator(output_dir="reports")
        rr = RandomEntryResult(
            mean_return_pct=2.0,
            p05_return_pct=-3.0,
            p95_return_pct=7.0,
            n_simulations=1000,
            beats_median=True,
        )
        md = gen.generate(_make_result(), _make_metrics(), save=False, random_result=rr)
        assert "ランダムエントリー" in md


class TestGenerateWalkForwardSummary:
    def test_returns_string(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        results = [_make_result(), _make_result()]
        metrics_list = [_make_metrics(), _make_metrics()]
        summary = gen.generate_walk_forward_summary(results, metrics_list)
        assert isinstance(summary, str)

    def test_contains_window_count(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        results = [_make_result(), _make_result()]
        metrics_list = [_make_metrics(), _make_metrics()]
        summary = gen.generate_walk_forward_summary(results, metrics_list)
        assert "2" in summary

    def test_contains_strategy_name(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        results = [_make_result()]
        metrics_list = [_make_metrics()]
        summary = gen.generate_walk_forward_summary(results, metrics_list)
        assert "TestStrategy" in summary

    def test_length_mismatch_raises_error(self) -> None:
        gen = ReportGenerator(output_dir="reports")
        results = [_make_result(), _make_result()]
        metrics_list = [_make_metrics()]  # 長さが異なる
        with pytest.raises(ValueError, match="results.*metrics_list.*長さが一致しません"):
            gen.generate_walk_forward_summary(results, metrics_list)
