"""平均回帰戦略（Long-only）。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from cryptbot.strategies.base import BaseStrategy, Direction, Signal
from cryptbot.utils.time_utils import now_jst


class MeanReversionStrategy(BaseStrategy):
    """平均回帰戦略（Long-only）。
    BUY: close < bb_lower かつ bb_bandwidth >= min_bandwidth_pct
    SELL: close > bb_upper（既存ロングのクローズのみ）
    HOLD: それ以外
    """

    REQUIRED_COLUMNS = ["close", "bb_lower", "bb_upper", "bb_bandwidth", "timestamp"]

    def __init__(
        self,
        min_bandwidth_pct: float = 1.0,  # 最小 BB 帯域幅 [%]
    ) -> None:
        super().__init__("mean_reversion")
        self.min_bandwidth_pct = min_bandwidth_pct

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        last = data.iloc[-1]
        ts_raw = last.get("timestamp") if "timestamp" in data.columns else None
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = now_jst()

        required = ["close", "bb_lower", "bb_upper", "bb_bandwidth"]
        missing = [c for c in required if c not in data.columns]
        if missing:
            return self._hold(f"mean_reversion: カラム不足 {missing}", ts)

        close = last["close"]
        bb_lower = last["bb_lower"]
        bb_upper = last["bb_upper"]
        bb_bw = last["bb_bandwidth"]

        if any(np.isnan(v) for v in [close, bb_lower, bb_upper, bb_bw]):
            return self._hold("mean_reversion: NaN", ts)

        # close < bb_lower AND bb_bandwidth >= min_bandwidth_pct → BUY
        if close < bb_lower and bb_bw >= self.min_bandwidth_pct:
            return Signal(Direction.BUY, 1.0, "mean_reversion: close below bb_lower", ts)

        # close > bb_upper → SELL
        if close > bb_upper:
            return Signal(Direction.SELL, 1.0, "mean_reversion: close above bb_upper", ts)

        return self._hold("mean_reversion: in range", ts)
