"""OHLCV DataFrame に対してテクニカル指標を計算し、カラムを付与するモジュール。"""
from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = frozenset(["open", "high", "low", "close", "volume"])


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range を計算する。

    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = TR.rolling(period).mean()
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _calc_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI(Relative Strength Index) を計算する。

    Wilder's EWM (alpha=1/period) で平滑化する。
    """
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index を計算して pd.Series で返す。

    DX = |DI+ - DI-| / (DI+ + DI-) * 100
    ADX = EWM(DX, alpha=1/period, adjust=False)  # Wilder's smoothing
    RegimeDetector から呼び出す用。
    """
    missing = frozenset(["high", "low", "close"]) - set(df.columns)
    if missing:
        raise ValueError(f"calculate_adx: 必要なカラムが不足: {sorted(missing)}")

    high = df["high"]
    low = df["low"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # +DM, -DM
    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # ATR (Wilder's smoothing)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_w = tr.ewm(alpha=1 / period, adjust=False).mean()

    # DI+, DI-
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_w

    # DX -> ADX
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV DataFrame に全テクニカル指標カラムを付与して返す。

    入力 DataFrame に必要なカラム: open, high, low, close, volume

    Returns:
        入力 df のコピーに以下のカラムを追加したもの:
        sma_short, sma_long, atr, rsi, bb_upper, bb_lower, bb_bandwidth,
        momentum, volume_ma_ratio

    Note:
        先頭の一部行は NaN になる（period 分のウォームアップ期間）。
        呼び出し元がドロップするかどうかを決める。
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"normalize: 必要なカラムが不足: {sorted(missing)}")

    result = df.copy()
    close = result["close"]
    volume = result["volume"]

    # SMA
    result["sma_short"] = close.rolling(20).mean()
    result["sma_long"] = close.rolling(50).mean()

    # ATR
    result["atr"] = _calc_atr(result, period=14)

    # RSI
    result["rsi"] = _calc_rsi(close, period=14)

    # Bollinger Bands
    # 標本標準偏差 (ddof=1) — Bollinger Bands 標準定義
    bb_std = close.rolling(20).std(ddof=1)
    result["bb_upper"] = result["sma_short"] + 2 * bb_std
    result["bb_lower"] = result["sma_short"] - 2 * bb_std
    result["bb_bandwidth"] = (result["bb_upper"] - result["bb_lower"]) / result["sma_short"] * 100

    # Momentum（5バー窓: 取引頻度向上のため14→5に変更）
    result["momentum"] = close.pct_change(5) * 100

    # Volume MA Ratio
    result["volume_ma_ratio"] = volume / volume.rolling(20).mean()

    # ADX（RegimeDetector との共有計算を normalizer に統一）
    result["adx"] = calculate_adx(result, period=14)

    return result
