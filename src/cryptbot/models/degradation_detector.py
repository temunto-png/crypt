"""モデル劣化検知モジュール。

直近 N バーの confidence が学習時ベースラインから X% 低下した場合に
アラートを発行し、呼び出し元がシグナルを無効化できるようにする。
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class DegradationStatus:
    """劣化検知の結果。"""

    degraded: bool
    mean_recent_confidence: float
    baseline_confidence: float
    drop_pct: float         # (baseline - recent) / baseline * 100
    message: str


class DegradationDetector:
    """直近 N バーの confidence が学習時ベースラインから X% 低下でアラート。

    設計方針:
    - confidence の履歴はメモリ上の deque に保持（再起動でリセット）
    - 劣化検知時はモデルを無効化せず、戻り値の degraded フラグを使って
      呼び出し元（apply_ml_filter）が confidence=0.0 を返す責務を持つ
    - window 未満の履歴しかない場合は degraded=False（安全側に倒す）
    """

    def __init__(
        self,
        baseline_confidence: float,
        window: int = 100,
        drop_threshold_pct: float = 20.0,
    ) -> None:
        """
        Args:
            baseline_confidence: 学習データの平均 confidence（モデルメタデータから取得）
            window: 直近何バーで評価するか
            drop_threshold_pct: 何% 低下でアラートとするか
        """
        self._baseline = baseline_confidence
        self._window = window
        self._drop_threshold = drop_threshold_pct
        self._history: deque[float] = deque(maxlen=window)

    def update(self, confidence: float) -> None:
        """推論結果の confidence を履歴に追加する。"""
        self._history.append(confidence)

    def check(self) -> DegradationStatus:
        """現在の劣化状態を評価して返す。

        履歴が window 未満の場合は degraded=False（データ不足）。
        """
        if len(self._history) < self._window:
            return DegradationStatus(
                degraded=False,
                mean_recent_confidence=0.0,
                baseline_confidence=self._baseline,
                drop_pct=0.0,
                message=f"履歴不足 ({len(self._history)}/{self._window})",
            )

        mean_recent = sum(self._history) / len(self._history)

        if self._baseline <= 0:
            drop_pct = 0.0
        else:
            drop_pct = (self._baseline - mean_recent) / self._baseline * 100.0

        degraded = drop_pct >= self._drop_threshold

        if degraded:
            msg = (
                f"モデル劣化検知: baseline={self._baseline:.4f}, "
                f"recent={mean_recent:.4f}, drop={drop_pct:.1f}%"
            )
            logger.warning(msg)
        else:
            msg = f"正常: recent_confidence={mean_recent:.4f}, drop={drop_pct:.1f}%"

        return DegradationStatus(
            degraded=degraded,
            mean_recent_confidence=mean_recent,
            baseline_confidence=self._baseline,
            drop_pct=drop_pct,
            message=msg,
        )

    @classmethod
    def from_model_metadata(
        cls,
        metadata_path: Path,
        window: int = 100,
        drop_threshold_pct: float = 20.0,
    ) -> "DegradationDetector":
        """モデルメタデータ YAML から baseline_confidence を読み込んで初期化する。

        Args:
            metadata_path: BaseMLModel.save() が書き出した YAML ファイルのパス
            window: 評価ウィンドウ
            drop_threshold_pct: 劣化判定閾値 [%]
        """
        path = Path(metadata_path)
        with path.open(encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        baseline = float(meta.get("mean_confidence", 0.5))
        return cls(
            baseline_confidence=baseline,
            window=window,
            drop_threshold_pct=drop_threshold_pct,
        )
