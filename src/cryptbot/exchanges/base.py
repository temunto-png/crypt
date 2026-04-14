"""取引所アダプターの抽象基底クラスとデータクラス定義。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Ticker:
    pair: str               # "btc_jpy"
    sell: float             # best ask
    buy: float              # best bid
    high: float             # 24h high
    low: float              # 24h low
    last: float             # last price
    vol: float              # 24h volume
    timestamp: datetime     # JST timezone-aware


@dataclass
class OrderBook:
    pair: str
    asks: list[tuple[float, float]]   # [(price, size), ...]  安値順
    bids: list[tuple[float, float]]   # [(price, size), ...]  高値順
    timestamp: datetime               # JST timezone-aware


@dataclass
class Candle:
    timestamp: datetime   # JST timezone-aware、バーの開始時刻
    open: float
    high: float
    low: float
    close: float
    volume: float


class ExchangeError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw_response = raw_response

    def __str__(self) -> str:
        return self.message


class BaseExchange(ABC):
    """取引所アダプターの抽象基底クラス。

    Public API（認証不要）と Private API（認証必要）に分ける。
    Phase 1 バックテストでは Public API のみ使用する。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """取引所名（例: "bitbank"）"""

    @property
    @abstractmethod
    def supported_pairs(self) -> list[str]:
        """取引可能ペア一覧（例: ["btc_jpy"]）"""

    # --- Public API ---

    @abstractmethod
    async def get_ticker(self, pair: str) -> Ticker:
        """ティッカー情報を取得する"""

    @abstractmethod
    async def get_order_book(self, pair: str) -> OrderBook:
        """板情報（OrderBook）を取得する"""

    @abstractmethod
    async def get_candlesticks(
        self,
        pair: str,
        timeframe: str,
        year: int,
    ) -> list[Candle]:
        """OHLCV ローソク足データを取得する。

        Args:
            pair: 取引ペア（例: "btc_jpy"）
            timeframe: 時間足（例: "1hour"）
            year: 取得する年（例: 2024）

        Returns:
            Candle のリスト（timestamp 昇順）
        """

    # --- Private API（Phase 2 以降で使用） ---

    @abstractmethod
    async def get_assets(self) -> dict[str, float]:
        """残高を取得する。{"jpy": 100000.0, "btc": 0.001} 形式"""

    @abstractmethod
    async def place_order(
        self,
        pair: str,
        side: str,           # "buy" or "sell"
        order_type: str,     # "market" or "limit"
        size: float,
        price: float | None = None,
    ) -> dict:
        """注文を発行する。取引所が返す注文 dict を返す"""

    @abstractmethod
    async def cancel_order(self, pair: str, order_id: str) -> bool:
        """注文をキャンセルする。成功時 True"""

    @abstractmethod
    async def get_open_orders(self, pair: str) -> list[dict]:
        """未約定注文一覧を返す"""
