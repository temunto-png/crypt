"""移動平均クロス戦略。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import now_jst


class MACrossStrategy(BaseStrategy):
    """移動平均クロス戦略。
    BUY: SMA_short > SMA_long（ゴールデンクロス）
    SELL: SMA_short < SMA_long（デッドクロス）
    HOLD: それ以外
    """

    REQUIRED_COLUMNS = ["sma_short", "sma_long", "timestamp"]

    def __init__(
        self,
        min_ma_diff_pct: float = 0.001,  # 最小MA差分（0.1%）
    ) -> None:
        super().__init__("ma_cross")
        self.min_ma_diff_pct = min_ma_diff_pct

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        # 最低2バー必要（クロス検出のため）
        if len(data) < 2:
            return self._hold("ma_cross: データ不足", now_jst())

        last = data.iloc[-1]
        ts_raw = last.get("timestamp") if "timestamp" in data.columns else None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = now_jst()

        # 必要カラムがない or NaN → HOLD
        missing = [c for c in ["sma_short", "sma_long"] if c not in data.columns]
        if missing:
            return self._hold(f"ma_cross: カラム不足 {missing}", ts)

        cur_short = last["sma_short"]
        cur_long = last["sma_long"]
        prev_short = data.iloc[-2]["sma_short"]
        prev_long = data.iloc[-2]["sma_long"]

        if any(np.isnan(v) for v in [cur_short, cur_long, prev_short, prev_long]):
            return self._hold("ma_cross: NaN", ts)

        # 1. まずクロスを検出
        golden_cross = prev_short < prev_long and cur_short > cur_long
        dead_cross = prev_short > prev_long and cur_short < cur_long

        if not (golden_cross or dead_cross):
            return self._hold("ma_cross: no cross", ts)

        # 2. クロス発生時のみ差分フィルターを適用
        diff_pct = abs(cur_short - cur_long) / cur_long
        if diff_pct < self.min_ma_diff_pct:
            return self._hold("ma_cross: MA差分が小さすぎる", ts)

        # 3. シグナル生成
        if golden_cross:
            return Signal(Direction.BUY, 1.0, "ma_cross: golden cross", ts)
        return Signal(Direction.SELL, 1.0, "ma_cross: dead cross", ts)
