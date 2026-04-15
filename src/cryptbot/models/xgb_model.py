"""XGBoost モデル実装。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import yaml

from cryptbot.models.base import BaseMLModel, MLPrediction
from cryptbot.utils.time_utils import JST

logger = logging.getLogger(__name__)

_DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "verbosity": 0,
}


class XGBoostModel(BaseMLModel):
    """XGBoost を使った binary/ternary 分類モデル。

    LightGBMModel と同一インターフェース。
    binary の場合は class 1（UP）の確率を confidence として扱う。
    """

    def __init__(
        self,
        name: str = "xgb",
        params: dict[str, Any] | None = None,
        label_type: str = "binary",
    ) -> None:
        """
        Args:
            name: モデル名
            params: XGBoost パラメータ。None の場合はデフォルト値を使用。
            label_type: "binary" | "ternary"
        """
        super().__init__(name)
        self._params = dict(_DEFAULT_PARAMS)
        if params:
            self._params.update(params)
        self._label_type = label_type
        self._model: Any = None
        self._feature_names: list[str] = []
        self._mean_confidence: float = 0.0

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """モデルを学習する。"""
        import xgboost as xgb

        self._feature_names = list(X.columns)
        params = dict(self._params)

        if self._label_type == "ternary":
            params["objective"] = "multi:softprob"
            params["eval_metric"] = "mlogloss"
            params["num_class"] = 3
            y_fit = y.map({-1: 0, 0: 1, 1: 2})
        else:
            y_fit = y

        self._model = xgb.XGBClassifier(**params)
        self._model.fit(X, y_fit)
        self._fitted = True

        proba = self._model.predict_proba(X)
        self._mean_confidence = float(np.max(proba, axis=1).mean())
        logger.info("XGBoostModel.fit 完了: rows=%d, mean_confidence=%.4f", len(X), self._mean_confidence)

    def predict(self, X: pd.DataFrame) -> MLPrediction:
        """最後の行を予測して MLPrediction を返す。"""
        if not self._fitted or self._model is None:
            raise RuntimeError("predict() を呼ぶ前に fit() が必要です")

        proba = self.predict_proba(X)
        last = proba[-1]

        if self._label_type == "binary":
            confidence = float(last[1])  # class 1（UP）の確率
            label = 1 if confidence >= 0.5 else 0
        else:
            idx = int(np.argmax(last))
            label = [-1, 0, 1][idx]
            confidence = float(last[idx])

        return MLPrediction(
            label=label,
            confidence=confidence,
            model_name=self._name,
            feature_count=len(self._feature_names),
        )

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._fitted or self._model is None:
            raise RuntimeError("predict_proba() を呼ぶ前に fit() が必要です")
        return self._model.predict_proba(X)

    def save(self, path: Path) -> None:
        """pkl ファイルにモデルを保存し、YAML メタデータを書き出す。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._model, path)

        meta = {
            "model_name": self._name,
            "model_class": "XGBoostModel",
            "trained_at": datetime.now(tz=JST).isoformat(),
            "feature_names": self._feature_names,
            "train_rows": None,
            "mean_confidence": self._mean_confidence,
            "label_type": self._label_type,
            "params": self._params,
        }
        meta_path = path.with_suffix(".yaml")
        with meta_path.open("w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)
        logger.info("XGBoostModel 保存: %s", path)

    @classmethod
    def load(cls, path: Path) -> "XGBoostModel":
        """pkl ファイルとメタデータ YAML からモデルをロードする。"""
        path = Path(path)
        meta_path = path.with_suffix(".yaml")

        with meta_path.open(encoding="utf-8") as f:
            meta = yaml.safe_load(f)

        instance = cls(
            name=meta.get("model_name", "xgb"),
            params=meta.get("params"),
            label_type=meta.get("label_type", "binary"),
        )
        instance._model = joblib.load(path)
        instance._fitted = True
        instance._feature_names = meta.get("feature_names", [])
        instance._mean_confidence = meta.get("mean_confidence", 0.0)
        logger.info("XGBoostModel ロード: %s", path)
        return instance
