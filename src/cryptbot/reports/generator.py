from __future__ import annotations

import re
from pathlib import Path

from cryptbot.backtest.benchmark import (
    BootstrapCI,
    BuyAndHoldResult,
    LiveReadinessResult,
    RandomEntryResult,
    check_live_readiness,
)
from cryptbot.backtest.engine import BacktestResult
from cryptbot.backtest.metrics import MetricsResult
from cryptbot.utils.time_utils import now_jst


def _format_benchmark_section(
    bootstrap_ci: BootstrapCI | None,
    bah_result: BuyAndHoldResult | None,
    random_result: RandomEntryResult | None,
) -> str:
    """ベンチマーク結果を Markdown セクションとしてフォーマットする。

    いずれも None の場合は空文字列を返す。
    """
    lines: list[str] = []

    if bootstrap_ci is not None:
        lower_mark = "PASS" if bootstrap_ci.lower > 0 else "FAIL"
        lines.append(
            f"\n## Bootstrap CI（block_size={bootstrap_ci.block_size}, "
            f"n={bootstrap_ci.n_bootstrap}）\n"
        )
        lines.append("| 指標 | 値 |")
        lines.append("|------|-----|")
        lines.append(f"| 平均リターン | {bootstrap_ci.mean:+.4f} |")
        lines.append(
            f"| {bootstrap_ci.confidence_level:.0%} CI | "
            f"[{bootstrap_ci.lower:+.4f}, {bootstrap_ci.upper:+.4f}] |"
        )
        lines.append(f"| CI 下限 > 0 | [{lower_mark}] |")

    if bah_result is not None:
        beats_mark = "PASS" if bah_result.beats else "FAIL"
        lines.append("\n## Buy & Hold 比較\n")
        lines.append("| 指標 | 値 |")
        lines.append("|------|-----|")
        lines.append(f"| 戦略リターン | {bah_result.strategy_return_pct:+.2f}% |")
        lines.append(f"| Buy & Hold | {bah_result.bah_return_pct:+.2f}% |")
        lines.append(f"| Alpha | {bah_result.alpha_pct:+.2f}% |")
        lines.append(f"| B&H 超過 | [{beats_mark}] |")

    if random_result is not None:
        beats_mark = "PASS" if random_result.beats_median else "FAIL"
        lines.append(
            f"\n## ランダムエントリーベンチマーク（n={random_result.n_simulations}）\n"
        )
        lines.append("| 指標 | 値 |")
        lines.append("|------|-----|")
        lines.append(f"| シミュレーション平均 | {random_result.mean_return_pct:+.2f}% |")
        lines.append(
            f"| 5〜95%ile | "
            f"[{random_result.p05_return_pct:+.2f}%, {random_result.p95_return_pct:+.2f}%] |"
        )
        lines.append(f"| ランダム中央値超過 | [{beats_mark}] |")

    return "\n".join(lines)


class ReportGenerator:
    def __init__(
        self,
        output_dir: str | Path = "reports",
    ) -> None:
        self.output_dir = Path(output_dir)

    def generate(
        self,
        result: BacktestResult,
        metrics: MetricsResult,
        *,
        save: bool = True,
        bootstrap_ci: BootstrapCI | None = None,
        bah_result: BuyAndHoldResult | None = None,
        random_result: RandomEntryResult | None = None,
    ) -> str:
        """Markdown レポートを生成する。

        Args:
            result: BacktestResult
            metrics: MetricsResult
            save: True の場合 output_dir/{strategy}_{timestamp}.md に保存
            bootstrap_ci: Bootstrap CI 結果（省略可）
            bah_result: Buy & Hold 比較結果（省略可）
            random_result: ランダムエントリーベンチマーク結果（省略可）

        Returns:
            生成した Markdown 文字列
        """
        generated_at = now_jst().strftime("%Y-%m-%d %H:%M:%S %Z")
        start_str = metrics.start_time.strftime("%Y-%m-%d %H:%M")
        end_str = metrics.end_time.strftime("%Y-%m-%d %H:%M")

        # CVaR 表示（None の場合はサンプル不足を明示）
        cvar_display = (
            f"{metrics.cvar_95:.2f}% / トレード"
            if metrics.cvar_95 is not None
            else "N/A（サンプル不足）"
        )

        # Live Readiness 判定
        live_readiness = check_live_readiness(
            metrics,
            bootstrap_ci=bootstrap_ci,
            bah_result=bah_result,
            random_result=random_result,
        )
        readiness_mark = "**READY**" if live_readiness.live_ready else "**NOT READY**"
        readiness_lines = []
        for name, passed, detail in live_readiness.checks:
            mark = "PASS" if passed else "FAIL"
            readiness_lines.append(f"- [{mark}] {name}: {detail}")
        readiness_section = "\n".join(readiness_lines)

        # ベンチマークセクション
        benchmark_section = _format_benchmark_section(bootstrap_ci, bah_result, random_result)

        report = f"""\
# バックテストレポート: {result.strategy_name}

生成日時: {generated_at}

## 概要

| 項目 | 値 |
|------|-----|
| 期間 | {start_str} 〜 {end_str} ({metrics.duration_days:.1f}日) |
| 初期残高 | {result.initial_balance:,.0f} JPY |
| 最終残高 | {result.final_balance:,.0f} JPY |
| 総損益 | {metrics.total_pnl:+,.0f} JPY |
| 総リターン | {metrics.total_return_pct:+.2f}% |
| 年率リターン | {metrics.annual_return_pct:+.2f}% |

## リスク指標

| 指標 | 値 | 基準 |
|------|-----|------|
| 最大ドローダウン | {metrics.max_drawdown_pct:.2f}% | < 15% |
| Sharpe Ratio | {metrics.sharpe_ratio:.3f} | > 0.5 |
| Sortino Ratio | {metrics.sortino_ratio:.3f} | > 0.7 |
| Calmar Ratio | {metrics.calmar_ratio:.3f} | > 0.5 |
| CVaR(95%) | {cvar_display} | < -3% |

## トレード統計

| 指標 | 値 |
|------|-----|
| 取引回数 | {metrics.total_trades} |
| 勝率 | {metrics.win_rate:.1%} |
| 平均利益 | {metrics.avg_win:+,.0f} JPY |
| 平均損失 | {metrics.avg_loss:+,.0f} JPY |
| Profit Factor | {metrics.profit_factor:.2f} |
| 最大連敗数 | {metrics.max_consecutive_losses} |
| 総手数料 | {metrics.total_fee:,.0f} JPY |
{benchmark_section}
## Live 移行判定: {readiness_mark}

{readiness_section}
"""

        if save:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = now_jst().strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r"[^\w\-]", "_", result.strategy_name)
            filename = f"{safe_name}_{timestamp}.md"
            filepath = self.output_dir / filename
            filepath.write_text(report, encoding="utf-8")

        return report

    def generate_walk_forward_summary(
        self,
        results: list[BacktestResult],
        metrics_list: list[MetricsResult],
    ) -> str:
        """Walk-forward 検証の全ウィンドウ結果をまとめたレポートを生成する。

        各ウィンドウの期待値・Sharpe・MDD を比較テーブルで表示する。
        """
        if len(results) != len(metrics_list):
            raise ValueError(
                f"results ({len(results)}) と metrics_list ({len(metrics_list)}) の長さが一致しません"
            )
        generated_at = now_jst().strftime("%Y-%m-%d %H:%M:%S %Z")
        strategy_name = results[0].strategy_name if results else "N/A"

        rows = []
        for i, (result, metrics) in enumerate(zip(results, metrics_list), start=1):
            start_str = metrics.start_time.strftime("%Y-%m-%d")
            end_str = metrics.end_time.strftime("%Y-%m-%d")
            rows.append(
                f"| {i} | {start_str} 〜 {end_str} "
                f"| {metrics.total_pnl:+,.0f} "
                f"| {metrics.sharpe_ratio:.3f} "
                f"| {metrics.max_drawdown_pct:.2f}% |"
            )
        table_rows = "\n".join(rows)

        # 集計
        total_pnl_all = sum(m.total_pnl for m in metrics_list)
        avg_sharpe = sum(m.sharpe_ratio for m in metrics_list) / len(metrics_list) if metrics_list else 0.0
        worst_mdd = min(m.max_drawdown_pct for m in metrics_list) if metrics_list else 0.0
        passed_windows = sum(1 for m in metrics_list if m.total_pnl > 0)

        report = f"""\
# Walk-Forward 検証サマリー: {strategy_name}

生成日時: {generated_at}
ウィンドウ数: {len(results)}

## ウィンドウ別結果

| # | 期間 | 総損益 (JPY) | Sharpe | 最大DD |
|---|------|------------|--------|--------|
{table_rows}

## 集計

| 指標 | 値 |
|------|-----|
| 合計損益 | {total_pnl_all:+,.0f} JPY |
| 平均 Sharpe | {avg_sharpe:.3f} |
| 最大ドローダウン（最悪） | {worst_mdd:.2f}% |
| 利益ウィンドウ数 | {passed_windows} / {len(results)} |
"""
        return report
