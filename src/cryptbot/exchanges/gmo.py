"""GMO コイン取引所アダプター（スタブ実装）。"""
from __future__ import annotations

from cryptbot.exchanges.base import BaseExchange, Candle, OrderBook, Ticker


class GmoCoinExchange(BaseExchange):
    """GMO コイン取引所アダプター（将来実装予定のスタブ）。"""

    @property
    def name(self) -> str:
        return "gmo_coin"

    @property
    def supported_pairs(self) -> list[str]:
        return ["btc_jpy"]

    async def get_ticker(self, pair: str) -> Ticker:
        raise NotImplementedError

    async def get_order_book(self, pair: str) -> OrderBook:
        raise NotImplementedError

    async def get_candlesticks(
        self,
        pair: str,
        timeframe: str,
        year: int,
    ) -> list[Candle]:
        raise NotImplementedError

    async def get_assets(self) -> dict[str, float]:
        raise NotImplementedError

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
    ) -> dict:
        raise NotImplementedError

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        raise NotImplementedError

    async def get_open_orders(self, pair: str) -> list[dict]:
        raise NotImplementedError
