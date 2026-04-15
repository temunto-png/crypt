"""板情報ベースの疑似約定シミュレーター。

PaperExecutor から呼ばれる純粋ユーティリティ。PaperEngine とは独立しており、
OrderBook を直接受け取って約定価格・手数料・スリッページを計算する。

## 成行注文（simulate_market_order）
  - BUY: asks を安値順に走査してVWAP 約定
  - SELL: bids を高値順に走査してVWAP 約定
  - 板の深さが不足する場合は部分約定（is_partial=True）

## 指値注文（simulate_limit_order）
  - BUY: best_ask < limit_price のとき約定（同値は未約定扱い）
  - SELL: best_bid > limit_price のとき約定（同値は未約定扱い）
  - 約定する場合は成行注文と同じ板走査ロジックを適用
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cryptbot.exchanges.base import OrderBook


@dataclass(frozen=True)
class FillResult:
    """疑似約定の結果。"""

    fill_price: float     # VWAP 約定価格（JPY/BTC）
    fill_size: float      # 約定量（BTC）
    fee: float            # 手数料（JPY）
    slippage_pct: float   # スリッページ率（対 mid price の乖離率）
    is_partial: bool      # 部分約定フラグ（板の深さ不足）


class Simulator:
    """板情報ベースの疑似約定シミュレーター。

    インスタンスは状態を持たない。同じ引数を与えれば常に同じ結果を返す。
    """

    TAKER_FEE_PCT: float = 0.001   # 0.1%
    MAKER_FEE_PCT: float = 0.0     # 0%（指値約定時）

    def simulate_market_order(
        self,
        order_book: OrderBook,
        side: Literal["buy", "sell"],
        size: float,
    ) -> FillResult:
        """成行注文を板走査で疑似約定する。

        Args:
            order_book: 板情報。asks は安値順、bids は高値順であること。
            side: "buy"（asks を走査）または "sell"（bids を走査）
            size: 注文数量（BTC）

        Returns:
            FillResult。板の深さが size に満たない場合は is_partial=True。
        """
        if size <= 0:
            raise ValueError(f"size は正の数である必要があります: {size}")

        levels = order_book.asks if side == "buy" else order_book.bids

        # mid price（スリッページ計算の基準）
        mid_price = self._calc_mid_price(order_book)

        remaining = size
        total_cost = 0.0
        total_filled = 0.0

        for level_price, level_size in levels:
            if remaining <= 0:
                break
            take = min(remaining, level_size)
            total_cost += take * level_price
            total_filled += take
            remaining -= take

        is_partial = remaining > 1e-10  # 浮動小数点誤差を考慮

        if total_filled == 0:
            # 板が空の場合
            best_price = levels[0][0] if levels else 0.0
            return FillResult(
                fill_price=best_price,
                fill_size=0.0,
                fee=0.0,
                slippage_pct=0.0,
                is_partial=True,
            )

        fill_price = total_cost / total_filled  # VWAP
        fee = total_cost * self.TAKER_FEE_PCT
        slippage_pct = (
            abs(fill_price - mid_price) / mid_price if mid_price > 0 else 0.0
        )

        return FillResult(
            fill_price=fill_price,
            fill_size=total_filled,
            fee=fee,
            slippage_pct=slippage_pct,
            is_partial=is_partial,
        )

    def simulate_limit_order(
        self,
        order_book: OrderBook,
        side: Literal["buy", "sell"],
        size: float,
        limit_price: float,
    ) -> FillResult | None:
        """指値注文の即時約定可否を判定し、約定する場合は FillResult を返す。

        約定条件（同値は未約定扱い）:
          - BUY: best_ask < limit_price
          - SELL: best_bid > limit_price

        約定しない場合（GTC 待ち状態）は None を返す。
        Paper trade では None 返却 = キャンセル扱いとする。

        Args:
            order_book: 板情報
            side: "buy" または "sell"
            size: 注文数量（BTC）
            limit_price: 指値価格（JPY）

        Returns:
            FillResult（約定時）または None（不約定時）
        """
        if size <= 0:
            raise ValueError(f"size は正の数である必要があります: {size}")

        if side == "buy":
            if not order_book.asks:
                return None
            best_ask = order_book.asks[0][0]
            # 同値は未約定扱い
            if limit_price <= best_ask:
                return None
            # 指値より安い asks の範囲内で板走査
            filtered_levels = [
                (p, s) for p, s in order_book.asks if p < limit_price
            ]
        else:  # sell
            if not order_book.bids:
                return None
            best_bid = order_book.bids[0][0]
            # 同値は未約定扱い
            if limit_price >= best_bid:
                return None
            filtered_levels = [
                (p, s) for p, s in order_book.bids if p > limit_price
            ]

        if not filtered_levels:
            return None

        # 指値範囲内の板のみで成行と同じ走査
        filtered_book = OrderBook(
            pair=order_book.pair,
            asks=filtered_levels if side == "buy" else [],
            bids=filtered_levels if side == "sell" else [],
            timestamp=order_book.timestamp,
        )
        result = self.simulate_market_order(filtered_book, side, size)
        # 指値注文が約定した場合は Maker 扱い（手数料 0%）
        return FillResult(
            fill_price=result.fill_price,
            fill_size=result.fill_size,
            fee=0.0,  # Maker 手数料無料
            slippage_pct=result.slippage_pct,
            is_partial=result.is_partial,
        )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_mid_price(order_book: OrderBook) -> float:
        """best ask と best bid の中値を返す。片側が空の場合は 0.0。"""
        if order_book.asks and order_book.bids:
            return (order_book.asks[0][0] + order_book.bids[0][0]) / 2
        return 0.0
