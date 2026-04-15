"""ML モデルの抽象基底クラスと共有データクラス定義。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class MLPrediction:
    """ML モデルの予測結果。"""

    label: int              # binary: 0=DOWN/HOLD, 1=UP  / ternary: 1=UP, 0=NEUTRAL, -1=DOWN
    confidence: float       # 0.0-1.0  (predict_proba の最大値)
    model_name: str
    feature_count: int      # 推論に使った特徴量数（デバッグ用）


class BaseMLModel(ABC):
    """ML モデルの抽象基底クラス。

    全実装は fit/predict/predict_proba/save/load を実装する。
    モデル出力は confidence_score (0.0-1.0) に正規化して返す。
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._fitted = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_fitted(self) -> bool:
        """fit() 完了後に True。"""
        return self._fitted

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        """モデルを学習する。

        Args:
            X: FeatureBuilder.build_features() の出力
            y: LabelBuilder.build_binary_labels() / build_ternary_labels() の出力
        """

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> MLPrediction:
        """1行または複数行を予測し、最後の行の MLPrediction を返す。

        Args:
            X: shape (n, len(FEATURE_COLUMNS)) の DataFrame

        Returns:
            MLPrediction（confidence は [0, 1] の範囲）
        """

    @abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """クラス確率を返す。

        Returns:
            shape (n_samples, n_classes) の ndarray
        """

    @abstractmethod
    def save(self, path: Path) -> None:
        """モデルを pkl ファイルに保存する。

        メタデータ（mean_confidence 等）は path.with_suffix(".yaml") に書き出す。
        """

    @classmethod
    @abstractmethod
    def load(cls, path: Path) -> "BaseMLModel":
        """pkl ファイルからモデルをロードする。"""
