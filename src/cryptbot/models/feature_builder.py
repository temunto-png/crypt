"""normalize() 済み OHLCV DataFrame → ML 用特徴量行列に変換するモジュール。

呼び出し元は normalizer.normalize() を適用済みかつ adx カラムを含む
DataFrame を渡す責任を持つ。このモジュールは変換のみ行う。
"""
from __future__ import annotations

import pandas as pd

# normalizer.normalize() + adx を付与した DataFrame に期待するカラム
_REQUIRED_COLUMNS: list[str] = [
    "close", "high", "low",
    "sma_short", "sma_long",
    "rsi",
    "bb_upper", "bb_lower", "bb_bandwidth",
    "atr",
    "momentum",
    "volume_ma_ratio",
    "adx",
]

# ML モデルに渡す特徴量カラム名（順序固定）
FEATURE_COLUMNS: list[str] = [
    "sma_ratio",        # sma_short / sma_long - 1
    "rsi",
    "bb_position",      # (close - bb_lower) / (bb_upper - bb_lower)
    "bb_bandwidth",
    "atr_ratio",        # atr / close
    "momentum",
    "volume_ma_ratio",
    "adx",
    "adx_norm",         # adx / 25.0（閾値正規化）
    "close_change_1",   # close.pct_change(1)
    "close_change_5",   # close.pct_change(5)
    "close_change_14",  # close.pct_change(14)
    "high_low_ratio",   # (high - low) / close
    "rsi_change",       # rsi.diff(1)
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """normalize() + adx 付き DataFrame から特徴量 DataFrame を構築して返す。

    Args:
        df: normalizer.normalize() 適用済みかつ adx カラムを含む DataFrame

    Returns:
        FEATURE_COLUMNS の各カラムのみを含む DataFrame（NaN 行を dropna 済み）

    Raises:
        ValueError: 必須カラムが不足している場合
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"feature_builder: 必要なカラムが不足: {sorted(missing)}")

    result = pd.DataFrame(index=df.index)

    close = df["close"]
    bb_range = df["bb_upper"] - df["bb_lower"]

    result["sma_ratio"] = df["sma_short"] / df["sma_long"] - 1.0
    result["rsi"] = df["rsi"]
    # bb_range が 0 の行は NaN になる（後の dropna で除去）
    result["bb_position"] = (close - df["bb_lower"]) / bb_range.replace(0, float("nan"))
    result["bb_bandwidth"] = df["bb_bandwidth"]
    result["atr_ratio"] = df["atr"] / close.replace(0, float("nan"))
    result["momentum"] = df["momentum"]
    result["volume_ma_ratio"] = df["volume_ma_ratio"]
    result["adx"] = df["adx"]
    result["adx_norm"] = df["adx"] / 25.0
    result["close_change_1"] = close.pct_change(1)
    result["close_change_5"] = close.pct_change(5)
    result["close_change_14"] = close.pct_change(14)
    result["high_low_ratio"] = (df["high"] - df["low"]) / close.replace(0, float("nan"))
    result["rsi_change"] = df["rsi"].diff(1)

    return result[FEATURE_COLUMNS].dropna()


def get_latest_features(df: pd.DataFrame) -> pd.DataFrame:
    """直近1行分の特徴量を返す（推論用）。

    Args:
        df: normalizer.normalize() 適用済みかつ adx カラムを含む DataFrame

    Returns:
        shape (1, len(FEATURE_COLUMNS)) の DataFrame。
        特徴量計算に必要なデータが不足している場合は空の DataFrame。
    """
    features = build_features(df)
    if features.empty:
        return features
    return features.iloc[[-1]]
