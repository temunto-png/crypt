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
