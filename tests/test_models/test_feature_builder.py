"""feature_builder のテスト。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cryptbot.data.normalizer import normalize
from cryptbot.models.feature_builder import FEATURE_COLUMNS, build_features, get_latest_features


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 5_000_000.0 + np.cumsum(rng.normal(0, 10_000, n))
    high = close + rng.uniform(0, 5_000, n)
    low = close - rng.uniform(0, 5_000, n)
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(10, 100, n),
    })


def _normalized(n: int = 200) -> pd.DataFrame:
    return normalize(_make_ohlcv(n))


class TestBuildFeatures:
    def test_output_columns(self) -> None:
        df = _normalized()
        features = build_features(df)
        assert list(features.columns) == FEATURE_COLUMNS

    def test_no_nan(self) -> None:
        df = _normalized(300)
        features = build_features(df)
        assert not features.isnull().any().any()

    def test_row_count_reduced(self) -> None:
        """NaN 除去でウォームアップ期間分の行が削られること。"""
        df = _normalized(200)
        features = build_features(df)
        assert len(features) < len(df)
        assert len(features) > 0

    def test_missing_column_raises(self) -> None:
        df = _normalized().drop(columns=["adx"])
        with pytest.raises(ValueError, match="必要なカラムが不足"):
            build_features(df)

    def test_missing_close_raises(self) -> None:
        df = _normalized().drop(columns=["close"])
        with pytest.raises(ValueError, match="必要なカラムが不足"):
            build_features(df)

    def test_sma_ratio_range(self) -> None:
        """sma_ratio = sma_short / sma_long - 1 の値域を確認。"""
        df = _normalized(300)
        features = build_features(df)
        # BTC価格では通常 ±0.5 以内
        assert features["sma_ratio"].abs().max() < 1.0

    def test_bb_position_computed(self) -> None:
        df = _normalized(300)
        features = build_features(df)
        # bb_position は (close - bb_lower) / bb_range: トレンドなしなら 0〜1 付近
        assert "bb_position" in features.columns

    def test_adx_norm_is_adx_div_25(self) -> None:
        df = _normalized(300)
        features = build_features(df)
        assert (features["adx_norm"] * 25.0).sub(features["adx"]).abs().max() < 1e-10


class TestGetLatestFeatures:
    def test_returns_single_row(self) -> None:
        df = _normalized(200)
        features = get_latest_features(df)
        assert len(features) == 1
        assert list(features.columns) == FEATURE_COLUMNS

    def test_empty_on_insufficient_data(self) -> None:
        """ウォームアップ期間に満たない短いデータは空 DataFrame を返す。"""
        df = _normalized(10)
        features = get_latest_features(df)
        assert features.empty
