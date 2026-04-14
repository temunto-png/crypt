"""cryptbot.main と cryptbot.tools.verify_audit_log のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cryptbot.main import main, LiveTradingNotImplementedError
from cryptbot.tools.verify_audit_log import main as verify_main


# ------------------------------------------------------------------ #
# cryptbot.main
# ------------------------------------------------------------------ #

class TestMain:
    def test_help_exits_zero(self) -> None:
        """`--help` が SystemExit(0) で終了すること。"""
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_paper_mode_returns_zero(self, capsys) -> None:
        """paper モード（デフォルト）が 0 を返すこと。"""
        result = main([])
        assert result == 0

    def test_paper_mode_explicit_returns_zero(self) -> None:
        """--mode paper が 0 を返すこと。"""
        result = main(["--mode", "paper"])
        assert result == 0

    def test_live_mode_raises(self) -> None:
        """`--mode live` で LiveTradingNotImplementedError が発生すること。"""
        with pytest.raises(LiveTradingNotImplementedError):
            main(["--mode", "live"])

    def test_confirm_live_flag_alone_raises(self) -> None:
        """`--confirm-live` だけでも LiveTradingNotImplementedError が発生すること。"""
        with pytest.raises(LiveTradingNotImplementedError):
            main(["--confirm-live"])

    def test_live_mode_with_confirm_raises(self) -> None:
        """`--mode live --confirm-live` でも LiveTradingNotImplementedError が発生すること。"""
        with pytest.raises(LiveTradingNotImplementedError):
            main(["--mode", "live", "--confirm-live"])


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
