"""バックテスト評価指標計算。

BacktestResult から各種パフォーマンス指標を計算する関数群。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from cryptbot.backtest.engine import BacktestResult, TradeRecord


@dataclass
class MetricsResult:
    # 主指標
    total_pnl: float              # 総損益 [JPY]
    total_return_pct: float       # 総リターン [%]
    annual_return_pct: float      # 年率リターン [%]
    max_drawdown_pct: float       # 最大ドローダウン [%]（観測値）

    # 副指標
    sharpe_ratio: float           # Sharpe Ratio（年率）
    sortino_ratio: float          # Sortino Ratio（年率）
    calmar_ratio: float           # Calmar Ratio
    profit_factor: float          # 総利益 / 総損失（絶対値）
    win_rate: float               # 勝率 [0-1]
    avg_win: float                # 平均利益 [JPY]
    avg_loss: float               # 平均損失 [JPY]（負値）
    cvar_95: float | None         # CVaR(95%) [%]（サンプル不足の場合は None）

    # 取引統計
    total_trades: int
    winning_trades: int
    losing_trades: int
    max_consecutive_losses: int
    total_fee: float              # 総手数料 [JPY]

    # 期間
    start_time: datetime
    end_time: datetime
    duration_days: float

    # ベンチマーク比較（計算されなかった場合は None）
    buy_and_hold_return_pct: float | None = None
    no_trade_rate: float | None = None   # HOLD シグナルの割合（signals が渡された場合）


def calculate_metrics(
    result: BacktestResult,
    *,
    risk_free_rate: float = 0.0,
) -> MetricsResult:
    """BacktestResult から評価指標を計算する。"""
    trades = result.trades
    equity = result.equity_curve

    # ---- 損益集計 ----
    total_pnl = result.final_balance - result.initial_balance
    total_return_pct = (
        (result.final_balance / result.initial_balance - 1) * 100
        if result.initial_balance > 0
        else 0.0
    )

    # ---- 年率リターン ----
    total_days = (result.end_time - result.start_time).days
    if total_days <= 0:
        annual_return_pct = 0.0
    else:
        total_return = result.final_balance / result.initial_balance
        annual_return_pct = (total_return ** (365.0 / total_days) - 1) * 100

    # ---- ドローダウン ----
    if len(equity) > 0:
        drawdown_series = calculate_drawdown_series(equity)
        max_drawdown_pct = float(drawdown_series.min())
    else:
        max_drawdown_pct = 0.0

    # ---- Sharpe / Sortino ----
    if len(equity) > 1:
        returns = equity.pct_change().dropna()
        sharpe_ratio = calculate_sharpe(returns, risk_free_rate=risk_free_rate)
        sortino_ratio = calculate_sortino(returns, risk_free_rate=risk_free_rate)
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

    # ---- Calmar Ratio ----
    if max_drawdown_pct < 0:
        calmar_ratio = annual_return_pct / abs(max_drawdown_pct)
    else:
        calmar_ratio = 0.0

    # ---- トレード統計 ----
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    losing_trades = sum(1 for t in trades if t.pnl < 0)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0

    profit_factor = calculate_profit_factor(trades)

    total_fee = sum(t.fee for t in trades)

    # ---- CVaR ----
    trade_returns = [t.pnl_pct for t in trades]
    cvar_95 = calculate_cvar(trade_returns)

    # ---- 最大連続損失 ----
    max_consecutive_losses = _max_consecutive_losses(trades)

    # ---- 期間 ----
    duration_days = total_days if total_days >= 0 else 0.0

    return MetricsResult(
        total_pnl=total_pnl,
        total_return_pct=total_return_pct,
        annual_return_pct=annual_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        profit_factor=profit_factor,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        cvar_95=cvar_95,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        max_consecutive_losses=max_consecutive_losses,
        total_fee=total_fee,
        start_time=result.start_time,
        end_time=result.end_time,
        duration_days=float(duration_days),
    )


def _max_consecutive_losses(trades: list[TradeRecord]) -> int:
    """最大連続損失回数を計算する。"""
    max_streak = 0
    current_streak = 0
    for t in trades:
        if t.pnl < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def calculate_drawdown_series(equity: pd.Series) -> pd.Series:
    """equity curve からドローダウン率の時系列を計算する。

    Returns:
        Series of drawdown [%] (negative values)
    """
    rolling_max = equity.expanding().max()
    drawdown = (equity - rolling_max) / rolling_max * 100
    return drawdown


def calculate_sharpe(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 24 * 365,
) -> float:
    """Sharpe Ratio を計算する（年率換算）。

    returns が空または std=0 の場合は 0.0 を返す。
    """
    if len(returns) == 0:
        return 0.0
    excess_returns = returns - risk_free_rate / periods_per_year
    std = excess_returns.std()
    if std == 0 or np.isnan(std) or std < 1e-15:
        return 0.0
    annual_factor = np.sqrt(periods_per_year)
    return float(excess_returns.mean() / std * annual_factor)


def calculate_sortino(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 24 * 365,
) -> float:
    """Sortino Ratio を計算する（下方偏差を使用）。"""
    if len(returns) == 0:
        return 0.0
    excess_returns = returns - risk_free_rate / periods_per_year
    downside = excess_returns[excess_returns < 0]
    if len(downside) == 0:
        return 0.0
    downside_std = float(np.sqrt((downside ** 2).mean()))
    if downside_std == 0:
        return 0.0
    annual_factor = np.sqrt(periods_per_year)
    return float(excess_returns.mean() / downside_std * annual_factor)


def calculate_cvar(
    trade_returns: list[float],
    confidence_level: float = 0.95,
    min_samples: int = 20,
) -> float | None:
    """CVaR(95%) を計算する。

    下位 (1 - confidence_level) のリターンの平均を返す。
    サンプル数が min_samples 未満の場合は None を返す（統計的に信頼できないため）。

    Args:
        trade_returns: トレードごとの損益率リスト
        confidence_level: 信頼水準（デフォルト 0.95）
        min_samples: 計算に必要な最小サンプル数（デフォルト 20）

    Returns:
        CVaR 値（負値が典型的）、またはサンプル不足の場合 None
    """
    if len(trade_returns) < min_samples:
        return None
    arr = np.array(sorted(trade_returns))
    cutoff_idx = int(len(arr) * (1 - confidence_level))
    if cutoff_idx == 0:
        return None
    return float(arr[:cutoff_idx].mean())


def calculate_profit_factor(trades: list[TradeRecord]) -> float:
    """Profit Factor = 総利益 / 総損失（絶対値）。

    損失がない場合は inf を返す。
    """
    total_gain = sum(t.pnl for t in trades if t.pnl > 0)
    total_loss = sum(abs(t.pnl) for t in trades if t.pnl <= 0)
    if total_loss == 0:
        return float("inf")
    return total_gain / total_loss
