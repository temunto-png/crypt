"""DegradationDetector のテスト。"""
from __future__ import annotations

import pytest
import yaml

from cryptbot.models.degradation_detector import DegradationDetector


class TestDegradationDetector:
    def test_insufficient_history_not_degraded(self) -> None:
        """window に満たない履歴では degraded=False。"""
        det = DegradationDetector(baseline_confidence=0.7, window=10, drop_threshold_pct=20.0)
        for _ in range(9):
            det.update(0.3)  # 低 confidence を入れても
        status = det.check()
        assert not status.degraded

    def test_high_confidence_not_degraded(self) -> None:
        det = DegradationDetector(baseline_confidence=0.7, window=10, drop_threshold_pct=20.0)
        for _ in range(10):
            det.update(0.72)
        status = det.check()
        assert not status.degraded
        assert status.drop_pct < 20.0

    def test_low_confidence_degraded(self) -> None:
        """baseline=0.7, 履歴=0.4 → drop=42.8% > 20% → degraded=True。"""
        det = DegradationDetector(baseline_confidence=0.7, window=10, drop_threshold_pct=20.0)
        for _ in range(10):
            det.update(0.4)
        status = det.check()
        assert status.degraded
        assert status.drop_pct > 20.0

    def test_exact_threshold_boundary(self) -> None:
        """drop が threshold を超えると degraded=True。"""
        baseline = 0.5
        det = DegradationDetector(baseline_confidence=baseline, window=5, drop_threshold_pct=20.0)
        # drop = (0.5 - 0.35) / 0.5 * 100 = 30% > 20%
        for _ in range(5):
            det.update(0.35)
        status = det.check()
        assert status.degraded

    def test_update_returns_none(self) -> None:
        det = DegradationDetector(baseline_confidence=0.6, window=5)
        result = det.update(0.5)
        assert result is None

    def test_window_is_rolling(self) -> None:
        """deque の maxlen が window 分のみ保持することを確認。"""
        det = DegradationDetector(baseline_confidence=0.7, window=5, drop_threshold_pct=20.0)
        # 最初の 5 件は高 confidence
        for _ in range(5):
            det.update(0.75)
        # 次の 5 件（= window 分）は低 confidence で上書き
        for _ in range(5):
            det.update(0.3)
        status = det.check()
        assert status.degraded

    def test_status_fields(self) -> None:
        det = DegradationDetector(baseline_confidence=0.7, window=5, drop_threshold_pct=20.0)
        for _ in range(5):
            det.update(0.7)
        status = det.check()
        assert status.baseline_confidence == 0.7
        assert abs(status.mean_recent_confidence - 0.7) < 1e-9
        assert isinstance(status.message, str)

    def test_from_model_metadata(self, tmp_path) -> None:
        meta_path = tmp_path / "model.yaml"
        meta = {"mean_confidence": 0.65}
        with meta_path.open("w") as f:
            yaml.dump(meta, f)

        det = DegradationDetector.from_model_metadata(meta_path, window=10, drop_threshold_pct=25.0)
        assert det._baseline == 0.65
        assert det._window == 10
        assert det._drop_threshold == 25.0

    def test_zero_baseline_no_error(self) -> None:
        det = DegradationDetector(baseline_confidence=0.0, window=5)
        for _ in range(5):
            det.update(0.0)
        status = det.check()
        assert not status.degraded  # ゼロ除算回避
