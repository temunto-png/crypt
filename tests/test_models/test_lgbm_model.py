"""LightGBMModel のテスト。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cryptbot.data.normalizer import normalize
from cryptbot.models.feature_builder import build_features
from cryptbot.models.label_builder import align_features_labels, build_binary_labels
from cryptbot.models.lgbm_model import LightGBMModel


def _make_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
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


def _make_train_data(n: int = 300) -> tuple[pd.DataFrame, pd.Series]:
    df = normalize(_make_ohlcv(n))
    X = build_features(df)
    y = build_binary_labels(df, horizon=1)
    return align_features_labels(X, y)


class TestLightGBMModel:
    def test_fit_completes(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)
        assert model.is_fitted

    def test_predict_confidence_range(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)
        pred = model.predict(X)
        assert 0.0 <= pred.confidence <= 1.0

    def test_predict_label_binary(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)
        pred = model.predict(X)
        assert pred.label in (0, 1)

    def test_predict_model_name(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel(name="my_lgbm")
        model.fit(X, y)
        pred = model.predict(X)
        assert pred.model_name == "my_lgbm"

    def test_predict_feature_count(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)
        pred = model.predict(X)
        assert pred.feature_count == X.shape[1]

    def test_predict_proba_shape(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)
        proba = model.predict_proba(X)
        assert proba.shape == (len(X), 2)

    def test_save_load_roundtrip(self, tmp_path) -> None:
        X, y = _make_train_data()
        model = LightGBMModel(name="test_lgbm")
        model.fit(X, y)
        original_pred = model.predict(X)

        model_path = tmp_path / "model.pkl"
        model.save(model_path)

        # pkl と yaml が作成されること
        assert model_path.exists()
        assert model_path.with_suffix(".yaml").exists()

        loaded = LightGBMModel.load(model_path)
        assert loaded.is_fitted
        assert loaded.name == "test_lgbm"

        loaded_pred = loaded.predict(X)
        assert abs(loaded_pred.confidence - original_pred.confidence) < 1e-6

    def test_metadata_has_mean_confidence(self, tmp_path) -> None:
        X, y = _make_train_data()
        model = LightGBMModel()
        model.fit(X, y)

        model_path = tmp_path / "model.pkl"
        model.save(model_path)

        import yaml
        with model_path.with_suffix(".yaml").open() as f:
            meta = yaml.safe_load(f)
        assert "mean_confidence" in meta
        assert 0.0 < meta["mean_confidence"] <= 1.0

    def test_predict_before_fit_raises(self) -> None:
        X, _ = _make_train_data()
        model = LightGBMModel()
        with pytest.raises(RuntimeError):
            model.predict(X)

    def test_custom_params(self) -> None:
        X, y = _make_train_data()
        model = LightGBMModel(params={"n_estimators": 10})
        model.fit(X, y)
        assert model.is_fitted
