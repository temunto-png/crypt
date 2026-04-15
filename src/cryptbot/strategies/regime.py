"""ADX + ATR を用いた市場レジーム分類器。"""
from __future__ import annotations

from enum import Enum

import pandas as pd

class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"


class RegimeDetector:
    """ADX + ATR でレジームを分類する。

    入力 DataFrame の期待スキーマ（呼び出し元が事前に計算して渡す責任を持つ）:
    REQUIRED_COLUMNS = ["close", "high", "low", "atr", "sma_short", "sma_long"]
    atr: ATR(14), sma_short: SMA(20), sma_long: SMA(50) — normalizer.py で計算済みであること
    """

    REQUIRED_COLUMNS: list[str] = ["close", "high", "low", "atr", "sma_short", "sma_long", "adx"]

    # レジーム分類の閾値
    ADX_THRESHOLD: float = 25.0
    HIGH_VOL_ATR_RATIO: float = 2.0   # ATR / ATR_median(20) > 2.0 で HIGH_VOL

    def detect(self, data: pd.DataFrame) -> Regime:
        """直近バーのレジームを判定する。

        Args:
            data: normalize() 済みの DataFrame（最低20バー以上を推奨）

        Returns:
            Regime 列挙値

        Raises:
            ValueError: 必要なカラムが不足している場合
        """
        # カラム存在チェック
        missing = [c for c in self.REQUIRED_COLUMNS if c not in data.columns]
        if missing:
            raise ValueError(f"RegimeDetector: 必要なカラムが不足: {missing}")

        # 最低行数チェック（ADX 計算に必要）
        if len(data) < 20:
            raise ValueError("RegimeDetector: データが不足しています（最低20バー必要）")

        adx = data["adx"]
        atr_median = data["atr"].rolling(20).median()
        atr_median_val = atr_median.iloc[-1]

        if atr_median_val == 0 or pd.isna(atr_median_val):
            atr_ratio_val = 0.0
        else:
            atr_ratio_val = data["atr"].iloc[-1] / atr_median_val

        # HIGH_VOL チェック（最優先）
        if atr_ratio_val > self.HIGH_VOL_ATR_RATIO:
            return Regime.HIGH_VOL

        # TREND チェック
        if adx.iloc[-1] > self.ADX_THRESHOLD:
            if data["sma_short"].iloc[-1] > data["sma_long"].iloc[-1]:
                return Regime.TREND_UP
            return Regime.TREND_DOWN

        return Regime.RANGE
