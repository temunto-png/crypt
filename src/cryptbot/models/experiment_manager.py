"""YAML + pkl ファイルで実験を管理するモジュール。MLflow 不使用。"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from cryptbot.models.base import BaseMLModel
from cryptbot.utils.time_utils import JST

logger = logging.getLogger(__name__)

_INDEX_FILE = "index.yaml"


@dataclass
class ExperimentRecord:
    """1 実験の記録。"""

    experiment_id: str          # "{model_name}_{YYYYMMDD_HHMMSS}"
    model_name: str
    label_type: str             # "binary" | "ternary"
    horizon: int
    train_start: str            # ISO 日付
    train_end: str
    val_start: str
    val_end: str
    params: dict[str, Any]
    metrics: dict[str, float]   # {"accuracy": 0.62, "precision": 0.64, ...}
    model_path: str             # index.yaml からの相対パス
    created_at: str             # ISO datetime


class ExperimentManager:
    """実験の保存・読み込み・一覧管理。

    ディレクトリ構造:
    base_dir/
      index.yaml                  ← 全実験の一覧
      lgbm_20260415_120000/
        model.pkl
        model.yaml                ← BaseMLModel.save() が書き出すメタデータ
        experiment.yaml           ← ExperimentRecord の内容
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save_experiment(
        self,
        model: BaseMLModel,
        record: ExperimentRecord,
    ) -> Path:
        """モデルと実験記録を保存し、index.yaml を更新する。

        Returns:
            モデル pkl ファイルのパス
        """
        exp_dir = self._base_dir / record.experiment_id
        exp_dir.mkdir(parents=True, exist_ok=True)

        model_path = exp_dir / "model.pkl"
        model.save(model_path)

        exp_yaml = exp_dir / "experiment.yaml"
        with exp_yaml.open("w", encoding="utf-8") as f:
            yaml.dump(asdict(record), f, allow_unicode=True, default_flow_style=False)

        # index.yaml を更新
        record_dict = asdict(record)
        record_dict["model_path"] = str(model_path.relative_to(self._base_dir))
        experiments = self._load_index()
        # 同一 experiment_id の上書き
        experiments = [e for e in experiments if e["experiment_id"] != record.experiment_id]
        experiments.append(record_dict)
        self._save_index(experiments)

        logger.info("実験を保存: %s", record.experiment_id)
        return model_path

    def load_model(
        self,
        experiment_id: str,
        model_cls: type[BaseMLModel],
    ) -> BaseMLModel:
        """experiment_id で指定したモデルをロードする。

        Args:
            experiment_id: 実験 ID
            model_cls: LightGBMModel または XGBoostModel

        Raises:
            FileNotFoundError: 指定の実験が見つからない場合
        """
        experiments = self._load_index()
        entry = next((e for e in experiments if e["experiment_id"] == experiment_id), None)
        if entry is None:
            raise FileNotFoundError(f"実験が見つかりません: {experiment_id}")

        model_path = self._base_dir / entry["model_path"]
        return model_cls.load(model_path)

    def list_experiments(self) -> list[ExperimentRecord]:
        """全実験を一覧で返す（新しい順）。"""
        raw = self._load_index()
        records = []
        for entry in raw:
            try:
                records.append(ExperimentRecord(**entry))
            except (TypeError, KeyError):
                logger.warning("不正な実験エントリをスキップ: %s", entry.get("experiment_id"))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def get_best_experiment(
        self,
        metric: str = "accuracy",
        model_name: str | None = None,
    ) -> ExperimentRecord | None:
        """指定メトリクスが最大の実験を返す。該当なしは None。"""
        records = self.list_experiments()
        if model_name:
            records = [r for r in records if r.model_name == model_name]
        candidates = [r for r in records if metric in r.metrics]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.metrics[metric])

    def load_latest_model(
        self,
        model_cls: type[BaseMLModel],
        model_name: str | None = None,
    ) -> BaseMLModel | None:
        """最新の実験のモデルをロードする。実験なしは None。"""
        records = self.list_experiments()
        if model_name:
            records = [r for r in records if r.model_name == model_name]
        if not records:
            return None
        latest = records[0]  # list_experiments は新しい順
        return self.load_model(latest.experiment_id, model_cls)

    @staticmethod
    def make_experiment_id(model_name: str) -> str:
        """現在時刻ベースの実験 ID を生成する。"""
        ts = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
        return f"{model_name}_{ts}"

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _load_index(self) -> list[dict]:
        index_path = self._base_dir / _INDEX_FILE
        if not index_path.exists():
            return []
        with index_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []

    def _save_index(self, experiments: list[dict]) -> None:
        index_path = self._base_dir / _INDEX_FILE
        with index_path.open("w", encoding="utf-8") as f:
            yaml.dump(experiments, f, allow_unicode=True, default_flow_style=False)
