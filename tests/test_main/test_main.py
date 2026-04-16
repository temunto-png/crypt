"""cryptbot.main と cryptbot.tools.verify_audit_log のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from cryptbot.main import main
from cryptbot.tools.verify_audit_log import main as verify_main
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_ohlcv_and_store(tmp_path: Path, rows: int = 55) -> tuple[Path, Path]:
    """正規化済み OHLCV を Storage に保存し、DB パスとデータパスを返す。"""
    from cryptbot.data.normalizer import normalize
    from cryptbot.data.storage import Storage

    db_path = tmp_path / "paper.db"
    data_dir = tmp_path / "data"
    storage = Storage(db_path=db_path, data_dir=data_dir)
    storage.initialize()

    base = datetime(2024, 1, 1, 9, 0, tzinfo=JST)
    timestamps = [base + timedelta(hours=i) for i in range(rows)]
    prices = [5_000_000.0 + i * 1000.0 for i in range(rows)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.002 for p in prices],
        "low": [p * 0.998 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [1.0] * rows,
    })
    df = normalize(df)
    storage.save_ohlcv(df, pair="btc_jpy", timeframe="1hour", year=2024)

    return db_path, data_dir


# ------------------------------------------------------------------ #
# cryptbot.main
# ------------------------------------------------------------------ #

class TestMain:
    def test_help_exits_zero(self) -> None:
        """`--help` が SystemExit(0) で終了すること。"""
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_paper_mode_returns_zero(self, tmp_path: Path) -> None:
        """paper モード（デフォルト）が OHLCV データあれば 0 を返すこと。"""
        db_path, data_dir = _make_ohlcv_and_store(tmp_path)
        result = main(["--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 0

    def test_paper_mode_explicit_returns_zero(self, tmp_path: Path) -> None:
        """--mode paper が 0 を返すこと。"""
        db_path, data_dir = _make_ohlcv_and_store(tmp_path)
        result = main(["--mode", "paper", "--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 0

    def test_paper_mode_no_data_returns_one(self, tmp_path: Path) -> None:
        """OHLCV データが存在しない場合は 1 を返すこと。"""
        db_path = tmp_path / "empty.db"
        data_dir = tmp_path / "data"
        result = main(["--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 1

    def test_live_mode_gate_fails_without_env(self, tmp_path: Path) -> None:
        """`--mode live` は CRYPT_MODE=live 未設定時にゲートチェック失敗で 1 を返すこと。"""
        db_path = tmp_path / "live.db"
        data_dir = tmp_path / "data"
        result = main(["--mode", "live", "--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 1

    def test_live_mode_with_confirm_gate_fails_without_env(self, tmp_path: Path) -> None:
        """`--mode live --confirm-live` も CRYPT_MODE=live 未設定時にゲートチェック失敗で 1 を返すこと。"""
        db_path = tmp_path / "live.db"
        data_dir = tmp_path / "data"
        result = main(["--mode", "live", "--confirm-live", "--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 1

    def test_confirm_live_flag_alone_runs_paper(self, tmp_path: Path) -> None:
        """`--confirm-live` 単体は paper モードとして動作し、データなしで 1 を返すこと。"""
        db_path = tmp_path / "paper.db"
        data_dir = tmp_path / "data"
        result = main(["--confirm-live", "--db", str(db_path), "--data-dir", str(data_dir)])
        assert result == 1


# ------------------------------------------------------------------ #
# cryptbot.tools.verify_audit_log
# ------------------------------------------------------------------ #

class TestVerifyAuditLog:
    def test_missing_db_returns_one(self, tmp_path: Path) -> None:
        """存在しない DB ファイルを指定すると終了コード 1 が返ること。"""
        result = verify_main(["--db", str(tmp_path / "nonexistent.db")])
        assert result == 1

    def test_valid_db_returns_zero(self, tmp_path: Path) -> None:
        """正常な audit_log を持つ DB で終了コード 0 が返ること。"""
        from cryptbot.data.storage import Storage

        storage = Storage(
            db_path=tmp_path / "test.db",
            data_dir=tmp_path / "data",
        )
        storage.initialize()
        storage.insert_audit_log("SIGNAL", strategy="s1", direction="BUY")
        storage.insert_audit_log("FILL", fill_price=5_000_000.0)

        result = verify_main(["--db", str(tmp_path / "test.db")])
        assert result == 0

    def test_tampered_db_returns_one(self, tmp_path: Path) -> None:
        """改ざんされた audit_log を持つ DB で終了コード 1 が返ること。"""
        import sqlite3
        from cryptbot.data.storage import Storage

        storage = Storage(
            db_path=tmp_path / "tampered.db",
            data_dir=tmp_path / "data",
        )
        storage.initialize()
        storage.insert_audit_log("SIGNAL", signal_reason="original")
        storage.insert_audit_log("FILL", fill_price=5_000_000.0)

        # trigger を迂回して改ざん
        conn = sqlite3.connect(tmp_path / "tampered.db")
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET signal_reason = 'tampered' "
            "WHERE id = (SELECT MIN(id) FROM audit_log)"
        )
        conn.commit()
        conn.close()

        result = verify_main(["--db", str(tmp_path / "tampered.db")])
        assert result == 1


# ------------------------------------------------------------------ #
# TestBuildMlComponents
# ------------------------------------------------------------------ #

class TestBuildMlComponents:
    """_build_ml_components() の動作を検証する。"""

    def _make_settings_ml_disabled(self, tmp_path: Path):
        """model.enabled=False の Settings を返す。"""
        from cryptbot.config.settings import Settings
        return Settings(
            _env_file=None,
            model={"enabled": False},
        )

    def _make_settings_ml_enabled(self, tmp_path: Path):
        """model.enabled=True, experiments_dir を tmp_path に向けた Settings を返す。"""
        from cryptbot.config.settings import Settings
        return Settings(
            _env_file=None,
            model={
                "enabled": True,
                "experiments_dir": str(tmp_path / "experiments"),
                "model_type": "lgbm",
            },
        )

    def test_disabled_returns_none_none(self, tmp_path: Path) -> None:
        """enabled=False のとき (None, None) を返す。"""
        from cryptbot.main import _build_ml_components
        settings = self._make_settings_ml_disabled(tmp_path)
        result = _build_ml_components(settings)
        assert result == (None, None)

    def test_enabled_no_model_returns_none(self, tmp_path: Path) -> None:
        """enabled=True でモデルが存在しないとき None（fail-closed）を返す。"""
        from cryptbot.main import _build_ml_components
        settings = self._make_settings_ml_enabled(tmp_path)
        result = _build_ml_components(settings)
        assert result is None

    def test_enabled_with_model_returns_components(self, tmp_path: Path) -> None:
        """enabled=True で正常モデルが存在するとき (model, detector) タプルを返す。"""
        import numpy as np
        import pandas as pd
        from cryptbot.main import _build_ml_components
        from cryptbot.models.experiment_manager import ExperimentManager, ExperimentRecord
        from cryptbot.models.lgbm_model import LightGBMModel
        from cryptbot.models.degradation_detector import DegradationDetector

        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir()
        manager = ExperimentManager(base_dir=str(exp_dir))

        # LightGBMModel.fit() で正規のモデルを訓練
        feature_cols = [f"f{i}" for i in range(10)]
        X = pd.DataFrame(np.random.rand(60, 10), columns=feature_cols)
        y = pd.Series(np.random.randint(0, 2, 60))
        model = LightGBMModel(params={"num_leaves": 4, "verbose": -1, "n_estimators": 2})
        model.fit(X, y)

        record = ExperimentRecord(
            experiment_id="test_lgbm_20240101_000000",
            model_name="lgbm",
            label_type="binary",
            horizon=1,
            train_start="2024-01-01",
            train_end="2024-06-01",
            val_start="2024-06-01",
            val_end="2024-12-01",
            params={},
            metrics={"accuracy": 0.65},
            model_path="test_lgbm_20240101_000000.pkl",
            created_at="2024-01-01T00:00:00",
        )
        manager.save_experiment(model, record)

        settings = self._make_settings_ml_enabled(tmp_path)
        result = _build_ml_components(settings)
        assert result is not None
        ml_model, degradation_detector = result
        assert ml_model is not None
        assert isinstance(degradation_detector, DegradationDetector)

    def test_enabled_active_experiment_not_found_returns_none(self, tmp_path: Path) -> None:
        """enabled=True で active_experiment_id が存在しないとき None を返す。"""
        from cryptbot.config.settings import Settings
        from cryptbot.main import _build_ml_components

        settings = Settings(
            _env_file=None,
            model={
                "enabled": True,
                "experiments_dir": str(tmp_path / "experiments"),
                "model_type": "lgbm",
                "active_experiment_id": "nonexistent_id",
            },
        )
        result = _build_ml_components(settings)
        assert result is None


class TestOhlcvBackfill:
    def test_backfill_failure_returns_1(self, tmp_path: Path, monkeypatch) -> None:
        """OHLCV バックフィル失敗時に main が 1 を返す。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from cryptbot.exchanges.base import ExchangeError

        monkeypatch.setenv("CRYPT_MODE", "live")
        monkeypatch.setenv("CRYPT_BITBANK_API_KEY", "test_key")
        monkeypatch.setenv("CRYPT_BITBANK_API_SECRET", "test_secret")

        db_path = tmp_path / "live.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        mock_updater = MagicMock()
        mock_updater.backfill = AsyncMock(side_effect=ExchangeError("API timeout"))

        with (
            patch("cryptbot.live.gate.phase_4_gate"),
            patch("cryptbot.exchanges.bitbank_private.BitbankPrivateExchange"),
            patch("cryptbot.exchanges.bitbank.BitbankExchange"),
            patch("cryptbot.data.fetcher.DataFetcher"),
            patch("cryptbot.data.ohlcv_updater.OhlcvUpdater", return_value=mock_updater),
        ):
            result = main(["--mode", "live", "--confirm-live",
                           "--db", str(db_path), "--data-dir", str(data_dir)])

        assert result == 1
