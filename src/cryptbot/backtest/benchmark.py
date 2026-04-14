"""バックテスト ベンチマーク・統計検定・Live Readiness 判定モジュール。

提供する機能:
- BootstrapCI        : block bootstrap による信頼区間
- BuyAndHoldResult   : Buy & Hold ベンチマーク比較
- RandomEntryResult  : ランダムエントリー Monte Carlo ベンチマーク
- LiveReadinessResult: Live 移行可否の総合判定
- 各計算関数
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from cryptbot.backtest.metrics import MetricsResult


# --------------------------------------------------------------------------- #
# データクラス
# --------------------------------------------------------------------------- #

@dataclass
class BootstrapCI:
    """Block bootstrap による平均リターンの信頼区間。

    Attributes:
        mean: ブートストラップ分布の平均
        lower: 信頼区間の下限（デフォルト 2.5 パーセンタイル）
        upper: 信頼区間の上限（デフォルト 97.5 パーセンタイル）
        n_bootstrap: ブートストラップ反復回数
        block_size: 使用したブロックサイズ
        confidence_level: 信頼水準（0.95 = 95%CI）
    """
    mean: float
    lower: float
    upper: float
    n_bootstrap: int
    block_size: int
    confidence_level: float = 0.95


@dataclass
class BuyAndHoldResult:
    """Buy & Hold ベンチマーク比較結果。

    Attributes:
        bah_return_pct: Buy & Hold の総リターン [%]
        strategy_return_pct: 戦略の総リターン [%]
        alpha_pct: strategy - B&H のアルファ [%]
        beats: 戦略が B&H を上回る場合 True
    """
    bah_return_pct: float
    strategy_return_pct: float
    alpha_pct: float
    beats: bool


@dataclass
class RandomEntryResult:
    """ランダムエントリー Monte Carlo ベンチマーク結果。

    Attributes:
        mean_return_pct: シミュレーション平均リターン [%]
        p05_return_pct: 5 パーセンタイル
        p95_return_pct: 95 パーセンタイル
        n_simulations: シミュレーション回数
        beats_median: 戦略リターンがシミュレーション中央値を上回る場合 True
    """
    mean_return_pct: float
    p05_return_pct: float
    p95_return_pct: float
    n_simulations: int
    beats_median: bool


@dataclass
class LiveReadinessResult:
    """Live 移行可否の総合判定結果。

    Attributes:
        live_ready: 全チェックが PASS の場合 True
        checks: [(criterion_name, passed, detail)] のリスト
    """
    live_ready: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# bootstrap CI
# --------------------------------------------------------------------------- #

def _optimal_block_size(n: int) -> int:
    """Politis-Romano 自動推定の近似（n^(1/3) を使用）。

    完全な Politis-Romano 推定は計算負荷が高いため、
    統計理論的に妥当な n^(1/3) を採用する。
    """
    return max(1, int(round(n ** (1 / 3))))


def calculate_bootstrap_ci(
    returns: list[float],
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    min_samples: int = 20,
    rng: np.random.Generator | None = None,
) -> BootstrapCI | None:
    """Circular Block Bootstrap による平均リターンの信頼区間を計算する。

    系列相関がある時系列に対して正しい信頼区間を与える。
    ブロックサイズは n^(1/3) で自動推定する。

    Args:
        returns: トレードごとのリターン率リスト
        n_bootstrap: ブートストラップ反復回数（デフォルト 1000）
        confidence_level: 信頼水準（デフォルト 0.95）
        min_samples: 計算に必要な最小サンプル数（デフォルト 20）
        rng: 乱数ジェネレータ（再現性テスト用）

    Returns:
        BootstrapCI、またはサンプル不足の場合 None
    """
    if len(returns) < min_samples:
        return None

    arr = np.array(returns, dtype=float)
    n = len(arr)
    block_size = _optimal_block_size(n)
    rng = rng or np.random.default_rng()

    # Circular Block Bootstrap
    # ブロック開始インデックスをランダムサンプリング
    n_blocks = int(np.ceil(n / block_size))
    boot_means = np.empty(n_bootstrap)

    for i in range(n_bootstrap):
        starts = rng.integers(0, n, size=n_blocks)
        indices = np.concatenate([
            np.arange(s, s + block_size) % n for s in starts
        ])[:n]
        boot_means[i] = arr[indices].mean()

    alpha = 1 - confidence_level
    lower = float(np.percentile(boot_means, alpha / 2 * 100))
    upper = float(np.percentile(boot_means, (1 - alpha / 2) * 100))

    return BootstrapCI(
        mean=float(boot_means.mean()),
        lower=lower,
        upper=upper,
        n_bootstrap=n_bootstrap,
        block_size=block_size,
        confidence_level=confidence_level,
    )


# --------------------------------------------------------------------------- #
# Buy & Hold ベンチマーク
# --------------------------------------------------------------------------- #

def calculate_buy_and_hold(
    ohlcv: pd.DataFrame,
    strategy_return_pct: float,
    cost_pct: float = 0.0015,
) -> BuyAndHoldResult:
    """Buy & Hold ベンチマークと戦略リターンを比較する。

    Buy & Hold リターン = (最終 close - 初期 open) / 初期 open * 100 - 往復コスト

    Args:
        ohlcv: OHLCV DataFrame（timestamp 昇順）
        strategy_return_pct: 戦略の総リターン [%]
        cost_pct: 往復コスト率（Taker + スリッページ、デフォルト 0.15%）

    Returns:
        BuyAndHoldResult
    """
    if ohlcv.empty or len(ohlcv) < 2:
        bah_return = 0.0
    else:
        entry_price = float(ohlcv["open"].iloc[0])
        exit_price = float(ohlcv["close"].iloc[-1])
        raw_return = (exit_price - entry_price) / entry_price * 100
        bah_return = raw_return - cost_pct * 2 * 100  # 往復コスト [%]

    alpha = strategy_return_pct - bah_return
    return BuyAndHoldResult(
        bah_return_pct=bah_return,
        strategy_return_pct=strategy_return_pct,
        alpha_pct=alpha,
        beats=alpha > 0,
    )


# --------------------------------------------------------------------------- #
# ランダムエントリー Monte Carlo ベンチマーク
# --------------------------------------------------------------------------- #

def calculate_random_entry_benchmark(
    ohlcv: pd.DataFrame,
    avg_holding_bars: int,
    n_simulations: int = 1000,
    cost_pct: float = 0.0015,
    strategy_return_pct: float = 0.0,
    rng: np.random.Generator | None = None,
) -> RandomEntryResult | None:
    """ランダムエントリー・固定保有期間の Monte Carlo ベンチマークを計算する。

    各シミュレーションでランダムに 1 回エントリーし、avg_holding_bars 後にクローズ。
    戦略が「ランダム以上」であることを確認するための比較基準となる。

    Args:
        ohlcv: OHLCV DataFrame（timestamp 昇順）
        avg_holding_bars: 平均保有バー数
        n_simulations: シミュレーション回数（デフォルト 1000）
        cost_pct: 片道コスト率（デフォルト 0.15%）
        strategy_return_pct: 戦略の総リターン [%]（beats_median 判定に使用）
        rng: 乱数ジェネレータ（再現性テスト用）

    Returns:
        RandomEntryResult、またはデータ不足の場合 None
    """
    n = len(ohlcv)
    if n < avg_holding_bars + 1:
        return None

    closes = ohlcv["close"].to_numpy(dtype=float)
    opens = ohlcv["open"].to_numpy(dtype=float)
    rng = rng or np.random.default_rng()

    max_entry = n - avg_holding_bars - 1
    entry_indices = rng.integers(0, max_entry + 1, size=n_simulations)

    sim_returns = np.empty(n_simulations)
    for i, entry_idx in enumerate(entry_indices):
        exit_idx = entry_idx + avg_holding_bars
        entry_price = opens[entry_idx]
        exit_price = closes[exit_idx]
        raw_return = (exit_price - entry_price) / entry_price * 100
        sim_returns[i] = raw_return - cost_pct * 2 * 100

    median_return = float(np.median(sim_returns))
    return RandomEntryResult(
        mean_return_pct=float(sim_returns.mean()),
        p05_return_pct=float(np.percentile(sim_returns, 5)),
        p95_return_pct=float(np.percentile(sim_returns, 95)),
        n_simulations=n_simulations,
        beats_median=strategy_return_pct > median_return,
    )


# --------------------------------------------------------------------------- #
# Live Readiness 総合判定
# --------------------------------------------------------------------------- #

def check_live_readiness(
    metrics: "MetricsResult",
    *,
    bootstrap_ci: BootstrapCI | None = None,
    bah_result: BuyAndHoldResult | None = None,
    random_result: RandomEntryResult | None = None,
) -> LiveReadinessResult:
    """バックテスト結果から Live 移行可否を総合判定する。

    各チェックが PASS かどうかを返す。live_ready は全チェック PASS の場合のみ True。

    組み込みチェック（metrics から）:
    - 取引回数 > 30
    - 総損益 > 0
    - 最大ドローダウン < 15%
    - Profit Factor > 1.3
    - Sharpe > 0.5
    - CVaR が計算可能（サンプル不足でない）

    ベンチマークチェック（オプション）:
    - Bootstrap CI 下限 > 0
    - Buy & Hold を上回る
    - ランダムエントリー中央値を上回る

    Args:
        metrics: MetricsResult
        bootstrap_ci: BootstrapCI（省略可）
        bah_result: BuyAndHoldResult（省略可）
        random_result: RandomEntryResult（省略可）

    Returns:
        LiveReadinessResult
    """
    checks: list[tuple[str, bool, str]] = []

    # --- 基本チェック ---
    checks.append((
        "取引回数 > 30",
        metrics.total_trades > 30,
        str(metrics.total_trades),
    ))
    checks.append((
        "総損益 > 0",
        metrics.total_pnl > 0,
        f"{metrics.total_pnl:+,.0f} JPY",
    ))
    checks.append((
        "最大ドローダウン < 15%",
        metrics.max_drawdown_pct > -15,
        f"{metrics.max_drawdown_pct:.2f}%",
    ))
    checks.append((
        "Profit Factor > 1.3",
        metrics.profit_factor > 1.3,
        f"{metrics.profit_factor:.2f}",
    ))
    checks.append((
        "Sharpe > 0.5",
        metrics.sharpe_ratio > 0.5,
        f"{metrics.sharpe_ratio:.3f}",
    ))
    checks.append((
        "CVaR 計算可能（サンプル数 >= 20）",
        metrics.cvar_95 is not None,
        "OK" if metrics.cvar_95 is not None else "サンプル不足",
    ))

    # --- Bootstrap CI チェック ---
    if bootstrap_ci is not None:
        checks.append((
            "Bootstrap CI 下限 > 0",
            bootstrap_ci.lower > 0,
            f"lower={bootstrap_ci.lower:.4f}",
        ))

    # --- Buy & Hold チェック ---
    if bah_result is not None:
        checks.append((
            "Buy & Hold を上回る",
            bah_result.beats,
            f"alpha={bah_result.alpha_pct:+.2f}%",
        ))

    # --- ランダムエントリーチェック ---
    if random_result is not None:
        checks.append((
            "ランダムエントリー中央値を上回る",
            random_result.beats_median,
            f"random_mean={random_result.mean_return_pct:+.2f}%",
        ))

    live_ready = all(passed for _, passed, _ in checks)
    return LiveReadinessResult(live_ready=live_ready, checks=checks)
