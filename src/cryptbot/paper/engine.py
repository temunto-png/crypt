"""Paper trading エンジン。

1 回の run_one_bar() 呼び出しで「状態ロード → 1 バー実行 → 状態保存」を完結する。
cron から定期呼び出しされることを想定した設計（1 invocation = 1 bar）。

## 約定タイミング（バックテストと一致）
- バー i のシグナル → バー i+1 の open 価格で約定（pending_signal として DB に永続化）

## 冪等性保証
- last_processed_bar_ts と最新バーの timestamp が同一なら処理をスキップして早期終了
- orders テーブルの orders_active_unique インデックスが二重 BUY active を防止
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from cryptbot.backtest.engine import (
    TradeRecord,
    _extract_timestamp,
    apply_ml_filter,
    apply_regime_override,
    calc_mark_balance,
    calc_trade_record,
)

if TYPE_CHECKING:
    from cryptbot.models.base import BaseMLModel
    from cryptbot.models.degradation_detector import DegradationDetector
from cryptbot.config.settings import PaperSettings
from cryptbot.data.storage import Storage
from cryptbot.paper.state import PaperState, PaperStateStore
from cryptbot.risk.manager import PortfolioState, RiskManager
from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.strategies.regime import RegimeDetector
from cryptbot.utils.time_utils import JST, now_jst

logger = logging.getLogger(__name__)

_MAX_RECENT_RETURNS = 50


@dataclass
class PaperRunResult:
    """run_one_bar() の実行結果。"""

    bar_timestamp: datetime
    signal: Signal | None           # 今バーで生成したシグナル（pending として次回約定）
    trade: TradeRecord | None       # 今バーで約定したトレード（前回 pending の約定）
    portfolio: PortfolioState
    cvar_action: str
    skipped: bool = False
    skip_reason: str = ""


class PaperEngine:
    """Paper trading エンジン（cron-friendly、1 bar per invocation）。"""

    TAKER_FEE_PCT = 0.001
    SLIPPAGE_PCT = 0.0005
    MIN_BTC_ORDER = 0.0001

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        state_store: PaperStateStore,
        storage: Storage,
        settings: PaperSettings,
        regime_detector: RegimeDetector | None = None,
        ml_model: "BaseMLModel | None" = None,
        degradation_detector: "DegradationDetector | None" = None,
        ml_confidence_threshold: float = 0.6,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._state_store = state_store
        self._storage = storage
        self._settings = settings
        self._regime_detector = regime_detector
        self._ml_model = ml_model
        self._degradation_detector = degradation_detector
        self._ml_confidence_threshold = ml_confidence_threshold

    def run_one_bar(self, data: pd.DataFrame) -> PaperRunResult:
        """最新バー 1 本分を処理して状態を保存する。

        Args:
            data: ウォームアップ分を含む OHLCV DataFrame。
                  data.iloc[-1] が今回処理する最新バー。
                  最低 (warmup_bars + 1) 本が必要。

        Returns:
            PaperRunResult（skipped=True の場合は同一バーの二重処理）
        """
        if len(data) == 0:
            return PaperRunResult(
                bar_timestamp=now_jst(),
                signal=None,
                trade=None,
                portfolio=self._make_initial_portfolio(),
                cvar_action="normal",
                skipped=True,
                skip_reason="empty_data",
            )

        current_bar = data.iloc[-1]
        current_bar_ts = _extract_timestamp(current_bar)
        current_date = current_bar_ts.date()

        # ---- 状態を復元 ----
        paper_state = self._state_store.load()
        if paper_state is None:
            portfolio, pending_signal, pending_cvar_action, recent_returns, entry_time, entry_price = (
                self._init_fresh_state()
            )
        else:
            # 同一バーの二重処理防止
            if paper_state.last_processed_bar_ts is not None:
                try:
                    last_ts = datetime.fromisoformat(paper_state.last_processed_bar_ts)
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=JST)
                    if last_ts == current_bar_ts:
                        logger.info(
                            "paper engine: 同一バー (%s) は処理済みのためスキップ", current_bar_ts
                        )
                        return PaperRunResult(
                            bar_timestamp=current_bar_ts,
                            signal=None,
                            trade=None,
                            portfolio=PaperStateStore.to_portfolio_state(paper_state),
                            cvar_action=paper_state.pending_cvar_action,
                            skipped=True,
                            skip_reason="already_processed",
                        )
                except ValueError:
                    pass

            portfolio = PaperStateStore.to_portfolio_state(paper_state)
            pending_signal = PaperStateStore.extract_pending_signal(paper_state)
            pending_cvar_action = paper_state.pending_cvar_action
            recent_returns = PaperStateStore.extract_recent_returns(paper_state)
            entry_time = PaperStateStore.extract_position_entry_time(paper_state)
            entry_price = paper_state.position_entry_price

        # ---- Kill switch チェック ----
        if self._risk_manager._kill_switch.active:
            logger.warning("paper engine: kill switch が active のためスキップ")
            new_state = PaperStateStore.from_portfolio_state(
                portfolio=portfolio,
                pending_signal=None,
                pending_cvar_action="normal",
                recent_trade_returns=recent_returns,
                position_entry_time=entry_time,
                position_entry_price=entry_price,
                last_processed_bar_ts=current_bar_ts,
            )
            self._state_store.save(new_state)
            return PaperRunResult(
                bar_timestamp=current_bar_ts,
                signal=None,
                trade=None,
                portfolio=portfolio,
                cvar_action="normal",
                skipped=True,
                skip_reason="kill_switch_active",
            )

        open_price = float(current_bar["open"])
        close_price = float(current_bar["close"])
        executed_trade: TradeRecord | None = None

        # ---- 前回バーの pending_signal を今バーの open 価格で約定 ----
        if pending_signal is not None:
            portfolio, entry_time, entry_price, executed_trade = self._execute_pending(
                pending_signal=pending_signal,
                cvar_action=pending_cvar_action,
                execution_price=open_price,
                timestamp=current_bar_ts,
                current_date=current_date,
                portfolio=portfolio,
                position_entry_time=entry_time,
                position_entry_price=entry_price,
            )
            if executed_trade is not None:
                self._record_fill(executed_trade)
                if executed_trade.pnl_pct != 0.0:
                    recent_returns.append(executed_trade.pnl_pct / 100.0)
                    if len(recent_returns) > _MAX_RECENT_RETURNS:
                        recent_returns.pop(0)

        # ---- per-bar リスク制御: max_trade_loss ----
        if portfolio.position_size != 0.0:
            if self._risk_manager.check_max_trade_loss(portfolio, close_price):
                logger.debug(
                    "max_trade_loss 超過: entry=%.0f current=%.0f → 翌バー強制クローズ",
                    portfolio.entry_price,
                    close_price,
                )
                new_pending = Signal(
                    direction=Direction.SELL,
                    confidence=1.0,
                    reason="max_trade_loss_exceeded",
                    timestamp=current_bar_ts,
                )
                mark_bal = calc_mark_balance(portfolio, close_price, self.TAKER_FEE_PCT, self.SLIPPAGE_PCT)
                self._risk_manager.check_circuit_breakers(portfolio, mark_bal)
                self._save_state(
                    portfolio, new_pending, "normal", recent_returns,
                    entry_time, entry_price, current_bar_ts,
                )
                return PaperRunResult(
                    bar_timestamp=current_bar_ts,
                    signal=new_pending,
                    trade=executed_trade,
                    portfolio=portfolio,
                    cvar_action="normal",
                )

        # ---- per-bar リスク制御: circuit breakers ----
        mark_balance = calc_mark_balance(portfolio, close_price, self.TAKER_FEE_PCT, self.SLIPPAGE_PCT)
        self._risk_manager.check_circuit_breakers(portfolio, mark_balance)

        # kill switch が circuit breaker で発動した場合
        if self._risk_manager._kill_switch.active:
            self._save_state(
                portfolio, None, "normal", recent_returns,
                entry_time, entry_price, current_bar_ts,
            )
            return PaperRunResult(
                bar_timestamp=current_bar_ts,
                signal=None,
                trade=executed_trade,
                portfolio=portfolio,
                cvar_action="normal",
                skipped=False,
                skip_reason="",
            )

        # ---- シグナル生成 ----
        new_signal = self._strategy.generate_signal(data)

        # ---- レジームオーバーライド ----
        new_signal = apply_regime_override(
            new_signal, data, portfolio, current_bar_ts, self._regime_detector
        )

        # ---- ML フィルター ----
        new_signal = apply_ml_filter(
            new_signal, data, current_bar_ts,
            self._ml_model, self._degradation_detector,
            self._ml_confidence_threshold,
            fail_closed=True,
        )

        # ---- CVaR フィルター ----
        cvar_action = "normal"
        if new_signal.direction == Direction.BUY:
            cvar_action = self._risk_manager.get_cvar_action(recent_returns)
            if cvar_action == "stop" or self._risk_manager._kill_switch.active:
                new_signal = Signal(
                    direction=Direction.HOLD,
                    confidence=0.0,
                    reason=f"cvar_block:{cvar_action}",
                    timestamp=current_bar_ts,
                )

        # ---- audit log にシグナルを記録 ----
        self._record_signal(new_signal, portfolio)

        # ---- 状態を保存（HOLD は pending に積まない） ----
        pending_to_save = new_signal if new_signal.direction != Direction.HOLD else None
        self._save_state(
            portfolio, pending_to_save, cvar_action, recent_returns,
            entry_time, entry_price, current_bar_ts,
        )

        return PaperRunResult(
            bar_timestamp=current_bar_ts,
            signal=new_signal,
            trade=executed_trade,
            portfolio=portfolio,
            cvar_action=cvar_action,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _execute_pending(
        self,
        pending_signal: Signal,
        cvar_action: str,
        execution_price: float,
        timestamp: datetime,
        current_date: date,
        portfolio: PortfolioState,
        position_entry_time: datetime | None,
        position_entry_price: float,
    ) -> tuple[PortfolioState, datetime | None, float, TradeRecord | None]:
        """pending_signal を execution_price（今バー open）で実行する。

        BacktestEngine._execute_pending() と同一のロジック。
        """
        trade: TradeRecord | None = None

        # クローズ判定
        if portfolio.position_size != 0.0:
            should_close = (
                pending_signal.direction == Direction.SELL
                or self._risk_manager._kill_switch.active
            )
            if should_close:
                trade = calc_trade_record(
                    entry_time=position_entry_time,
                    entry_price=position_entry_price,
                    exit_time=timestamp,
                    exit_price=execution_price,
                    size=portfolio.position_size,
                    strategy_name=self._strategy.name,
                    taker_fee_pct=self.TAKER_FEE_PCT,
                    slippage_pct=self.SLIPPAGE_PCT,
                )
                portfolio = self._risk_manager.record_trade_result(
                    portfolio, trade.exit_proceeds, trade.pnl, current_date
                )
                portfolio = dataclasses.replace(
                    portfolio, position_size=0.0, entry_price=0.0
                )
                position_entry_time = None
                position_entry_price = 0.0

        # エントリー判定
        if portfolio.position_size == 0.0 and pending_signal.direction == Direction.BUY:
            risk_result = self._risk_manager.check_entry(
                portfolio,
                pending_signal.confidence,
                execution_price,
                confidence_threshold=self._ml_confidence_threshold if self._ml_model is not None else 0.0,
            )
            if risk_result.allowed:
                size_multiplier = 0.5 if cvar_action == "half" else 1.0
                size = self._risk_manager.calculate_position_size(portfolio, execution_price)
                size = round(size * size_multiplier, 4)
                if size >= self.MIN_BTC_ORDER:
                    entry_cost = size * execution_price * (
                        1 + self.TAKER_FEE_PCT + self.SLIPPAGE_PCT
                    )
                    portfolio = dataclasses.replace(
                        portfolio,
                        position_size=size,
                        entry_price=execution_price,
                        balance=portfolio.balance - entry_cost,
                    )
                    position_entry_time = timestamp
                    position_entry_price = execution_price

        return portfolio, position_entry_time, position_entry_price, trade

    def _record_fill(self, trade: TradeRecord) -> None:
        """約定を orders テーブルと audit_log に記録する。

        orders テーブルへの記録は fail-open（エラー時はログのみ）。
        audit_log への fill 記録は fail-closed（例外を上位に伝播）。
        """
        try:
            order_id = self._storage.insert_order({
                "pair": self._settings.pair,
                "side": "BUY",
                "order_type": "market",
                "size": trade.size,
                "status": "CREATED",
                "fill_price": trade.entry_price,
                "fill_size": trade.size,
                "fee": trade.fee / 2,
                "strategy": self._strategy.name,
            })
            self._storage.update_order_status(
                order_id, "SUBMITTED",
                fill_price=trade.entry_price,
                fill_size=trade.size,
            )
            self._storage.update_order_status(
                order_id, "FILLED",
                fill_price=trade.entry_price,
                fill_size=trade.size,
                fee=trade.fee / 2,
            )

            sell_order_id = self._storage.insert_order({
                "pair": self._settings.pair,
                "side": "SELL",
                "order_type": "market",
                "size": trade.size,
                "status": "CREATED",
                "fill_price": trade.exit_price,
                "fill_size": trade.size,
                "fee": trade.fee / 2,
                "strategy": self._strategy.name,
            })
            self._storage.update_order_status(
                sell_order_id, "SUBMITTED",
                fill_price=trade.exit_price,
                fill_size=trade.size,
            )
            self._storage.update_order_status(
                sell_order_id, "FILLED",
                fill_price=trade.exit_price,
                fill_size=trade.size,
                fee=trade.fee / 2,
            )
        except Exception:
            logger.exception("orders テーブルへの約定記録中にエラーが発生しました")

        # audit_log への記録は fail-closed（例外を上位に伝播させる）
        self._storage.insert_audit_log(
            "fill",
            strategy=self._strategy.name,
            direction="SELL",
            fill_price=trade.exit_price,
            fill_size=trade.size,
            fee=trade.fee,
            slippage_pct=self.SLIPPAGE_PCT,
            portfolio_value=trade.exit_proceeds,
        )

    def _record_signal(self, signal: Signal, portfolio: PortfolioState) -> None:
        """シグナルを audit_log に記録する。fail-open（エラーは無視）。"""
        try:
            self._storage.insert_audit_log(
                "signal",
                strategy=self._strategy.name,
                direction=signal.direction.value,
                signal_confidence=signal.confidence,
                signal_reason=signal.reason,
                portfolio_value=portfolio.balance,
            )
        except Exception:
            logger.exception("シグナル記録中にエラーが発生しました")

    def _save_state(
        self,
        portfolio: PortfolioState,
        pending_signal: Signal | None,
        pending_cvar_action: str,
        recent_returns: list[float],
        entry_time: datetime | None,
        entry_price: float,
        bar_ts: datetime,
    ) -> None:
        state = PaperStateStore.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=pending_signal,
            pending_cvar_action=pending_cvar_action,
            recent_trade_returns=recent_returns,
            position_entry_time=entry_time,
            position_entry_price=entry_price,
            last_processed_bar_ts=bar_ts,
        )
        self._state_store.save(state)

    def _make_initial_portfolio(self) -> PortfolioState:
        today = datetime.now(JST).date()
        return PortfolioState(
            balance=self._settings.initial_balance,
            peak_balance=self._settings.initial_balance,
            position_size=0.0,
            entry_price=0.0,
            daily_loss=0.0,
            weekly_loss=0.0,
            monthly_loss=0.0,
            consecutive_losses=0,
            last_reset_date=today,
            last_weekly_reset=today,
            last_monthly_reset=today,
        )

    def _init_fresh_state(
        self,
    ) -> tuple[PortfolioState, Signal | None, str, list[float], datetime | None, float]:
        """初回起動時の状態を生成する。"""
        portfolio = self._make_initial_portfolio()
        return portfolio, None, "normal", [], None, 0.0

