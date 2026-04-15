"""バックテストエンジン。

OHLCV DataFrame 上でローソク足を1本ずつシミュレートし、
戦略のシグナルに基づいてトレードを記録する。
Walk-forward 検証対応。テスト期間保護付き。

## 約定タイミング（look-ahead bias 防止）
- バー i のシグナル → バー i+1 の open 価格で約定
- ウォームアップ終了時（i == warmup_bars）に strategy.reset() を呼び出し、
  ウォームアップ中に蓄積したステート（BuyAndHold の _has_position 等）をクリア

## per-bar リスク制御
- ポジション保有中: max_trade_loss_pct を超えた含み損 → 翌バー強制クローズ
- 毎バー: ドローダウン・月次損失 → kill switch 発動 → 翌バー以降のエントリー停止
- エントリー前: CVaR action == "stop" → エントリーブロック
- エントリー前: CVaR action == "half" → ポジションサイズ 50% 縮小

## レジーム統合（オプション）
- regime_detector が指定されている場合、シグナル生成後にオーバーライドする
- TREND_DOWN / HIGH_VOL: ポジション保有中なら翌バー強制クローズ、
  ノーポジションならエントリーをブロック
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import TYPE_CHECKING, Any

import pandas as pd
import numpy as np

from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.strategies.regime import RegimeDetector, Regime
from cryptbot.risk.manager import RiskManager, PortfolioState
from cryptbot.utils.time_utils import JST

if TYPE_CHECKING:
    from cryptbot.models.base import BaseMLModel
    from cryptbot.models.degradation_detector import DegradationDetector

logger = logging.getLogger(__name__)

# 連続リターン追跡の最大サイズ（CVaR 計算用）
_MAX_RECENT_RETURNS = 50


# ---------------------------------------------------------------------------
# モジュールレベルヘルパー（PaperEngine と共有）
# ---------------------------------------------------------------------------

def calc_trade_record(
    entry_time: datetime | None,
    entry_price: float,
    exit_time: datetime,
    exit_price: float,
    size: float,
    strategy_name: str,
    taker_fee_pct: float,
    slippage_pct: float,
) -> "TradeRecord":
    """クローズ時の TradeRecord を計算する（pure function）。

    BacktestEngine._close_position() および PaperEngine から共有される。
    """
    fee_entry = size * entry_price * taker_fee_pct
    slip_entry = size * entry_price * slippage_pct
    entry_cost = size * entry_price + fee_entry + slip_entry

    fee_exit = size * exit_price * taker_fee_pct
    slip_exit = size * exit_price * slippage_pct
    exit_proceeds = size * exit_price - fee_exit - slip_exit

    total_fee = fee_entry + fee_exit
    total_slippage = slip_entry + slip_exit
    pnl = exit_proceeds - entry_cost
    pnl_pct = pnl / entry_cost * 100.0 if entry_cost > 0 else 0.0

    return TradeRecord(
        entry_time=entry_time if entry_time is not None else exit_time,
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        size=size,
        pnl=pnl,
        pnl_pct=pnl_pct,
        fee=total_fee,
        slippage=total_slippage,
        exit_proceeds=exit_proceeds,
        strategy=strategy_name,
    )


def calc_mark_balance(
    portfolio: "PortfolioState",
    close_price: float,
    taker_fee_pct: float,
    slippage_pct: float,
) -> float:
    """ポジション保有中は mark-to-market で時価評価した残高を返す（pure function）。"""
    if portfolio.position_size != 0.0:
        unrealized = portfolio.position_size * close_price * (
            1 - taker_fee_pct - slippage_pct
        )
        return portfolio.balance + unrealized
    return portfolio.balance


def apply_regime_override(
    signal: "Signal",
    window: "pd.DataFrame",
    portfolio: "PortfolioState",
    timestamp: datetime,
    regime_detector: "RegimeDetector | None",
) -> "Signal":
    """レジームが TREND_DOWN / HIGH_VOL の場合にシグナルをオーバーライドする（pure function）。"""
    if regime_detector is None:
        return signal
    if len(window) < 20 or not _has_regime_columns(window):
        return signal
    try:
        regime = regime_detector.detect(window)
    except ValueError:
        return signal

    if regime not in (Regime.TREND_DOWN, Regime.HIGH_VOL):
        return signal

    if portfolio.position_size != 0.0:
        return Signal(
            direction=Direction.SELL,
            confidence=1.0,
            reason=f"regime_close:{regime.value}",
            timestamp=timestamp,
        )
    return Signal(
        direction=Direction.HOLD,
        confidence=0.0,
        reason=f"regime_skip:{regime.value}",
        timestamp=timestamp,
    )


@dataclass
class TradeRecord:
    """1 ラウンドトリップのトレード記録。"""
    entry_time: datetime    # JST
    exit_time: datetime     # JST
    entry_price: float
    exit_price: float
    size: float             # [BTC]
    pnl: float              # [JPY] 実現損益（手数料・スリッページ込み）
    pnl_pct: float          # [%] pnl / entry_cost
    fee: float              # [JPY]
    slippage: float         # [JPY]
    exit_proceeds: float    # [JPY] クローズ時にキャッシュに戻る金額（= entry_cost + pnl）
    strategy: str


@dataclass
class BacktestResult:
    """バックテスト実行結果。"""
    strategy_name: str
    trades: list[TradeRecord]
    equity_curve: pd.Series           # index: datetime, values: 残高 [JPY]
    portfolio_states: list[PortfolioState]
    initial_balance: float
    final_balance: float
    start_time: datetime
    end_time: datetime
    params: dict[str, Any]            # バックテスト設定のスナップショット


def _make_initial_portfolio(balance: float) -> PortfolioState:
    """初期 PortfolioState を生成する。"""
    today = date.today()
    return PortfolioState(
        balance=balance,
        peak_balance=balance,
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


def _extract_timestamp(row: pd.Series) -> datetime:
    """行から JST timezone-aware datetime を取得する。"""
    if "timestamp" in row.index:
        ts = row["timestamp"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=JST)
            return ts.astimezone(JST)
        if isinstance(ts, pd.Timestamp):
            dt = ts.to_pydatetime()
            if dt.tzinfo is None:
                return dt.replace(tzinfo=JST)
            return dt.astimezone(JST)
    # timestamp カラムがない場合は index から取得
    idx = row.name
    if isinstance(idx, (datetime, pd.Timestamp)):
        dt = idx if isinstance(idx, datetime) else idx.to_pydatetime()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    return datetime.now(JST)


def _has_regime_columns(data: pd.DataFrame) -> bool:
    """RegimeDetector に必要なカラムが揃っているか確認する。"""
    return all(c in data.columns for c in RegimeDetector.REQUIRED_COLUMNS)


def apply_ml_filter(
    signal: "Signal",
    window: "pd.DataFrame",
    timestamp: datetime,
    ml_model: "BaseMLModel | None",
    degradation_detector: "DegradationDetector | None",
    confidence_threshold: float = 0.6,
) -> "Signal":
    """ML モデルの confidence で Signal.confidence を上書きする（pure function）。

    - ml_model が None の場合はシグナルをそのまま返す
    - HOLD / SELL シグナルはそのまま返す（クローズシグナルはブロックしない）
    - 特徴量が計算できない場合（データ不足・カラム不足）はそのまま返す
    - DegradationDetector が degraded 状態の場合は confidence=0.0 を返す
    """
    if ml_model is None or signal.direction != Direction.BUY:
        return signal

    try:
        from cryptbot.models.feature_builder import get_latest_features
        features = get_latest_features(window)
        if features.empty:
            return signal

        prediction = ml_model.predict(features)

        # label <= 0 は DOWN または NEUTRAL 予測 → BUY を抑制
        if prediction.label <= 0:
            return Signal(
                direction=Direction.HOLD,
                confidence=0.0,
                reason=f"{signal.reason}|ml_label:{prediction.label}",
                timestamp=timestamp,
            )

        if degradation_detector is not None:
            degradation_detector.update(prediction.confidence)
            status = degradation_detector.check()
            if status.degraded:
                logger.warning("ML モデル劣化検知: シグナルを無効化 (%s)", status.message)
                return Signal(
                    direction=Direction.HOLD,
                    confidence=0.0,
                    reason=f"ml_degradation:{status.drop_pct:.1f}%_drop",
                    timestamp=timestamp,
                )

        return Signal(
            direction=signal.direction,
            confidence=prediction.confidence,
            reason=f"{signal.reason}|ml:{prediction.confidence:.3f}",
            timestamp=timestamp,
        )
    except Exception as exc:
        logger.warning("apply_ml_filter: 予期しないエラー (%s)、シグナルをそのまま返す", exc)
        return signal


class BacktestEngine:
    """バックテストエンジン。"""

    # コスト設定（設計書より）
    TAKER_FEE_PCT = 0.001    # 0.1%
    SLIPPAGE_PCT  = 0.0005   # 0.05%
    TOTAL_COST_PCT = TAKER_FEE_PCT + SLIPPAGE_PCT  # 0.0015 = 0.15%

    MIN_BTC_ORDER = 0.0001   # bitbank 最小注文数量

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        initial_balance: float = 100_000.0,
        taker_fee_pct: float = TAKER_FEE_PCT,
        slippage_pct: float = SLIPPAGE_PCT,
        regime_detector: RegimeDetector | None = None,
        ml_model: "BaseMLModel | None" = None,
        degradation_detector: "DegradationDetector | None" = None,
        ml_confidence_threshold: float = 0.6,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._initial_balance = initial_balance
        self._taker_fee_pct = taker_fee_pct
        self._slippage_pct = slippage_pct
        self._regime_detector = regime_detector
        self._ml_model = ml_model
        self._degradation_detector = degradation_detector
        self._ml_confidence_threshold = ml_confidence_threshold

    def run(
        self,
        data: pd.DataFrame,
        *,
        warmup_bars: int = 50,
    ) -> BacktestResult:
        """バックテストを実行する。

        約定タイミング:
        - バー i のシグナルはバー i+1 の open 価格で約定する（look-ahead bias 防止）
        - ウォームアップ終了後に strategy.reset() を呼び出す

        per-bar リスク制御:
        - max_trade_loss_pct: 含み損超過 → 翌バー強制クローズ
        - drawdown / monthly_loss: kill switch 発動
        - CVaR action: stop → エントリーブロック, half → サイズ 50%

        終了時にポジションが残っていれば最終バーの close 価格で強制クローズ。
        """
        # Kill switch が active なら警告ログを記録して空結果を返す
        if self._risk_manager._kill_switch.active:
            logger.warning(
                "BacktestEngine.run: kill switch が active のためバックテストをスキップします。"
            )
            empty_ts = datetime.now(JST)
            return BacktestResult(
                strategy_name=self._strategy.name,
                trades=[],
                equity_curve=pd.Series(dtype=float),
                portfolio_states=[],
                initial_balance=self._initial_balance,
                final_balance=self._initial_balance,
                start_time=empty_ts,
                end_time=empty_ts,
                params=self._make_params(warmup_bars),
            )

        portfolio = _make_initial_portfolio(self._initial_balance)
        trades: list[TradeRecord] = []
        equity_index: list[datetime] = []
        equity_values: list[float] = []
        portfolio_states: list[PortfolioState] = []
        recent_trade_returns: list[float] = []

        # オープンポジション情報
        position_entry_time: datetime | None = None
        position_entry_price: float = 0.0

        # バー i のシグナルをバー i+1 で実行するためのバッファ
        pending_signal: Signal | None = None
        # バー i で算出した cvar_action を翌バーの _execute_pending に渡す
        pending_cvar_action: str = "normal"

        n = len(data)

        for i in range(n):
            current_bar = data.iloc[i]
            close_price = float(current_bar["close"])
            timestamp = _extract_timestamp(current_bar)
            current_date = timestamp.date()

            # ---- ウォームアップ期間 ----
            if i < warmup_bars:
                # 指標ウォームアップのためシグナル生成は行うが、約定・equity 記録はしない
                window = data.iloc[: i + 1]
                _ = self._strategy.generate_signal(window)
                continue

            # ---- ウォームアップ終了直後の初期化 ----
            if i == warmup_bars:
                # ウォームアップ中に蓄積したステートをリセット
                self._strategy.reset()
                # 最初の pending signal を生成（次バーで実行される）
                window = data.iloc[: i + 1]
                pending_signal = self._strategy.generate_signal(window)
                pending_signal = self._apply_regime_override(pending_signal, window, portfolio, timestamp)
                # この最初のバーは pending 生成のみ。equity は記録する（execution なし）
                mark_balance = self._mark_balance(portfolio, close_price)
                equity_index.append(timestamp)
                equity_values.append(mark_balance)
                portfolio_states.append(dataclasses.replace(portfolio))
                continue

            # ---- 翌バー: pending signal を open 価格で実行 ----
            open_price = float(current_bar["open"])
            execution_price = open_price

            if pending_signal is not None:
                portfolio, position_entry_time, position_entry_price, trade = self._execute_pending(
                    pending_signal=pending_signal,
                    cvar_action=pending_cvar_action,
                    execution_price=execution_price,
                    timestamp=timestamp,
                    current_date=current_date,
                    portfolio=portfolio,
                    position_entry_time=position_entry_time,
                    position_entry_price=position_entry_price,
                )
                if trade is not None:
                    trades.append(trade)
                    if trade.pnl != 0.0 or trade.fee != 0.0:
                        entry_cost = trade.exit_proceeds - trade.pnl
                        if entry_cost > 0:
                            recent_trade_returns.append(trade.pnl / entry_cost)
                            if len(recent_trade_returns) > _MAX_RECENT_RETURNS:
                                recent_trade_returns.pop(0)

            # ---- per-bar リスク制御 ----
            # 1. 含み損が max_trade_loss_pct を超えた場合、翌バー強制クローズ
            if portfolio.position_size != 0.0:
                if self._risk_manager.check_max_trade_loss(portfolio, close_price):
                    logger.debug(
                        "max_trade_loss 超過: entry=%.0f current=%.0f → 翌バー強制クローズ",
                        portfolio.entry_price, close_price,
                    )
                    pending_signal = Signal(
                        direction=Direction.SELL,
                        confidence=1.0,
                        reason="max_trade_loss_exceeded",
                        timestamp=timestamp,
                    )
                    # mark_balance と circuit_breakers は下で実行
                    mark_balance = self._mark_balance(portfolio, close_price)
                    self._risk_manager.check_circuit_breakers(portfolio, mark_balance)
                    equity_index.append(timestamp)
                    equity_values.append(mark_balance)
                    portfolio_states.append(dataclasses.replace(portfolio))
                    continue

            # 2. ドローダウン / 月次損失チェック
            mark_balance = self._mark_balance(portfolio, close_price)
            self._risk_manager.check_circuit_breakers(portfolio, mark_balance)

            # ---- 新しい pending signal を生成 ----
            window = data.iloc[: i + 1]
            pending_signal = self._strategy.generate_signal(window)

            # ---- レジームオーバーライド ----
            pending_signal = self._apply_regime_override(pending_signal, window, portfolio, timestamp)

            # ---- ML フィルター ----
            pending_signal = apply_ml_filter(
                pending_signal, window, timestamp,
                self._ml_model, self._degradation_detector,
                self._ml_confidence_threshold,
            )

            # ---- CVaR による補正 ----
            if pending_signal.direction == Direction.BUY:
                cvar_action = self._risk_manager.get_cvar_action(recent_trade_returns)
                pending_cvar_action = cvar_action
                if cvar_action == "stop" or self._risk_manager._kill_switch.active:
                    pending_signal = Signal(
                        direction=Direction.HOLD,
                        confidence=0.0,
                        reason=f"cvar_block:{cvar_action}",
                        timestamp=timestamp,
                    )
            else:
                pending_cvar_action = "normal"

            # ---- equity 記録 ----
            equity_index.append(timestamp)
            equity_values.append(mark_balance)
            portfolio_states.append(dataclasses.replace(portfolio))

        # ---- 終了時のオープンポジション強制クローズ ----
        if portfolio.position_size != 0.0 and n > 0:
            last_bar = data.iloc[-1]
            last_price = float(last_bar["close"])
            last_ts = _extract_timestamp(last_bar)
            last_date = last_ts.date()

            trade = self._close_position(
                entry_time=position_entry_time,
                entry_price=position_entry_price,
                exit_time=last_ts,
                exit_price=last_price,
                size=portfolio.position_size,
            )
            trades.append(trade)
            portfolio = self._risk_manager.record_trade_result(
                portfolio, trade.exit_proceeds, trade.pnl, last_date
            )
            portfolio = dataclasses.replace(
                portfolio,
                position_size=0.0,
                entry_price=0.0,
            )

            # 最後の equity 値を強制クローズ後の残高で更新
            if equity_values:
                equity_values[-1] = portfolio.balance
                if portfolio_states:
                    portfolio_states[-1] = dataclasses.replace(portfolio)

        equity_curve = pd.Series(
            equity_values,
            index=equity_index,
            dtype=float,
        )

        start_time = equity_index[0] if equity_index else datetime.now(JST)
        end_time = equity_index[-1] if equity_index else datetime.now(JST)

        return BacktestResult(
            strategy_name=self._strategy.name,
            trades=trades,
            equity_curve=equity_curve,
            portfolio_states=portfolio_states,
            initial_balance=self._initial_balance,
            final_balance=portfolio.balance,
            start_time=start_time,
            end_time=end_time,
            params=self._make_params(warmup_bars),
        )

    def _execute_pending(
        self,
        pending_signal: Signal,
        execution_price: float,
        timestamp: datetime,
        current_date: date,
        portfolio: PortfolioState,
        position_entry_time: datetime | None,
        position_entry_price: float,
        cvar_action: str = "normal",
    ) -> tuple[PortfolioState, datetime | None, float, TradeRecord | None]:
        """pending_signal を execution_price（翌バー open）で実行する。

        Returns:
            (updated_portfolio, position_entry_time, position_entry_price, trade_or_None)
        """
        trade: TradeRecord | None = None

        # クローズ判定
        if portfolio.position_size != 0.0:
            should_close = (
                pending_signal.direction == Direction.SELL
                or self._risk_manager._kill_switch.active
            )
            if should_close:
                trade = self._close_position(
                    entry_time=position_entry_time,
                    entry_price=position_entry_price,
                    exit_time=timestamp,
                    exit_price=execution_price,
                    size=portfolio.position_size,
                )
                portfolio = self._risk_manager.record_trade_result(
                    portfolio, trade.exit_proceeds, trade.pnl, current_date
                )
                portfolio = dataclasses.replace(
                    portfolio,
                    position_size=0.0,
                    entry_price=0.0,
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
                        1 + self._taker_fee_pct + self._slippage_pct
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

    def _apply_regime_override(
        self,
        signal: Signal,
        window: pd.DataFrame,
        portfolio: PortfolioState,
        timestamp: datetime,
    ) -> Signal:
        """レジームが TREND_DOWN / HIGH_VOL の場合にシグナルをオーバーライドする。"""
        return apply_regime_override(signal, window, portfolio, timestamp, self._regime_detector)

    def _mark_balance(self, portfolio: PortfolioState, close_price: float) -> float:
        """ポジション保有中は mark-to-market で時価評価した残高を返す。"""
        return calc_mark_balance(portfolio, close_price, self._taker_fee_pct, self._slippage_pct)

    def _close_position(
        self,
        entry_time: datetime | None,
        entry_price: float,
        exit_time: datetime,
        exit_price: float,
        size: float,
    ) -> TradeRecord:
        """ポジションクローズ時の TradeRecord を生成する。"""
        return calc_trade_record(
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            size=size,
            strategy_name=self._strategy.name,
            taker_fee_pct=self._taker_fee_pct,
            slippage_pct=self._slippage_pct,
        )

    def _make_params(self, warmup_bars: int) -> dict[str, Any]:
        return {
            "warmup_bars": warmup_bars,
            "initial_balance": self._initial_balance,
            "taker_fee_pct": self._taker_fee_pct,
            "slippage_pct": self._slippage_pct,
        }

    def run_walk_forward(
        self,
        data: pd.DataFrame,
        train_months: int = 6,
        validation_months: int = 4,
        step_months: int = 4,
        *,
        warmup_bars: int = 50,
    ) -> list[BacktestResult]:
        """Walk-forward 検証を実行する。

        各ウィンドウ:
        - 学習期間: train_months（パラメータ最適化用、本メソッドでは run() するのみ）
        - 検証期間: validation_months（独立検証）

        run() 前に strategy.reset() を呼び出して各ウィンドウの独立性を保証する。

        Returns:
            検証期間の BacktestResult のリスト（ウィンドウごと）
        """
        if "timestamp" not in data.columns:
            raise ValueError("run_walk_forward: 'timestamp' カラムが必要です。")

        # timestamp を pd.Timestamp に統一
        timestamps = pd.to_datetime(data["timestamp"])
        start_ts = timestamps.iloc[0]
        end_ts = timestamps.iloc[-1]

        results: list[BacktestResult] = []
        window_start = start_ts

        while True:
            train_end = window_start + pd.DateOffset(months=train_months)
            val_end = train_end + pd.DateOffset(months=validation_months)

            # データ範囲内に train_end がない場合は終了
            if train_end > end_ts:
                break

            # 学習期間のデータ（warmup込みで run() する）
            train_mask = (timestamps >= window_start) & (timestamps < train_end)
            train_data = data[train_mask].reset_index(drop=True)

            # 検証期間のデータ
            val_mask = (timestamps >= train_end) & (timestamps < val_end)
            val_data = data[val_mask].reset_index(drop=True)

            # 学習期間が空の場合はスキップ
            if len(train_data) == 0:
                window_start = window_start + pd.DateOffset(months=step_months)
                continue

            # 検証期間が空の場合はスキップ
            if len(val_data) == 0:
                window_start = window_start + pd.DateOffset(months=step_months)
                continue

            # 学習期間: strategy をリセットしてから実行（ステート汚染防止）
            if len(train_data) > warmup_bars:
                self._strategy.reset()
                self.run(train_data, warmup_bars=warmup_bars)

            # 検証期間: strategy を再リセットして独立実行
            self._strategy.reset()
            result = self.run(val_data, warmup_bars=min(warmup_bars, len(val_data) // 2))
            results.append(result)

            window_start = window_start + pd.DateOffset(months=step_months)

            # val_end がデータ終端を超えたら終了
            if val_end >= end_ts:
                break

        return results
