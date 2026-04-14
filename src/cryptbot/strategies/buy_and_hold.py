"""Buy & Hold 戦略（ベンチマーク用）。"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import now_jst


class BuyAndHoldStrategy(BaseStrategy):
    """シンプルな Buy & Hold 戦略（ベンチマーク用）。
    ポジションを持っていない場合は BUY、持っている場合は HOLD を返す。
    """

    def __init__(self) -> None:
        super().__init__("buy_and_hold")
        self._has_position: bool = False

    def reset(self) -> None:
        """ポジション保有状態をリセットする。"""
        self._has_position = False

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        last = data.iloc[-1]
        ts_raw = last.get("timestamp") if "timestamp" in data.columns else None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = now_jst()

        if not self._has_position:
            self._has_position = True
            return Signal(Direction.BUY, 1.0, "buy and hold: initial buy", ts)
        return self._hold("buy and hold: holding", ts)
