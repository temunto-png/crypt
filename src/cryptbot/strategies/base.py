"""戦略の抽象基底クラスとデータクラス定義。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd

from cryptbot.utils.time_utils import JST


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    direction: Direction
    confidence: float      # 0.0 - 1.0
    reason: str
    timestamp: datetime    # JST timezone-aware


@dataclass
class MarketState:
    """市場状態（リスクチェック・ノートレード判断に使用）"""
    spread_pct: float          # bid/ask スプレッド [%]
    bid_depth_btc: float       # best bid 付近の板厚 [BTC]
    volatility_1h_pct: float   # 1時間変動率 [%]
    timestamp: datetime        # JST timezone-aware


class BaseStrategy(ABC):
    """戦略の抽象基底クラス。

    全戦略は generate_signal() を実装する。
    ノートレード判断は should_skip() で行う（デフォルト実装あり）。
    """

    # should_skip のデフォルト閾値
    MAX_SPREAD_PCT: float = 0.5
    MIN_BID_DEPTH_BTC: float = 0.01
    MAX_VOLATILITY_1H_PCT: float = 5.0

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """現在のデータからシグナルを生成する。

        Args:
            data: normalize() 済みの OHLCV + テクニカル指標 DataFrame
                  最低でも必要な期間分のデータが含まれていること

        Returns:
            Signal (direction, confidence, reason, timestamp)
        """

    def reset(self) -> None:
        """戦略の内部状態をリセットする。

        walk-forward の train/val 各ウィンドウ開始前、および run() のウォームアップ終了時に
        呼ばれる。デフォルト実装は no-op（ステートレス戦略は実装不要）。
        stateful な戦略はオーバーライドして内部状態をリセットすること。
        """

    def should_skip(self, market_state: MarketState) -> tuple[bool, str]:
        """市場環境によるノートレード判断。

        Returns:
            (skip: bool, reason: str)

        デフォルト実装:
        - spread_pct > 0.5% → skip
        - bid_depth_btc < 0.01 → skip
        - volatility_1h_pct > 5.0% → skip
        """
        if market_state.spread_pct > self.MAX_SPREAD_PCT:
            return True, f"スプレッド過大: {market_state.spread_pct:.3f}% > {self.MAX_SPREAD_PCT}%"
        if market_state.bid_depth_btc < self.MIN_BID_DEPTH_BTC:
            return True, f"板厚不足: {market_state.bid_depth_btc:.4f} BTC < {self.MIN_BID_DEPTH_BTC} BTC"
        if market_state.volatility_1h_pct > self.MAX_VOLATILITY_1H_PCT:
            return True, f"ボラティリティ過大: {market_state.volatility_1h_pct:.2f}% > {self.MAX_VOLATILITY_1H_PCT}%"
        return False, ""

    def _hold(self, reason: str, timestamp: datetime) -> Signal:
        """HOLD シグナルを生成するヘルパー。"""
        return Signal(
            direction=Direction.HOLD,
            confidence=0.0,
            reason=reason,
            timestamp=timestamp,
        )
