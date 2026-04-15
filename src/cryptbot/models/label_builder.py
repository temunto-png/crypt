"""将来リターンから教師ラベルを生成するモジュール。

ラベル生成には look-ahead bias が必要なため、バックテスト学習フェーズ
（テスト期間の外側）でのみ使用すること。
"""
from __future__ import annotations

import pandas as pd


def build_binary_labels(
    df: pd.DataFrame,
    horizon: int = 1,
    threshold_pct: float = 0.0,
) -> pd.Series:
    """horizon バー後のリターンが threshold_pct を超えれば 1、そうでなければ 0。

    Args:
        df: close カラムを含む DataFrame
        horizon: 何バー後のリターンで判定するか（デフォルト 1）
        threshold_pct: 閾値 [%]（デフォルト 0.0 = 0%超で UP）

    Returns:
        pd.Series (dtype int8, index=df.index, 末尾 horizon 行は NaN)
    """
    if "close" not in df.columns:
        raise ValueError("label_builder: 'close' カラムが必要です")
    if horizon <= 0:
        raise ValueError(f"label_builder: horizon は正の整数が必要です (got {horizon})")

    future_return = df["close"].shift(-horizon) / df["close"] - 1.0
    labels = (future_return * 100 > threshold_pct).astype("Int8")
    # shift で生じた末尾 horizon 行を明示的に NaN に設定
    labels.iloc[-horizon:] = pd.NA
    return labels.rename("label")


def build_ternary_labels(
    df: pd.DataFrame,
    horizon: int = 1,
    up_threshold_pct: float = 0.3,
    down_threshold_pct: float = -0.3,
) -> pd.Series:
    """3値ラベルを生成する: 1=UP, 0=NEUTRAL, -1=DOWN。

    UP/DOWN 閾値の内側は NEUTRAL として扱う（ノイズ排除）。

    Args:
        df: close カラムを含む DataFrame
        horizon: 何バー後のリターンで判定するか
        up_threshold_pct: UP 閾値 [%]（正の値）
        down_threshold_pct: DOWN 閾値 [%]（負の値）

    Returns:
        pd.Series (dtype int8, 1/0/-1, 末尾 horizon 行は NaN)
    """
    if "close" not in df.columns:
        raise ValueError("label_builder: 'close' カラムが必要です")
    if up_threshold_pct <= 0:
        raise ValueError("label_builder: up_threshold_pct は正の値が必要です")
    if down_threshold_pct >= 0:
        raise ValueError("label_builder: down_threshold_pct は負の値が必要です")

    future_return_pct = (df["close"].shift(-horizon) / df["close"] - 1.0) * 100

    labels = pd.Series(0, index=df.index, dtype="Int8")
    labels[future_return_pct > up_threshold_pct] = 1
    labels[future_return_pct < down_threshold_pct] = -1
    # 末尾 horizon 行は NaN に設定
    labels.iloc[-horizon:] = pd.NA
    return labels.rename("label")


def align_features_labels(
    features: pd.DataFrame,
    labels: pd.Series,
) -> tuple[pd.DataFrame, pd.Series]:
    """features と labels の index を揃えて NaN 行を除去した (X, y) を返す。

    Args:
        features: build_features() の出力
        labels: build_binary_labels() / build_ternary_labels() の出力

    Returns:
        (X, y) タプル。NaN なし、index 整合済み。
    """
    combined = features.join(labels, how="inner")
    combined = combined.dropna()
    X = combined[features.columns]
    y = combined["label"].astype(int)
    return X, y
