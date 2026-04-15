"""label_builder のテスト。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cryptbot.models.label_builder import (
    align_features_labels,
    build_binary_labels,
    build_ternary_labels,
)
from cryptbot.models.feature_builder import FEATURE_COLUMNS


def _make_close(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 5_000_000.0 + np.cumsum(rng.normal(0, 10_000, n))
    return pd.DataFrame({"close": close})


class TestBuildBinaryLabels:
    def test_basic(self) -> None:
        df = _make_close(100)
        labels = build_binary_labels(df, horizon=1)
        assert labels.name == "label"
        assert len(labels) == len(df)

    def test_tail_nan(self) -> None:
        df = _make_close(100)
        labels = build_binary_labels(df, horizon=3)
        # 末尾 3 行は NaN
        assert labels.iloc[-3:].isna().all()
        assert not labels.iloc[:-3].isna().any()

    def test_values_binary(self) -> None:
        df = _make_close(200)
        labels = build_binary_labels(df, horizon=1).dropna()
        assert set(labels.unique()).issubset({0, 1})

    def test_threshold_positive(self) -> None:
        """閾値 100% では全て 0 になる（1時間足でそんなに上がらない）。"""
        df = _make_close(100)
        labels = build_binary_labels(df, horizon=1, threshold_pct=100.0).dropna()
        assert (labels == 0).all()

    def test_missing_close_raises(self) -> None:
        df = pd.DataFrame({"open": [1.0, 2.0]})
        with pytest.raises(ValueError, match="close"):
            build_binary_labels(df)

    def test_invalid_horizon_raises(self) -> None:
        df = _make_close(10)
        with pytest.raises(ValueError, match="horizon"):
            build_binary_labels(df, horizon=0)


class TestBuildTernaryLabels:
    def test_three_classes(self) -> None:
        rng = np.random.default_rng(0)
        close = 5_000_000.0 + np.cumsum(rng.normal(0, 50_000, 500))
        df = pd.DataFrame({"close": close})
        labels = build_ternary_labels(df, horizon=1, up_threshold_pct=0.01, down_threshold_pct=-0.01).dropna()
        values = set(labels.unique())
        assert values.issubset({-1, 0, 1})
        assert len(values) == 3  # 全クラス存在

    def test_tail_nan(self) -> None:
        df = _make_close(100)
        labels = build_ternary_labels(df, horizon=2)
        assert labels.iloc[-2:].isna().all()

    def test_invalid_up_threshold_raises(self) -> None:
        df = _make_close(10)
        with pytest.raises(ValueError, match="up_threshold_pct"):
            build_ternary_labels(df, up_threshold_pct=-0.1)

    def test_invalid_down_threshold_raises(self) -> None:
        df = _make_close(10)
        with pytest.raises(ValueError, match="down_threshold_pct"):
            build_ternary_labels(df, down_threshold_pct=0.1)


class TestAlignFeaturesLabels:
    def test_basic_alignment(self) -> None:
        n = 100
        X = pd.DataFrame(
            np.random.default_rng(0).random((n, len(FEATURE_COLUMNS))),
            columns=FEATURE_COLUMNS,
        )
        y = pd.Series([0, 1] * (n // 2), name="label", dtype=int)
        X_out, y_out = align_features_labels(X, y)
        assert len(X_out) == len(y_out)
        assert not X_out.isnull().any().any()
        assert not y_out.isnull().any()

    def test_nan_rows_removed(self) -> None:
        n = 50
        X = pd.DataFrame(
            np.random.default_rng(0).random((n, 2)),
            columns=["a", "b"],
        )
        y = pd.Series([1] * (n - 3) + [None, None, None], name="label", dtype="Int8")
        X_out, y_out = align_features_labels(X, y)
        assert len(X_out) == n - 3
