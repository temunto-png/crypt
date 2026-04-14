"""モメンタム戦略。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import now_jst


class MomentumStrategy(BaseStrategy):
    """モメンタム戦略。
    BUY: momentum > threshold
    SELL: momentum < -threshold
    HOLD: それ以外
    """

    REQUIRED_COLUMNS = ["momentum", "timestamp"]

    def __init__(
        self,
        threshold: float = 2.0,  # モメンタム閾値 [%]
    ) -> None:
        super().__init__("momentum")
        self.threshold = threshold

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        last = data.iloc[-1]
        ts_raw = last.get("timestamp") if "timestamp" in data.columns else None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = now_jst()

        if "momentum" not in data.columns:
            return self._hold("momentum: カラム不足", ts)

        mom = last["momentum"]
        if np.isnan(mom):
            return self._hold("momentum: NaN", ts)

        if mom > self.threshold:
            confidence = min(mom / self.threshold / 3, 1.0)
            return Signal(Direction.BUY, confidence, f"momentum: {mom:.2f}% > {self.threshold}%", ts)

        if mom < -self.threshold:
            confidence = min(-mom / self.threshold / 3, 1.0)
            return Signal(Direction.SELL, confidence, f"momentum: {mom:.2f}% < -{self.threshold}%", ts)

        return self._hold(f"momentum: {mom:.2f}% in range", ts)
