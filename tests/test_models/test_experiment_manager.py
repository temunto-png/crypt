"""ExperimentManager のテスト。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cryptbot.data.normalizer import normalize
from cryptbot.models.experiment_manager import ExperimentManager, ExperimentRecord
from cryptbot.models.feature_builder import build_features
from cryptbot.models.label_builder import align_features_labels, build_binary_labels
from cryptbot.models.lgbm_model import LightGBMModel


def _make_ohlcv(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 5_000_000.0 + np.cumsum(rng.normal(0, 10_000, n))
    return pd.DataFrame({
        "open": close, "high": close + 1000, "low": close - 1000,
        "close": close, "volume": rng.uniform(10, 100, n),
    })


def _fit_model() -> LightGBMModel:
    df = normalize(_make_ohlcv())
    X = build_features(df)
    y = build_binary_labels(df, horizon=1)
    X, y = align_features_labels(X, y)
    model = LightGBMModel(name="lgbm", params={"n_estimators": 10})
    model.fit(X, y)
    return model


def _make_record(experiment_id: str, accuracy: float = 0.55) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        model_name="lgbm",
        label_type="binary",
        horizon=1,
        train_start="2024-01-01",
        train_end="2024-06-30",
        val_start="2024-07-01",
        val_end="2024-10-31",
        params={"n_estimators": 10},
        metrics={"accuracy": accuracy},
        model_path="",  # save_experiment が更新する
        created_at="2026-04-15T12:00:00+09:00",
    )


class TestExperimentManager:
    def test_save_and_list(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()
        record = _make_record("lgbm_20260415_120000")

        mgr.save_experiment(model, record)
        experiments = mgr.list_experiments()

        assert len(experiments) == 1
        assert experiments[0].experiment_id == "lgbm_20260415_120000"

    def test_model_files_created(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()
        record = _make_record("lgbm_test")

        model_path = mgr.save_experiment(model, record)

        assert model_path.exists()
        assert model_path.with_suffix(".yaml").exists()
        assert (tmp_path / "index.yaml").exists()

    def test_load_model(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()
        record = _make_record("lgbm_load_test")
        mgr.save_experiment(model, record)

        loaded = mgr.load_model("lgbm_load_test", LightGBMModel)
        assert loaded.is_fitted
        assert loaded.name == "lgbm"

    def test_load_model_not_found_raises(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            mgr.load_model("nonexistent", LightGBMModel)

    def test_get_best_experiment(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()

        for i, acc in enumerate([0.50, 0.62, 0.55]):
            record = _make_record(f"lgbm_exp{i}", accuracy=acc)
            record.created_at = f"2026-04-15T1{i}:00:00+09:00"
            mgr.save_experiment(model, record)

        best = mgr.get_best_experiment(metric="accuracy")
        assert best is not None
        assert best.metrics["accuracy"] == 0.62

    def test_get_best_experiment_empty_returns_none(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        assert mgr.get_best_experiment() is None

    def test_load_latest_model(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()

        r1 = _make_record("lgbm_old")
        r1.created_at = "2026-04-14T10:00:00+09:00"
        mgr.save_experiment(model, r1)

        r2 = _make_record("lgbm_new")
        r2.created_at = "2026-04-15T12:00:00+09:00"
        mgr.save_experiment(model, r2)

        loaded = mgr.load_latest_model(LightGBMModel)
        assert loaded is not None

    def test_load_latest_model_empty_returns_none(self, tmp_path) -> None:
        mgr = ExperimentManager(tmp_path)
        assert mgr.load_latest_model(LightGBMModel) is None

    def test_overwrite_same_experiment_id(self, tmp_path) -> None:
        """同一 experiment_id を上書きしても index にエントリが増えないこと。"""
        mgr = ExperimentManager(tmp_path)
        model = _fit_model()
        record = _make_record("lgbm_dup")
        mgr.save_experiment(model, record)
        mgr.save_experiment(model, record)

        experiments = mgr.list_experiments()
        assert len(experiments) == 1

    def test_make_experiment_id_format(self) -> None:
        exp_id = ExperimentManager.make_experiment_id("lgbm")
        assert exp_id.startswith("lgbm_")
        assert len(exp_id) == len("lgbm_20260415_120000")
