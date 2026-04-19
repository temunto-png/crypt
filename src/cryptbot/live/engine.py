"""Live trading エンジン（Phase 4）。

asyncio 永続プロセスとして動作する。cron からは呼ばない。
- バーループ: 次のバー境界まで待機 → 1バー処理
- アクティブ注文ポーラー: 定期的に SUBMITTED/PARTIAL 注文を確認

起動方法:
    asyncio.run(engine.run())
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import math  # F3: _submit_pending() での BUY サイズ計算に使用
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

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
    from cryptbot.data.ohlcv_updater import OhlcvUpdater
    from cryptbot.models.base import BaseMLModel
    from cryptbot.models.degradation_detector import DegradationDetector
from cryptbot.config.settings import LiveSettings
from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import ExchangeError
from cryptbot.execution.live_executor import DuplicateOrderError, LiveExecutor
from cryptbot.live.gate import LiveGateError
from cryptbot.live.state import LiveState, LiveStateStore
from cryptbot.risk.manager import PortfolioState, RiskManager
from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.strategies.regime import RegimeDetector
from cryptbot.utils.time_utils import JST, now_jst

logger = logging.getLogger(__name__)

_MAX_RECENT_RETURNS = 50
_MAX_CONSECUTIVE_UPDATE_FAILURES = 3

# タイムフレーム文字列 → 秒数マッピング
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1hour": 3600,
    "4hour": 14400,
    "8hour": 28800,
    "12hour": 43200,
    "1day": 86400,
}


@dataclass
class LiveRunResult:
    """run_one_bar() の実行結果。"""

    bar_timestamp: datetime
    signal: Signal | None
    order_submitted: bool   # この呼び出しで注文を発行したか
    portfolio: PortfolioState
    cvar_action: str
    skipped: bool = False
    skip_reason: str = ""


class LiveEngine:
    """Live trading エンジン（asyncio 永続プロセス）。

    使用前に phase_4_gate() で起動条件を確認すること。
    直接インスタンス化せず、main._run_live() 経由で使用する。
    """

    TAKER_FEE_PCT = 0.001
    MIN_BTC_ORDER = 0.0001

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        state_store: LiveStateStore,
        storage: Storage,
        executor: LiveExecutor,
        settings: LiveSettings,
        regime_detector: RegimeDetector | None = None,
        ml_model: "BaseMLModel | None" = None,
        degradation_detector: "DegradationDetector | None" = None,
        ml_confidence_threshold: float = 0.6,
        ohlcv_updater: "OhlcvUpdater | None" = None,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._state_store = state_store
        self._storage = storage
        self._executor = executor
        self._settings = settings
        self._momentum_window: int = settings.momentum_window
        self._regime_detector = regime_detector
        self._ml_model = ml_model
        self._degradation_detector = degradation_detector
        self._ml_confidence_threshold = ml_confidence_threshold
        self._ohlcv_updater = ohlcv_updater

    # ------------------------------------------------------------------
    # パブリック API
    # ------------------------------------------------------------------

    async def run(self, assets: dict[str, float] | None = None) -> None:
        """メインループ。asyncio.run(engine.run()) から呼ぶ。

        Args:
            assets: verify_api_permissions() から取得した残高辞書。
                    {"jpy": float, "btc": float} 形式。
                    None の場合は残高同期をスキップ（テスト用途）。

        KeyboardInterrupt / asyncio.CancelledError で graceful shutdown。
        bar_loop または partial_fill_loop が例外を送出した場合は両タスクを停止して伝播。
        """
        await self._recover_on_restart()
        await self._sync_initial_balance(assets)

        bar_task = asyncio.create_task(self._bar_loop(), name="bar_loop")
        partial_task = asyncio.create_task(
            self._partial_fill_loop(), name="partial_fill_loop"
        )

        try:
            done, _ = await asyncio.wait(
                {bar_task, partial_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            # 完了したタスクに例外があれば再送出
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    raise t.exception()  # type: ignore[misc]

        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("LiveEngine: シャットダウン要求を受信")

        finally:
            bar_task.cancel()
            partial_task.cancel()
            await asyncio.gather(bar_task, partial_task, return_exceptions=True)
            logger.info("LiveEngine: 終了")

    async def run_one_bar(self, data: pd.DataFrame) -> LiveRunResult:
        """最新バー 1 本分を処理して状態を保存する。

        外部（テスト・bar_loop）から呼ばれるメインロジック。

        Args:
            data: ウォームアップ分を含む OHLCV DataFrame。
                  data.iloc[-1] が今回処理する最新バー。
                  最低 (warmup_bars + 1) 本が必要。

        Returns:
            LiveRunResult（skipped=True の場合は同一バーの二重処理）
        """
        if len(data) == 0:
            return LiveRunResult(
                bar_timestamp=now_jst(),
                signal=None,
                order_submitted=False,
                portfolio=self._make_initial_portfolio(0.0),
                cvar_action="normal",
                skipped=True,
                skip_reason="empty_data",
            )

        current_bar = data.iloc[-1]
        current_bar_ts = _extract_timestamp(current_bar)
        current_date = current_bar_ts.date()

        # ---- 状態を復元 ----
        live_state = self._state_store.load()
        if live_state is None:
            portfolio, pending_signal, pending_cvar_action, recent_returns, entry_time, entry_price = (
                self._init_fresh_state()
            )
        else:
            # 同一バーの二重処理防止
            if live_state.last_processed_bar_ts is not None:
                try:
                    last_ts = datetime.fromisoformat(live_state.last_processed_bar_ts)
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=JST)
                    if last_ts == current_bar_ts:
                        logger.info(
                            "live engine: 同一バー (%s) は処理済みのためスキップ", current_bar_ts
                        )
                        return LiveRunResult(
                            bar_timestamp=current_bar_ts,
                            signal=None,
                            order_submitted=False,
                            portfolio=LiveStateStore.to_portfolio_state(live_state),
                            cvar_action=live_state.pending_cvar_action,
                            skipped=True,
                            skip_reason="already_processed",
                        )
                except ValueError:
                    pass

            portfolio = LiveStateStore.to_portfolio_state(live_state)
            pending_signal = LiveStateStore.extract_pending_signal(live_state)
            pending_cvar_action = live_state.pending_cvar_action
            recent_returns = LiveStateStore.extract_recent_returns(live_state)
            entry_time = LiveStateStore.extract_position_entry_time(live_state)
            entry_price = live_state.position_entry_price

        # ---- close_price と前バー価格を早期抽出 ----
        close_price = float(current_bar["close"])
        existing_last_price = live_state.last_price if live_state is not None else 0.0

        # ---- Kill switch チェック ----
        if self._risk_manager._kill_switch.active:
            logger.warning("live engine: kill switch が active のためスキップ")
            self._save_state(
                portfolio, None, "normal", recent_returns,
                entry_time, entry_price, current_bar_ts,
                last_price=existing_last_price,
            )
            return LiveRunResult(
                bar_timestamp=current_bar_ts,
                signal=None,
                order_submitted=False,
                portfolio=portfolio,
                cvar_action="normal",
                skipped=True,
                skip_reason="kill_switch_active",
            )
        order_submitted = False

        # ---- 前回バーの pending_signal を今バーの open 価格で発注 ----
        if pending_signal is not None:
            order_submitted = await self._submit_pending(
                pending_signal=pending_signal,
                cvar_action=pending_cvar_action,
                current_bar_ts=current_bar_ts,
                portfolio=portfolio,
                current_price=existing_last_price,
            )

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
                mark_bal = calc_mark_balance(portfolio, close_price, self.TAKER_FEE_PCT, 0.0)
                self._risk_manager.check_circuit_breakers(portfolio, mark_bal)
                self._save_state(
                    portfolio, new_pending, "normal", recent_returns,
                    entry_time, entry_price, current_bar_ts,
                    last_price=close_price,
                )
                return LiveRunResult(
                    bar_timestamp=current_bar_ts,
                    signal=new_pending,
                    order_submitted=order_submitted,
                    portfolio=portfolio,
                    cvar_action="normal",
                )

        # ---- per-bar リスク制御: circuit breakers ----
        mark_balance = calc_mark_balance(portfolio, close_price, self.TAKER_FEE_PCT, 0.0)
        self._risk_manager.check_circuit_breakers(portfolio, mark_balance)

        if self._risk_manager._kill_switch.active:
            self._save_state(
                portfolio, None, "normal", recent_returns,
                entry_time, entry_price, current_bar_ts,
                last_price=close_price,
            )
            return LiveRunResult(
                bar_timestamp=current_bar_ts,
                signal=None,
                order_submitted=order_submitted,
                portfolio=portfolio,
                cvar_action="normal",
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
            last_price=close_price,
        )

        return LiveRunResult(
            bar_timestamp=current_bar_ts,
            signal=new_signal,
            order_submitted=order_submitted,
            portfolio=portfolio,
            cvar_action=cvar_action,
        )

    # ------------------------------------------------------------------
    # 非同期ループ（run() から起動）
    # ------------------------------------------------------------------

    async def _bar_loop(self) -> None:
        """バー確定を待って run_one_bar() を呼ぶ無限ループ。"""
        consecutive_update_failures = 0
        while True:
            await self._wait_for_next_bar()
            if self._ohlcv_updater is not None:
                ok = await self._ohlcv_updater.update_latest()
                if not ok:
                    consecutive_update_failures += 1
                    logger.warning(
                        "live engine: OHLCV 更新失敗 (%d 回目)",
                        consecutive_update_failures,
                    )
                    if consecutive_update_failures >= _MAX_CONSECUTIVE_UPDATE_FAILURES:
                        raise LiveGateError(
                            f"OHLCV 連続更新失敗: {consecutive_update_failures} 回"
                        )
                    continue
                consecutive_update_failures = 0
            data = self._load_ohlcv()
            if data is not None:
                await self.run_one_bar(data)
            else:
                logger.warning("live engine: OHLCV データを取得できませんでした")

    async def _partial_fill_loop(self) -> None:
        """SUBMITTED/PARTIAL 注文を定期ポーリングする無限ループ。"""
        while True:
            await asyncio.sleep(self._settings.partial_fill_poll_interval_sec)
            await self._poll_active_orders()

    async def _poll_active_orders(self) -> None:
        """SUBMITTED / PARTIAL ステータスの注文を1件ずつ確認してハンドル。"""
        try:
            active_orders = self._storage.get_active_orders(self._settings.pair)
        except Exception:
            logger.exception("live engine: アクティブ注文の取得に失敗")
            return

        orders_to_poll = [
            o for o in active_orders if o["status"] in ("SUBMITTED", "PARTIAL")
        ]
        for order in orders_to_poll:
            try:
                result = await self._executor.handle_partial_fill(
                    pair=self._settings.pair,
                    db_order_id=int(order["id"]),
                    exchange_order_id=str(order.get("exchange_order_id", "")),
                    created_at_iso=str(order["created_at"]),
                    partial_fill_timeout_sec=self._settings.order_timeout_sec,
                    side=str(order.get("side", "")),
                    current_db_status=str(order.get("status", "SUBMITTED")),
                )
            except ExchangeError:
                # 取引所通信の一時失敗 → ログして次の注文へ
                logger.exception(
                    "live engine: 注文 (id=%s) の取引所照合に失敗（次回ポーリングで再試行）",
                    order["id"],
                )
                continue
            except Exception:
                # DB・状態永続化の失敗 → 内部状態が市場実態と乖離する可能性があるため伝播
                logger.exception(
                    "live engine: 注文 (id=%s) のDB更新に失敗（fail-close）", order["id"]
                )
                raise

            if result.terminal and result.status == "FULLY_FILLED":
                try:
                    state = self._state_store.load()
                    if state is None:
                        raise LiveGateError(
                            f"約定同期: LiveState が存在しません (order_id={result.db_order_id})"
                        )
                    side = result.side.upper()
                    if side == "BUY":
                        state.position_size = result.executed_amount
                        state.entry_price = result.average_price
                        state.position_entry_price = result.average_price
                        state.position_entry_time = now_jst().isoformat()
                    elif side == "SELL":
                        state.position_size = 0.0
                        state.entry_price = 0.0
                        state.position_entry_time = None
                        state.position_entry_price = 0.0
                    state.updated_at = now_jst().isoformat()
                    self._state_store.save(state)
                    logger.info(
                        "live engine: 約定同期完了 order_id=%s side=%s amount=%.4f price=%.0f",
                        result.db_order_id, side, result.executed_amount, result.average_price,
                    )
                except LiveGateError:
                    raise
                except Exception:
                    raise LiveGateError(
                        f"約定同期: LiveState の更新に失敗しました (order_id={result.db_order_id})"
                    )

            elif (
                result.terminal
                and result.status == "CANCELED_PARTIALLY_FILLED"
                and result.executed_amount > 0
            ):
                try:
                    state = self._state_store.load()
                    if state is None:
                        raise LiveGateError(
                            f"約定同期: LiveState が存在しません (order_id={result.db_order_id})"
                        )
                    side = result.side.upper()
                    if side == "BUY":
                        state.position_size = result.executed_amount
                        state.entry_price = result.average_price
                        state.position_entry_price = result.average_price
                        state.position_entry_time = now_jst().isoformat()
                    elif side == "SELL":
                        state.position_size = max(
                            0.0, state.position_size - result.executed_amount
                        )
                        if state.position_size == 0.0:
                            state.entry_price = 0.0
                            state.position_entry_price = 0.0
                            state.position_entry_time = None
                    state.updated_at = now_jst().isoformat()
                    self._state_store.save(state)
                    logger.warning(
                        "live engine: キャンセル部分約定 LiveState 更新 order_id=%s side=%s amount=%.4f price=%.0f",
                        result.db_order_id, side, result.executed_amount, result.average_price,
                    )
                except LiveGateError:
                    raise
                except Exception:
                    raise LiveGateError(
                        f"約定同期: LiveState の更新に失敗しました (order_id={result.db_order_id})"
                    )

            elif (
                result.terminal
                and result.status == "TIMEOUT_CANCELLED"
                and result.executed_amount > 0
            ):
                # タイムアウトキャンセル時に部分約定分を LiveState に反映する。
                # BUY が部分約定後にタイムアウトキャンセルされた場合、
                # 処理しないと position_size=0 のまま BTC が取引所に残る。
                try:
                    state = self._state_store.load()
                    if state is None:
                        raise LiveGateError(
                            f"約定同期: LiveState が存在しません (order_id={result.db_order_id})"
                        )
                    side = result.side.upper()
                    if side == "BUY":
                        state.position_size = result.executed_amount
                        state.entry_price = result.average_price
                        state.position_entry_price = result.average_price
                        state.position_entry_time = now_jst().isoformat()
                    elif side == "SELL":
                        state.position_size = max(
                            0.0, state.position_size - result.executed_amount
                        )
                        if state.position_size == 0.0:
                            state.entry_price = 0.0
                            state.position_entry_price = 0.0
                            state.position_entry_time = None
                    state.updated_at = now_jst().isoformat()
                    self._state_store.save(state)
                    logger.warning(
                        "live engine: タイムアウトキャンセル部分約定 LiveState 更新 order_id=%s side=%s amount=%.4f price=%.0f",
                        result.db_order_id, side, result.executed_amount, result.average_price,
                    )
                except LiveGateError:
                    raise
                except Exception:
                    raise LiveGateError(
                        f"約定同期: LiveState の更新に失敗しました (order_id={result.db_order_id})"
                    )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    async def _recover_on_restart(self) -> None:
        """再起動時に未解決注文をリカバリーする。run() から一度だけ呼ぶ。"""
        logger.info("live engine: 起動リカバリー開始")
        self._cancel_orphan_orders()
        await self._poll_active_orders()
        logger.info("live engine: 起動リカバリー完了")

    def _cancel_orphan_orders(self) -> None:
        """exchange_order_id=NULL の CREATED 注文をローカル CANCELLED に遷移する。

        取引所への送信前にクラッシュした注文が DB に残るケースを処理する。
        取引所には届いていないため、ローカルで安全にキャンセル扱いにする。
        """
        try:
            active = self._storage.get_active_orders(self._settings.pair)
        except Exception:
            logger.exception("live engine: orphan 注文取得に失敗（スキップ）")
            return

        orphans = [
            o for o in active
            if o["status"] == "CREATED" and not o.get("exchange_order_id")
        ]
        for order in orphans:
            try:
                self._storage.update_order_status(int(order["id"]), "CANCELLED")
                self._storage.insert_order_event(
                    int(order["id"]), "CANCELLED", note="startup_orphan_recovery"
                )
                logger.warning(
                    "live engine: orphan 注文をローカルキャンセル (id=%s)", order["id"]
                )
            except Exception:
                logger.exception(
                    "live engine: orphan 注文キャンセル失敗 (id=%s)、スキップ", order["id"]
                )

    async def _submit_pending(
        self,
        pending_signal: Signal,
        cvar_action: str,
        current_bar_ts: datetime,
        portfolio: PortfolioState,
        current_price: float,
    ) -> bool:
        """pending_signal を取引所に発注する。

        Returns:
            True: 発注成功。False: 発注スキップ（重複・サイズ不足等）。
        """
        pair = self._settings.pair

        if pending_signal.direction == Direction.HOLD:
            return False

        side = "buy" if pending_signal.direction == Direction.BUY else "sell"

        if pending_signal.direction == Direction.BUY:
            raw_size = self._risk_manager.calculate_position_size(portfolio, current_price)
            size_multiplier = 0.5 if cvar_action == "half" else 1.0
            size = round(math.floor(raw_size * size_multiplier / 0.0001) * 0.0001, 4)
            if size < self.MIN_BTC_ORDER:
                logger.warning("live engine: BUY サイズ不足でスキップ (%.4f)", size)
                return False
        else:  # SELL
            size = portfolio.position_size
            if size < self.MIN_BTC_ORDER:
                logger.warning(
                    "live engine: SELL ポジションなしのためスキップ (%.4f)", size
                )
                return False

        try:
            result = await self._executor.place_order(
                pair=pair,
                side=side,
                order_type="market",
                size=size,
            )
        except DuplicateOrderError:
            logger.warning(
                "live engine: pair=%s に既存のアクティブ注文があるため発注をスキップ", pair
            )
            return False
        except Exception:
            logger.exception("live engine: 注文発行中に予期しないエラーが発生")
            return False

        logger.info(
            "live engine: 注文発行 pair=%s side=%s size=%.4f order_id=%s status=%s",
            pair, side, size, result.order_id, result.status,
        )

        try:
            self._storage.insert_audit_log(
                "order_submitted",
                strategy=self._strategy.name,
                direction=side.upper(),
                signal_confidence=pending_signal.confidence,
                signal_reason=pending_signal.reason,
            )
        except Exception:
            logger.exception(
                "live engine: order_submitted 監査ログの記録に失敗 (order_id=%s) — 停止します",
                result.order_id,
            )
            raise LiveGateError(
                f"order_submitted 監査ログの記録に失敗しました (order_id={result.order_id})"
            )

        return True

    def _load_ohlcv(self) -> pd.DataFrame | None:
        """ウォームアップ込みの OHLCV データを Storage から取得し、特徴量を付与する。"""
        from cryptbot.data.normalizer import normalize

        needed = self._settings.warmup_bars + 1
        try:
            data = self._storage.load_ohlcv(
                pair=self._settings.pair,
                timeframe=self._settings.timeframe,
                verify=True,
                fail_closed=True,
            )
            if len(data) < needed:
                logger.warning(
                    "live engine: OHLCV データ不足 (%d/%d 本)", len(data), needed
                )
                return None
            data = data.tail(needed).reset_index(drop=True)
            # 特徴量生成（normalize しないと strategy が HOLD 固定になる）
            data = normalize(data, momentum_window=self._momentum_window)
            return data
        except Exception:
            logger.exception("live engine: OHLCV データの読み込みに失敗")
            return None

    async def _wait_for_next_bar(self) -> None:
        """次のバー境界（timeframe の次の :00）まで待機する。"""
        tf_seconds = self._resolve_timeframe_seconds()
        now_ts = now_jst().timestamp()
        next_bar_ts = (now_ts // tf_seconds + 1) * tf_seconds
        wait_seconds = next_bar_ts - now_ts
        logger.debug("live engine: 次のバーまで %.1f 秒待機", wait_seconds)
        await asyncio.sleep(wait_seconds)

    def _resolve_timeframe_seconds(self) -> int:
        """timeframe 文字列を秒数に変換する。未知の値は 3600 にフォールバック。"""
        return _TIMEFRAME_SECONDS.get(self._settings.timeframe, 3600)

    def _record_signal(self, signal: Signal, portfolio: PortfolioState) -> None:
        """シグナルを audit_log に記録する。

        BUY シグナルの場合のみ fail-closed（例外を再 raise）。
        SELL / HOLD は fail-open（取引継続を優先）。
        """
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
            logger.exception("live engine: シグナル記録中にエラーが発生しました")
            if signal.direction == Direction.BUY:
                raise  # BUY 前の証跡欠落は取引停止

    def _save_state(
        self,
        portfolio: PortfolioState,
        pending_signal: Signal | None,
        pending_cvar_action: str,
        recent_returns: list[float],
        entry_time: datetime | None,
        entry_price: float,
        bar_ts: datetime,
        last_price: float = 0.0,
    ) -> None:
        state = LiveStateStore.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=pending_signal,
            pending_cvar_action=pending_cvar_action,
            recent_trade_returns=recent_returns,
            position_entry_time=entry_time,
            position_entry_price=entry_price,
            last_processed_bar_ts=bar_ts,
            last_price=last_price,
        )
        self._state_store.save(state)

    def _make_initial_portfolio(self, balance: float) -> PortfolioState:
        today = now_jst().date()
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

    def _init_fresh_state(
        self,
        balance: float = 0.0,
    ) -> tuple[PortfolioState, Signal | None, str, list[float], datetime | None, float]:
        """初回起動時の状態を生成する。"""
        portfolio = self._make_initial_portfolio(balance)
        return portfolio, None, "normal", [], None, 0.0

    async def _sync_initial_balance(self, assets: dict[str, float] | None) -> None:
        """live 起動時に取引所の実残高を LiveState に同期する。

        - assets=None: スキップ（テスト用途）
        - 初回起動（LiveState なし）: assets["jpy"] を balance として初期 LiveState を作成
        - 再起動（既存 LiveState あり）: balance_sync_tolerance_pct で差分検証し、許容内なら上書き
        """
        if assets is None:
            return

        jpy = assets.get("jpy", 0.0)
        btc = assets.get("btc", 0.0)
        existing = self._state_store.load()

        if existing is None:
            if btc >= self._settings.min_order_size_btc:
                raise LiveGateError(
                    f"初回起動時に BTC 残高 {btc:.6f} BTC を検出しました。"
                    f"既存ポジションが LiveState に記録されていません。"
                    f"エントリー価格を確認し、手動で LiveState を修正後に再起動してください。"
                )
            portfolio = self._make_initial_portfolio(jpy)
            state = LiveStateStore.from_portfolio_state(
                portfolio=portfolio,
                pending_signal=None,
                pending_cvar_action="normal",
                recent_trade_returns=[],
                position_entry_time=None,
                position_entry_price=0.0,
                last_processed_bar_ts=None,
                last_price=0.0,
            )
            self._state_store.save(state)
            logger.info("live engine: 初回起動 balance=%.0f JPY", jpy)
        else:
            if existing.balance > 0:  # balance=0 の場合はゼロ除算を回避し無条件で上書き
                diff_pct = abs(jpy - existing.balance) / existing.balance
                if diff_pct > self._settings.balance_sync_tolerance_pct:
                    raise LiveGateError(
                        f"実残高 ({jpy:.0f} JPY) と内部残高 ({existing.balance:.0f} JPY) の乖離が "
                        f"許容差 ({self._settings.balance_sync_tolerance_pct:.1%}) を超えています "
                        f"(乖離率: {diff_pct:.1%})。手動確認後に再起動してください。"
                    )
            # BTC 残高の照合
            if existing.position_size < self._settings.min_order_size_btc:
                # LiveState はノーポジション
                if btc >= self._settings.min_order_size_btc:
                    raise LiveGateError(
                        f"再起動: BTC 残高 {btc:.6f} が存在しますが LiveState はノーポジションです。"
                        f"手動確認後に再起動してください。"
                    )
            else:
                # LiveState はポジションあり
                if btc < self._settings.min_order_size_btc:
                    raise LiveGateError(
                        f"再起動: LiveState には {existing.position_size:.6f} BTC のポジションがありますが、"
                        f"実口座の BTC 残高 ({btc:.6f}) が最小注文数量を下回っています。"
                        f"手動確認後に再起動してください。"
                    )
                # 数量の乖離チェック
                btc_diff_pct = abs(btc - existing.position_size) / existing.position_size
                if btc_diff_pct > self._settings.balance_sync_tolerance_pct:
                    raise LiveGateError(
                        f"再起動: 実 BTC 残高 ({btc:.6f}) と LiveState の position_size "
                        f"({existing.position_size:.6f}) の乖離 ({btc_diff_pct:.1%}) が "
                        f"許容差 ({self._settings.balance_sync_tolerance_pct:.1%}) を超えています。"
                        f"手動確認後に再起動してください。"
                    )
            old_balance = existing.balance
            existing.balance = jpy
            if jpy > existing.peak_balance:
                existing.peak_balance = jpy
            existing.updated_at = now_jst().isoformat()
            self._state_store.save(existing)
            logger.info(
                "live engine: 再起動 残高同期 internal=%.0f → actual=%.0f JPY",
                old_balance, jpy,
            )
