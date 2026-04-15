"""Live Executor — Phase 4 実装。

使用条件: TRADING_MODE=live + --confirm-live CLI フラグの両方が揃った場合のみ有効化。
二重発注防止・PARTIAL_FILLED ライフサイクル・sanitize_error によるセキュリティを実装する。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import BaseExchange, OrderBook
from cryptbot.execution.base import BaseExecutor, ExecutionResult
from cryptbot.utils.logger import sanitize_error
from cryptbot.utils.time_utils import JST


class DuplicateOrderError(RuntimeError):
    """同一 pair にアクティブ注文が既に存在する場合に送出される例外。"""


class LiveExecutor(BaseExecutor):
    """Live 注文実行エグゼキューター（Phase 4）。

    このクラスは TRADING_MODE=live と --confirm-live フラグの両方が
    揃った場合にのみ利用可能にすること。直接インスタンス化しない。
    """

    def __init__(
        self,
        exchange: BaseExchange,
        storage: Storage,
        strategy_name: str = "",
    ) -> None:
        self._exchange = exchange
        self._storage = storage
        self._strategy_name = strategy_name

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
        order_book: OrderBook | None = None,
    ) -> ExecutionResult:
        """Live 注文発行（TOC/TOU-safe 二重発注防止つき）。

        手順:
        1. BEGIN EXCLUSIVE トランザクションでアクティブ注文を確認 + CREATED で挿入
        2. 取引所に注文送信: CREATED → SUBMITTED
        3. 送信失敗時: CREATED → SUBMIT_FAILED（sanitize_error でエラーを記録）

        Returns:
            ExecutionResult（status="SUBMITTED", fill_price/size/fee=0.0）

        Raises:
            DuplicateOrderError: 同一 pair にアクティブ注文が既に存在する場合
        """
        # ステップ 1: EXCLUSIVE トランザクションで二重発注チェック + CREATED 挿入
        now_iso = datetime.now(tz=JST).isoformat()
        try:
            with self._storage.exclusive_transaction() as conn:
                active = conn.execute(
                    """
                    SELECT id FROM orders
                    WHERE pair = ? AND status IN ('CREATED', 'SUBMITTED', 'PARTIAL')
                    """,
                    (pair,),
                ).fetchall()
                if active:
                    raise DuplicateOrderError(
                        f"pair={pair!r} にアクティブ注文が既に存在します（id={active[0]['id']}）。"
                        " 二重発注を防止するためリクエストを拒否しました。"
                    )
                cursor = conn.execute(
                    """
                    INSERT INTO orders
                      (created_at, pair, side, order_type, price, size, status, strategy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?, ?)
                    """,
                    (
                        now_iso,
                        pair,
                        side.upper(),
                        order_type,
                        price,
                        size,
                        self._strategy_name or None,
                        now_iso,
                    ),
                )
                db_order_id: int = cursor.lastrowid  # type: ignore[assignment]
        except DuplicateOrderError:
            raise  # expected flow
        except sqlite3.OperationalError as e:
            # DB lock timeout — no order was created
            raise RuntimeError(
                f"注文の排他ロック取得に失敗しました（タイムアウト）: {sanitize_error(e)}"
            ) from e

        # ステップ 2: 取引所に注文送信
        try:
            result = await self._exchange.place_order(pair, side, order_type, size, price)
            exchange_order_id: str = str(result.get("order_id", ""))

            # CREATED → SUBMITTED
            self._storage.update_order_status(
                db_order_id,
                "SUBMITTED",
                exchange_order_id=exchange_order_id,
            )
            self._storage.insert_order_event(db_order_id, "SUBMITTED")

        except Exception as e:
            # CREATED → SUBMIT_FAILED
            note = sanitize_error(e)
            self._storage.update_order_status(db_order_id, "SUBMIT_FAILED")
            self._storage.insert_order_event(db_order_id, "SUBMIT_FAILED", note=note)
            raise

        return ExecutionResult(
            pair=pair,
            side=side,
            order_type=order_type,
            requested_size=size,
            fill_price=0.0,
            fill_size=0.0,
            fee=0.0,
            slippage_pct=0.0,
            status="SUBMITTED",
            order_id=db_order_id,
        )

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        """DB order_id（str）を受け取り取引所でキャンセルする。

        exchange_order_id が未設定（SUBMIT_FAILED 等）の場合は
        ローカル DB のみ CANCELLED に遷移させる。

        Returns:
            True: キャンセル成功（または未送信でローカルのみ更新）
            False: 取引所側で既に約定済み
        """
        db_order_id = int(order_id)
        exchange_order_id = self._storage.get_order_exchange_id(db_order_id)

        if exchange_order_id is None:
            # 未送信または送信失敗: ローカルのみ更新
            self._storage.update_order_status(db_order_id, "CANCELLED")
            self._storage.insert_order_event(db_order_id, "CANCELLED")
            return True

        try:
            result = await self._exchange.cancel_order(pair, exchange_order_id)
            if result:
                self._storage.update_order_status(db_order_id, "CANCELLED")
                self._storage.insert_order_event(db_order_id, "CANCELLED")
            return result
        except Exception as e:
            self._storage.insert_order_event(
                db_order_id, "ERROR", note=sanitize_error(e)
            )
            raise

    async def get_open_orders(self, pair: str) -> list[dict]:
        """pair のアクティブ注文を DB から返す（取引所 API 呼び出しなし）。"""
        return self._storage.get_active_orders(pair)

    async def handle_partial_fill(
        self,
        pair: str,
        db_order_id: int,
        exchange_order_id: str,
        created_at_iso: str,
        partial_fill_timeout_sec: int,
    ) -> None:
        """PARTIAL_FILLED 注文の状態を1回取得して更新する（ワンショット）。

        LiveEngine のポーリングタスクから定期的に呼ばれる。
        - 全量約定済み（FULLY_FILLED）→ DB を FILLED に遷移
        - タイムアウト超過かつ未約定 → cancel_order() を呼び出し
        - 上記以外 → 何もしない（次回ポーリング待ち）

        Args:
            pair: 取引ペア
            db_order_id: 内部 DB の注文 ID
            exchange_order_id: 取引所側の注文 ID
            created_at_iso: 注文作成時刻（ISO 文字列）
            partial_fill_timeout_sec: タイムアウト秒数（LiveSettings から取得）
        """
        order_data = await self._exchange.get_order(pair, exchange_order_id)
        exchange_status = order_data.get("status", "")

        # bitbank get_order レスポンスのフィールド名:
        # status: "UNFILLED" | "PARTIALLY_FILLED" | "FULLY_FILLED" |
        #         "CANCELED_UNFILLED" | "CANCELED_PARTIALLY_FILLED"
        # executed_amount: 約定済み数量（文字列）
        # average_price: 平均約定価格（文字列、未約定時は "0"）
        if exchange_status == "FULLY_FILLED":
            fill_amount = float(order_data.get("executed_amount", 0))
            avg_price = float(order_data.get("average_price", 0))
            self._storage.update_order_status(
                db_order_id,
                "FILLED",
                fill_price=avg_price,
                fill_size=fill_amount,
            )
            self._storage.insert_order_event(
                db_order_id,
                "FILLED",
                fill_price=avg_price,
                fill_size=fill_amount,
            )
            return

        # タイムアウトチェック: 超過していれば残量キャンセル
        created_at = datetime.fromisoformat(created_at_iso)
        elapsed = (datetime.now(tz=JST) - created_at).total_seconds()
        if elapsed > partial_fill_timeout_sec:
            await self.cancel_order(pair, str(db_order_id))
