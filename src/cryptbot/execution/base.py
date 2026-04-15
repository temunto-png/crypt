"""Executor 抽象基底クラスと ExecutionResult 定義。

PaperExecutor（疑似約定）と LiveExecutor（実注文）が共通で実装するインターフェース。
注文の発行・約定結果の取得のみを責務とする。ポートフォリオ更新は呼び出し側の責務。

## 設計上の注意
- BaseExecutor は注文の発行と約定結果の取得のみを担う
- ポートフォリオ状態の更新は RiskManager / PaperEngine 側が行う
- LiveExecutor は TRADING_MODE=live + --confirm-live フラグが揃った場合のみ有効化する
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from cryptbot.exchanges.base import OrderBook


@dataclass
class ExecutionResult:
    """注文の約定結果。"""

    pair: str
    side: str               # "buy" | "sell"
    order_type: str         # "market" | "limit"
    requested_size: float
    fill_price: float
    fill_size: float
    fee: float
    slippage_pct: float
    status: str             # "FILLED" | "PARTIAL_FILLED" | "CANCELLED" | "FAILED"
    order_id: int | None = None
    error: str | None = None


class BaseExecutor(ABC):
    """注文実行エグゼキューターの抽象基底クラス。

    PaperExecutor と LiveExecutor が実装する共通インターフェース。
    """

    @abstractmethod
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
            price: 指値価格（limit 注文時に指定、market では無視）
            order_book: 板情報（疑似約定に使用。None の場合は price ベースのフォールバック）

        Returns:
            ExecutionResult
        """
