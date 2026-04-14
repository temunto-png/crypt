"""ボラティリティフィルター付き MA Cross 戦略。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from cryptbot.strategies.base import BaseStrategy, Signal
from cryptbot.strategies.ma_cross import MACrossStrategy
from cryptbot.utils.time_utils import now_jst


class VolatilityFilterStrategy(BaseStrategy):
    """ボラティリティフィルター付き MA Cross。
    ATR が過去20期間中央値 × atr_multiplier を超える場合のみ MA Cross シグナルを有効にする。
    """

    REQUIRED_COLUMNS = ["atr", "sma_short", "sma_long", "timestamp"]

    def __init__(
        self,
        min_ma_diff_pct: float = 0.001,
        atr_multiplier: float = 1.0,
    ) -> None:
        super().__init__("volatility_filter")
        self._ma_cross = MACrossStrategy(min_ma_diff_pct=min_ma_diff_pct)
        self.atr_multiplier = atr_multiplier

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        last = data.iloc[-1]
        ts_raw = last.get("timestamp") if "timestamp" in data.columns else None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = now_jst()

        if "atr" not in data.columns:
            return self._hold("volatility_filter: atr カラム不足", ts)

        # 末尾のATRがNaN → HOLD
        current_atr = last["atr"]
        if np.isnan(current_atr):
            return self._hold("volatility_filter: ATR NaN", ts)

        atr_values = data["atr"].dropna()
        if atr_values.empty:
            return self._hold("volatility_filter: atr is all NaN", ts)

        # 過去20期間中央値（末尾20件）
        window = atr_values.iloc[-20:]
        atr_median = window.median()

        if atr_median == 0 or pd.isna(atr_median):
            return self._hold("volatility_filter: atr median is zero or NaN", ts)

        if current_atr < atr_median * self.atr_multiplier:
            return self._hold(
                f"volatility_filter: ATR {current_atr:.2f} < median {atr_median:.2f} × {self.atr_multiplier}",
                ts,
            )

        # フィルター通過 → MACrossStrategy に委譲
        return self._ma_cross.generate_signal(data)
