from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings
from cryptbot.risk.kill_switch import KillSwitch, KillSwitchReason


@dataclass
class PortfolioState:
    """ポートフォリオの現在状態。"""

    balance: float           # 現在残高 [JPY]
    peak_balance: float      # 過去最高残高 [JPY]（ドローダウン計算用）
    position_size: float     # 現在ポジションサイズ [BTC]（0 = ノーポジション）
    entry_price: float       # エントリー価格 [JPY]（ポジションなし時は 0.0）
    daily_loss: float        # 本日の損失 [JPY]（正値 = 損失）
    weekly_loss: float       # 今週の損失 [JPY]
    monthly_loss: float      # 今月の損失 [JPY]
    consecutive_losses: int  # 連敗カウント（ラウンドトリップ単位）
    last_reset_date: date    # 日次損失リセット日
    last_weekly_reset: date  # 週次損失リセット日
    last_monthly_reset: date # 月次損失リセット日


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str
    triggered_limits: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(
        self,
        settings: CircuitBreakerSettings,
        cvar_settings: CvarSettings,
        kill_switch: KillSwitch,
    ) -> None:
        self._settings = settings
        self._cvar_settings = cvar_settings
        self._kill_switch = kill_switch

    def check_entry(
        self,
        portfolio: PortfolioState,
        signal_confidence: float,
        current_price: float,
    ) -> RiskCheckResult:
        """エントリー前のリスクチェック。

        以下のいずれかに該当する場合 allowed=False:
        1. Kill switch が active
        2. 日次損失 >= daily_loss_pct * balance
        3. 週次損失 >= weekly_loss_pct * balance
        4. 月次損失 >= monthly_loss_pct * balance
        5. 最大ドローダウン >= max_drawdown_pct * peak_balance
        6. 連敗 >= max_consecutive_losses
        7. ポジション既に保有中（最大同時ポジション数 = 1）
        """
        triggered: list[str] = []

        if self._kill_switch.active:
            triggered.append("kill_switch_active")

        daily_limit = self._settings.daily_loss_pct * portfolio.balance
        if portfolio.daily_loss >= daily_limit:
            triggered.append("daily_loss_exceeded")

        weekly_limit = self._settings.weekly_loss_pct * portfolio.balance
        if portfolio.weekly_loss >= weekly_limit:
            triggered.append("weekly_loss_exceeded")

        monthly_limit = self._settings.monthly_loss_pct * portfolio.balance
        if portfolio.monthly_loss >= monthly_limit:
            triggered.append("monthly_loss_exceeded")

        drawdown = portfolio.peak_balance - portfolio.balance
        drawdown_limit = self._settings.max_drawdown_pct * portfolio.peak_balance
        if drawdown >= drawdown_limit:
            triggered.append("max_drawdown_exceeded")

        if portfolio.consecutive_losses >= self._settings.max_consecutive_losses:
            triggered.append("consecutive_losses_exceeded")

        if portfolio.position_size != 0.0:
            triggered.append("position_already_open")

        if triggered:
            return RiskCheckResult(
                allowed=False,
                reason=", ".join(triggered),
                triggered_limits=triggered,
            )

        return RiskCheckResult(allowed=True, reason="all_checks_passed", triggered_limits=[])

    def calculate_position_size(
        self,
        portfolio: PortfolioState,
        current_price: float,
    ) -> float:
        """注文サイズを計算する [BTC]。

        ルール:
        - 最大ポジション: balance * 0.30 / current_price [BTC]
        - 最小注文: 0.0001 BTC（bitbank の最小注文数量）
        - 最小注文未満の場合は 0.0 を返す（注文不可）
        - 結果は 0.0001 BTC 単位に切り捨て
        """
        if current_price <= 0:
            return 0.0
        min_order = 0.0001
        raw_size = portfolio.balance * 0.30 / current_price

        if raw_size < min_order:
            return 0.0

        # 0.0001 BTC 単位に切り捨て
        floored = math.floor(raw_size / min_order) * min_order
        # 浮動小数点誤差対策: 4桁に丸める
        return round(floored, 4)

    def record_trade_result(
        self,
        portfolio: PortfolioState,
        cash_delta: float,
        realized_pnl: float,
        current_date: date,
    ) -> PortfolioState:
        """トレード結果を記録し、更新されたポートフォリオ状態を返す。

        Args:
            cash_delta: クローズ時にキャッシュに戻る金額（exit_proceeds）。
                        エントリー時に balance から entry_cost を差し引いている前提で、
                        cash_delta を加算することで正しい残高になる。
            realized_pnl: 実現損益（損失カウンタ・連敗更新に使用）。
            current_date: トレード日付（期間リセット判定に使用）。

        - 日次/週次/月次損失を更新
        - balance を更新（balance += cash_delta）
        - peak_balance を更新（残高が過去最高を超えた場合）
        - 連敗カウントを更新（損失: +1, 利益: 0にリセット）
        - 期間リセット: 日次（日付変更時）、週次（月曜日）、月次（1日）
        """
        import dataclasses
        p = dataclasses.replace(portfolio)

        # 期間リセット（損失記録より先に実施）
        if current_date != p.last_reset_date:
            p.daily_loss = 0.0
            p.last_reset_date = current_date

        # 週次リセット: 月曜日（weekday == 0）かつリセット日が今週より前
        if current_date.weekday() == 0 and current_date != p.last_weekly_reset:
            p.weekly_loss = 0.0
            p.last_weekly_reset = current_date

        # 月次リセット: 月初（day == 1）かつリセット日が今月より前
        if current_date.day == 1 and current_date != p.last_monthly_reset:
            p.monthly_loss = 0.0
            p.last_monthly_reset = current_date

        # balance 更新（exit_proceeds をキャッシュに戻す）
        p.balance += cash_delta

        # peak_balance 更新
        if p.balance > p.peak_balance:
            p.peak_balance = p.balance

        # 損失の記録（realized_pnl < 0 が損失）
        if realized_pnl < 0:
            loss = -realized_pnl  # 正値に変換
            p.daily_loss += loss
            p.weekly_loss += loss
            p.monthly_loss += loss
            p.consecutive_losses += 1
        else:
            p.consecutive_losses = 0

        return p

    def check_max_trade_loss(
        self,
        portfolio: PortfolioState,
        current_price: float,
    ) -> bool:
        """ポジション保有中に含み損が max_trade_loss_pct を超えた場合 True を返す。

        判定式: (current_price - entry_price) / entry_price < -max_trade_loss_pct
        """
        if portfolio.position_size == 0.0 or portfolio.entry_price <= 0:
            return False
        unrealized_return = (current_price - portfolio.entry_price) / portfolio.entry_price
        return unrealized_return < -self._settings.max_trade_loss_pct

    def check_circuit_breakers(
        self,
        portfolio: PortfolioState,
        mark_balance: float,
    ) -> bool:
        """毎バー: ドローダウン・月次損失を評価し kill switch を発動する。

        portfolio.balance（現金）ではなく mark_balance（含み損益込みの時価評価）を
        使用するため、ポジション保有中も正確に検知できる。

        Returns:
            kill switch が発動した場合 True
        """
        peak = portfolio.peak_balance
        drawdown_pct = (peak - mark_balance) / peak if peak > 0 else 0.0

        if drawdown_pct >= self._settings.max_drawdown_pct:
            self._kill_switch.activate(
                reason=KillSwitchReason.MAX_DRAWDOWN,
                portfolio_value=mark_balance,
                drawdown_pct=drawdown_pct,
            )
            return True

        # 月次損失チェック
        monthly_limit = self._settings.monthly_loss_pct * portfolio.balance
        if portfolio.monthly_loss >= monthly_limit:
            self._kill_switch.activate(
                reason=KillSwitchReason.MONTHLY_LOSS,
                portfolio_value=mark_balance,
                drawdown_pct=drawdown_pct,
            )
            return True

        return False

    def update_drawdown_stop(
        self,
        portfolio: PortfolioState,
    ) -> tuple[PortfolioState, bool]:
        """ドローダウンチェックと Kill switch 発動判定を返す。

        Returns:
            (updated_portfolio, kill_switch_triggered: bool)
        """
        drawdown = portfolio.peak_balance - portfolio.balance
        drawdown_pct = drawdown / portfolio.peak_balance if portfolio.peak_balance > 0 else 0.0

        if drawdown_pct >= self._settings.max_drawdown_pct:
            self._kill_switch.activate(
                reason=KillSwitchReason.MAX_DRAWDOWN,
                portfolio_value=portfolio.balance,
                drawdown_pct=drawdown_pct,
            )
            return portfolio, True

        return portfolio, False

    def get_cvar_action(
        self,
        recent_trade_returns: list[float],
    ) -> str:
        """直近トレードのリターンから CVaR を計算してアクションを返す。

        Args:
            recent_trade_returns: 直近50トレードのリターン率のリスト（損失: 負の値）

        Returns:
            "normal" | "warning" | "half" | "stop"

        Rules:
        - len < 20 → "normal"（サンプル不足）
        - cvar > warn_pct → "normal"  (warn_pct = -0.03)
        - cvar > half_pct → "warning" (half_pct = -0.04)
        - cvar > stop_pct → "half"    (stop_pct = -0.05)
        - cvar <= stop_pct → "stop"

        CVaR(95%) = 下位5%リターンの平均（負値）
        """
        if len(recent_trade_returns) < 20:
            return "normal"

        arr = np.array(recent_trade_returns, dtype=float)
        threshold = np.percentile(arr, 5)
        tail = arr[arr <= threshold]
        cvar = float(np.mean(tail))

        warn_pct = self._cvar_settings.warn_pct
        half_pct = self._cvar_settings.half_pct
        stop_pct = self._cvar_settings.stop_pct

        if cvar > warn_pct:
            return "normal"
        elif cvar > half_pct:
            return "warning"
        elif cvar > stop_pct:
            return "half"
        else:
            return "stop"
