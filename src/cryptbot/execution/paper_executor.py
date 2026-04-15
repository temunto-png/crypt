"""Paper Executor — Simulator 経由の疑似約定エグゼキューター。

OrderBook が渡された場合は板走査で約定価格を決定する。
OrderBook なし + price ありの場合は固定コストモデル（Taker 0.1%）にフォールバックする。

## 約定記録
- orders テーブルへの記録: fail-open（エラー時はログのみ、約定結果は返す）
- order_id は best-effort（記録失敗時は None）

## 注文状態遷移
  CREATED → SUBMITTED → FILLED（全量約定）
                      → PARTIAL（部分約定）
"""
from __future__ import annotations

import logging
from typing import Literal

from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import OrderBook
from cryptbot.execution.base import BaseExecutor, ExecutionResult
from cryptbot.paper.simulator import FillResult, Simulator

logger = logging.getLogger(__name__)


class PaperExecutor(BaseExecutor):
    """Paper 注文実行エグゼキューター。

    Simulator を使った板情報ベースの疑似約定と、フォールバック用の
    固定コストモデル（price + Taker 0.1%）の2モードをサポートする。
    """

    def __init__(
        self,
        storage: Storage,
        simulator: Simulator,
        strategy_name: str = "",
    ) -> None:
        self._storage = storage
        self._simulator = simulator
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
        """注文を発行して約定結果を返す。

        Args:
            pair: 取引ペア（例: "btc_jpy"）
            side: "buy" または "sell"
            order_type: "market" または "limit"
            size: 注文数量（BTC）
            price: フォールバック価格または指値価格（JPY）
            order_book: 板情報（None の場合は price ベースのフォールバック）

        Returns:
            ExecutionResult

        Raises:
            ValueError: order_book も price も指定されていない場合
        """
        fill = self._compute_fill(
            order_book=order_book,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
        )

        if fill is None:
            # 指値注文が板に届かず不約定（GTC 待ち → paper では即キャンセル）
            return ExecutionResult(
                order_id=None,
                pair=pair,
                side=side,
                order_type=order_type,
                requested_size=size,
                fill_price=price or 0.0,
                fill_size=0.0,
                fee=0.0,
                slippage_pct=0.0,
                status="CANCELLED",
            )

        db_status = "PARTIAL" if fill.is_partial else "FILLED"
        result_status = "PARTIAL_FILLED" if fill.is_partial else "FILLED"

        order_id = self._record_order(
            pair=pair,
            side=side,
            order_type=order_type,
            size=size,
            fill=fill,
            db_status=db_status,
        )

        return ExecutionResult(
            order_id=order_id,
            pair=pair,
            side=side,
            order_type=order_type,
            requested_size=size,
            fill_price=fill.fill_price,
            fill_size=fill.fill_size,
            fee=fill.fee,
            slippage_pct=fill.slippage_pct,
            status=result_status,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _compute_fill(
        self,
        order_book: OrderBook | None,
        side: str,
        order_type: str,
        size: float,
        price: float | None,
    ) -> FillResult | None:
        """約定計算。OrderBook の有無と注文タイプに応じてロジックを選択する。"""
        if order_book is not None:
            if order_type == "market":
                return self._simulator.simulate_market_order(order_book, side, size)  # type: ignore[arg-type]
            elif order_type == "limit":
                if price is None:
                    raise ValueError("limit 注文には price が必要です")
                return self._simulator.simulate_limit_order(order_book, side, size, price)  # type: ignore[arg-type]

        # フォールバック: price ベースの固定コストモデル
        if price is None:
            raise ValueError("order_book と price のどちらか一方は必須です")

        cost = size * price
        fee = cost * Simulator.TAKER_FEE_PCT
        return FillResult(
            fill_price=price,
            fill_size=size,
            fee=fee,
            slippage_pct=0.0,
            is_partial=False,
        )

    def _record_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        fill: FillResult,
        db_status: str,
    ) -> int | None:
        """orders テーブルに約定を記録する（fail-open）。

        エラーが発生してもログのみ記録し、None を返して処理を継続する。
        """
        try:
            order_id = self._storage.insert_order({
                "pair": pair,
                "side": side.upper(),
                "order_type": order_type,
                "size": size,
                "status": "CREATED",
                "fill_price": fill.fill_price,
                "fill_size": fill.fill_size,
                "fee": fill.fee,
                "strategy": self._strategy_name,
            })
            self._storage.update_order_status(
                order_id,
                "SUBMITTED",
                fill_price=fill.fill_price,
                fill_size=fill.fill_size,
            )
            self._storage.update_order_status(
                order_id,
                db_status,
                fill_price=fill.fill_price,
                fill_size=fill.fill_size,
                fee=fill.fee,
            )
            return order_id
        except Exception:
            logger.exception("orders テーブルへの約定記録中にエラーが発生しました")
            return None
