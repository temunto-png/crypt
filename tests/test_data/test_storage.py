"""Storage クラスのテスト。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from cryptbot.data.storage import Storage, _compute_audit_hash, validate_ohlcv
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    """テスト用 Storage インスタンス（一時ディレクトリ使用）。"""
    s = Storage(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )
    s.initialize()
    return s


def _make_ohlcv_df(year: int = 2024, rows: int = 3) -> pd.DataFrame:
    """テスト用 OHLCV DataFrame を生成する。"""
    timestamps = pd.date_range(
        start=f"{year}-01-01 09:00:00",
        periods=rows,
        freq="1h",
        tz="Asia/Tokyo",
    )
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": [5_000_000.0 + i * 1000 for i in range(rows)],
        "high": [5_010_000.0 + i * 1000 for i in range(rows)],
        "low": [4_990_000.0 + i * 1000 for i in range(rows)],
        "close": [5_005_000.0 + i * 1000 for i in range(rows)],
        "volume": [1.5 + i * 0.1 for i in range(rows)],
    })


# ------------------------------------------------------------------ #
# P2-01: validate_ohlcv
# ------------------------------------------------------------------ #

class TestValidateOhlcv:
    def _base_df(self, rows: int = 3) -> pd.DataFrame:
        return _make_ohlcv_df(2024, rows=rows)

    def test_valid_df_passes(self) -> None:
        """正常な DataFrame はエラーなく通過する。"""
        validate_ohlcv(self._base_df())

    def test_empty_df_passes(self) -> None:
        """空の DataFrame はエラーなく通過する（行なし）。"""
        df = self._base_df(rows=0)
        validate_ohlcv(df)

    def test_missing_column_raises(self) -> None:
        """必須カラムが欠落すると ValueError。"""
        df = self._base_df().drop(columns=["volume"])
        with pytest.raises(ValueError, match="必須カラム"):
            validate_ohlcv(df)

    def test_duplicate_timestamp_raises(self) -> None:
        """重複 timestamp で ValueError。"""
        df = self._base_df(rows=3)
        df.loc[2, "timestamp"] = df.loc[0, "timestamp"]
        with pytest.raises(ValueError, match="重複"):
            validate_ohlcv(df)

    def test_high_below_open_raises(self) -> None:
        """high < open で ValueError。"""
        df = self._base_df(rows=3)
        df.loc[0, "high"] = df.loc[0, "open"] - 1000
        with pytest.raises(ValueError, match="high が open"):
            validate_ohlcv(df)

    def test_high_below_close_raises(self) -> None:
        """high < close で ValueError。"""
        df = self._base_df(rows=3)
        df.loc[0, "high"] = df.loc[0, "close"] - 1000
        with pytest.raises(ValueError, match="high が close"):
            validate_ohlcv(df)

    def test_low_above_open_raises(self) -> None:
        """low > open で ValueError。"""
        df = self._base_df(rows=3)
        df.loc[0, "low"] = df.loc[0, "open"] + 1000
        with pytest.raises(ValueError, match="low が open"):
            validate_ohlcv(df)

    def test_negative_price_raises(self) -> None:
        """価格に 0 以下の値があると ValueError（OHLC 整合性チェックより後に発火）。"""
        df = self._base_df(rows=3)
        # open=high=low=close=0 にすると OHLC 整合性は通過し、価格 <= 0 チェックで検出される
        df.loc[0, ["open", "high", "low", "close"]] = 0.0
        with pytest.raises(ValueError, match="0 以下"):
            validate_ohlcv(df)

    def test_negative_volume_raises(self) -> None:
        """volume に負の値があると ValueError。"""
        df = self._base_df(rows=3)
        df.loc[0, "volume"] = -1.0
        with pytest.raises(ValueError, match="volume に負"):
            validate_ohlcv(df)

    def test_non_monotonic_timestamp_raises(self) -> None:
        """timestamp が単調増加でない場合に ValueError。"""
        df = self._base_df(rows=3)
        # 2 行目と 1 行目の timestamp を入れ替え
        df.loc[1, "timestamp"], df.loc[0, "timestamp"] = (
            df.loc[0, "timestamp"],
            df.loc[1, "timestamp"],
        )
        with pytest.raises(ValueError, match="単調増加"):
            validate_ohlcv(df)


# ------------------------------------------------------------------ #
# initialize
# ------------------------------------------------------------------ #

class TestInitialize:
    def test_creates_tables(self, storage: Storage) -> None:
        """initialize 後に全テーブルが存在する。"""
        import sqlite3
        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert {"ohlcv_metadata", "audit_log", "orders", "order_events", "config_snapshot"} <= tables

    def test_idempotent(self, storage: Storage) -> None:
        """2 回呼び出しても例外が発生しない。"""
        storage.initialize()  # 2 回目


# ------------------------------------------------------------------ #
# OHLCV
# ------------------------------------------------------------------ #

class TestOhlcv:
    def test_save_and_load_roundtrip(self, storage: Storage) -> None:
        """save_ohlcv → load_ohlcv で同じ行数・列が返る。"""
        df = _make_ohlcv_df(2024, rows=5)
        path = storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

        assert path.exists()
        loaded = storage.load_ohlcv("btc_jpy", "1hour")

        assert len(loaded) == 5
        assert set(loaded.columns) >= {"timestamp", "open", "high", "low", "close", "volume"}

    def test_parquet_path_convention(self, storage: Storage, tmp_path: Path) -> None:
        """ファイルパスが data/{pair}/{timeframe}/{YYYY}.parquet になる。"""
        df = _make_ohlcv_df(2024)
        path = storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)
        assert path == tmp_path / "data" / "btc_jpy" / "1hour" / "2024.parquet"

    def test_metadata_recorded(self, storage: Storage) -> None:
        """save_ohlcv 後に ohlcv_metadata に 1 行挿入される。"""
        import sqlite3
        df = _make_ohlcv_df(2024)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM ohlcv_metadata").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["pair"] == "btc_jpy"  # pair
        assert rows[0]["timeframe"] == "1hour"    # timeframe
        assert rows[0]["row_count"] == 3           # row_count

    def test_load_empty_when_no_data(self, storage: Storage) -> None:
        """データが存在しない場合は空 DataFrame が返る。"""
        loaded = storage.load_ohlcv("btc_jpy", "1hour")
        assert loaded.empty

    def test_load_with_start_end_filter(self, storage: Storage) -> None:
        """start/end フィルタが正しく機能する。"""
        df = _make_ohlcv_df(2024, rows=10)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

        start = datetime(2024, 1, 1, 11, 0, 0, tzinfo=JST)
        end = datetime(2024, 1, 1, 13, 0, 0, tzinfo=JST)
        loaded = storage.load_ohlcv("btc_jpy", "1hour", start=start, end=end)

        assert len(loaded) == 3  # 11:00, 12:00, 13:00

    def test_load_multiple_years(self, storage: Storage) -> None:
        """複数年の Parquet が結合して返される。"""
        df2023 = _make_ohlcv_df(2023, rows=2)
        df2024 = _make_ohlcv_df(2024, rows=3)
        storage.save_ohlcv(df2023, "btc_jpy", "1hour", 2023)
        storage.save_ohlcv(df2024, "btc_jpy", "1hour", 2024)

        loaded = storage.load_ohlcv("btc_jpy", "1hour")
        assert len(loaded) == 5

    def test_save_missing_column_raises(self, storage: Storage) -> None:
        """必須カラム不足で ValueError が発生する。"""
        df = _make_ohlcv_df()
        df = df.drop(columns=["volume"])
        with pytest.raises(ValueError, match="volume"):
            storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

    # ------------------------------------------------------------------ #
    # P2-01: data_hash / has_ohlcv / verify_ohlcv_integrity
    # ------------------------------------------------------------------ #

    def test_data_hash_stored_in_metadata(self, storage: Storage) -> None:
        """save_ohlcv 後に ohlcv_metadata.data_hash が 64 文字の hex 文字列で保存されること。"""
        import sqlite3
        df = _make_ohlcv_df(2024, rows=5)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data_hash FROM ohlcv_metadata").fetchone()
        conn.close()
        assert row is not None
        assert len(row["data_hash"]) == 64

    def test_has_ohlcv_false_when_file_missing(self, storage: Storage, tmp_path: Path) -> None:
        """Parquet ファイルが存在しない場合 has_ohlcv() は False を返す。"""
        df = _make_ohlcv_df(2024)
        path = storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)
        path.unlink()  # ファイルを削除
        assert storage.has_ohlcv("btc_jpy", "1hour", 2024) is False

    def test_has_ohlcv_false_when_metadata_only(self, storage: Storage) -> None:
        """メタデータはあるがファイルが存在しない場合 has_ohlcv() は False を返す。"""
        # metadata のみ存在するケースは has_ohlcv のファイル確認で False になる
        assert storage.has_ohlcv("btc_jpy", "1hour", 2024) is False

    def test_verify_ohlcv_integrity_passes(self, storage: Storage) -> None:
        """正常に保存されたファイルは verify_ohlcv_integrity() が True を返す。"""
        df = _make_ohlcv_df(2024, rows=5)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)
        assert storage.verify_ohlcv_integrity("btc_jpy", "1hour", 2024) is True

    def test_verify_ohlcv_integrity_fails_after_file_tamper(
        self, storage: Storage
    ) -> None:
        """Parquet ファイルを書き換えると verify_ohlcv_integrity() が False を返す。"""
        df = _make_ohlcv_df(2024, rows=3)
        path = storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)
        # ファイルの末尾に余分なバイトを追加して改ざん
        with open(path, "ab") as f:
            f.write(b"\x00" * 10)
        assert storage.verify_ohlcv_integrity("btc_jpy", "1hour", 2024) is False

    def test_verify_ohlcv_integrity_false_when_no_file(self, storage: Storage) -> None:
        """ファイルが存在しない場合 verify_ohlcv_integrity() は False を返す。"""
        assert storage.verify_ohlcv_integrity("btc_jpy", "1hour", 2024) is False

    def test_source_exchange_stored(self, storage: Storage) -> None:
        """source_exchange が ohlcv_metadata に保存されること。"""
        import sqlite3
        df = _make_ohlcv_df(2024, rows=3)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024, source_exchange="bitbank")

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT source_exchange FROM ohlcv_metadata").fetchone()
        conn.close()
        assert row["source_exchange"] == "bitbank"

    def test_load_naive_datetime_raises(self, storage: Storage) -> None:
        """timezone-naive な start/end で ValueError が発生する。"""
        with pytest.raises(ValueError, match="timezone-aware"):
            storage.load_ohlcv("btc_jpy", "1hour", start=datetime(2024, 1, 1))

    def test_overwrite_upserts_metadata(self, storage: Storage) -> None:
        """同じ pair/timeframe/year を 2 回保存しても ohlcv_metadata は 1 行のみ。"""
        import sqlite3
        df1 = _make_ohlcv_df(2024, rows=3)
        df2 = _make_ohlcv_df(2024, rows=5)
        storage.save_ohlcv(df1, "btc_jpy", "1hour", 2024)
        storage.save_ohlcv(df2, "btc_jpy", "1hour", 2024)

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT row_count FROM ohlcv_metadata").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["row_count"] == 5


# ------------------------------------------------------------------ #
# audit_log
# ------------------------------------------------------------------ #

class TestAuditLog:
    def test_insert_and_verify(self, storage: Storage) -> None:
        """1 件挿入後にチェーン検証が True になる。"""
        storage.insert_audit_log("signal", direction="BUY", signal_confidence=0.8)
        assert storage.verify_audit_chain() is True

    def test_multiple_inserts_verify(self, storage: Storage) -> None:
        """複数件挿入後もチェーン検証が True になる。"""
        for i in range(5):
            storage.insert_audit_log(
                "signal",
                direction="BUY" if i % 2 == 0 else "SELL",
                signal_confidence=0.5 + i * 0.05,
            )
        assert storage.verify_audit_chain() is True

    def test_empty_chain_verify(self, storage: Storage) -> None:
        """レコードが 0 件でも検証が True になる。"""
        assert storage.verify_audit_chain() is True

    def test_tampered_chain_detected(self, storage: Storage) -> None:
        """prev_hash を改ざんするとチェーン検証が False になる。"""
        import sqlite3
        storage.insert_audit_log("signal", direction="BUY")
        storage.insert_audit_log("fill", fill_price=5_000_000.0, fill_size=0.01)

        # trigger を迂回して 2 番目のレコードの prev_hash を改ざん
        conn = sqlite3.connect(storage._db_path)
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("UPDATE audit_log SET prev_hash = 'tampered' WHERE id = 2")
        conn.commit()
        conn.close()

        assert storage.verify_audit_chain() is False

    def test_unknown_field_raises(self, storage: Storage) -> None:
        """未知フィールドで ValueError が発生する。"""
        with pytest.raises(ValueError, match="未知のフィールド"):
            storage.insert_audit_log("signal", unknown_field="x")

    def test_genesis_hash_on_first_insert(self, storage: Storage) -> None:
        """初回レコードの prev_hash が GENESIS を起点に計算される。"""
        import sqlite3
        storage.insert_audit_log("no_trade")

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT prev_hash FROM audit_log WHERE id = 1").fetchone()
        conn.close()
        assert row["prev_hash"] == "GENESIS"


# ------------------------------------------------------------------ #
# orders
# ------------------------------------------------------------------ #

class TestOrders:
    def _base_order(self) -> dict:
        return {
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "limit",
            "price": 5_000_000.0,
            "size": 0.01,
            "status": "CREATED",
        }

    def test_insert_order_returns_id(self, storage: Storage) -> None:
        """insert_order が整数 ID を返す。"""
        order_id = storage.insert_order(self._base_order())
        assert isinstance(order_id, int)
        assert order_id >= 1

    def test_update_order_status(self, storage: Storage) -> None:
        """update_order_status でステータスが更新される（CREATED → SUBMITTED → FILLED）。"""
        import sqlite3
        order_id = storage.insert_order(self._base_order())
        storage.update_order_status(order_id, "SUBMITTED")
        storage.update_order_status(order_id, "FILLED", fill_price=5_001_000.0, fill_size=0.01)

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, fill_price FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        conn.close()
        assert row["status"] == "FILLED"
        assert row["fill_price"] == pytest.approx(5_001_000.0)

    def test_insert_missing_required_raises(self, storage: Storage) -> None:
        """必須フィールド不足で ValueError が発生する。"""
        order = self._base_order()
        del order["size"]
        with pytest.raises(ValueError, match="size"):
            storage.insert_order(order)

    def test_update_nonexistent_order_raises(self, storage: Storage) -> None:
        """存在しない order_id で ValueError が発生する。"""
        with pytest.raises(ValueError, match="order_id=9999"):
            storage.update_order_status(9999, "CANCELLED")

    def test_update_unknown_field_raises(self, storage: Storage) -> None:
        """未知フィールドで ValueError が発生する。"""
        order_id = storage.insert_order(self._base_order())
        with pytest.raises(ValueError, match="未知のフィールド"):
            storage.update_order_status(order_id, "SUBMITTED", unknown="x")

    def test_valid_state_transition(self, storage: Storage) -> None:
        """CREATED → SUBMITTED → FILLED の正常遷移が成功すること。"""
        order_id = storage.insert_order(self._base_order())
        storage.update_order_status(order_id, "SUBMITTED")
        storage.update_order_status(order_id, "FILLED", fill_price=5_001_000.0, fill_size=0.01)

        import sqlite3
        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.close()
        assert row["status"] == "FILLED"

    def test_invalid_state_transition_raises(self, storage: Storage) -> None:
        """FILLED → CREATED の不正遷移が ValueError を発生させること。"""
        order_id = storage.insert_order(self._base_order())
        storage.update_order_status(order_id, "SUBMITTED")
        storage.update_order_status(order_id, "FILLED", fill_price=5_001_000.0)
        with pytest.raises(ValueError, match="不正な状態遷移"):
            storage.update_order_status(order_id, "CREATED")

    def test_terminal_state_cannot_transition(self, storage: Storage) -> None:
        """終端状態（CANCELLED）からは遷移不可。"""
        order_id = storage.insert_order(self._base_order())
        storage.update_order_status(order_id, "CANCELLED")
        with pytest.raises(ValueError, match="不正な状態遷移"):
            storage.update_order_status(order_id, "SUBMITTED")

    def test_duplicate_active_order_raises(self, storage: Storage) -> None:
        """同一 pair の active 注文が 2 件 insert されると IntegrityError が発生すること（P2-01）。"""
        import sqlite3
        storage.insert_order(self._base_order())  # CREATED (BUY)
        with pytest.raises(sqlite3.IntegrityError):
            storage.insert_order(self._base_order())  # 同一 pair active → unique 違反

    def test_sell_while_buy_active_raises(self, storage: Storage) -> None:
        """BUY active 中に同一 pair の SELL を insert すると IntegrityError が発生すること（P2-01）。"""
        import sqlite3
        storage.insert_order(self._base_order())  # BUY CREATED
        sell_order = {**self._base_order(), "side": "SELL"}
        with pytest.raises(sqlite3.IntegrityError):
            storage.insert_order(sell_order)  # 同一 pair に SELL active → unique 違反

    def test_active_constraint_after_fill(self, storage: Storage) -> None:
        """FILLED 後は同一 pair/side の新規 active 注文を insert できること。"""
        order_id = storage.insert_order(self._base_order())
        storage.update_order_status(order_id, "SUBMITTED")
        storage.update_order_status(order_id, "FILLED", fill_price=5_001_000.0)
        # FILLED は active ではないので新規注文を入れられる
        new_id = storage.insert_order(self._base_order())
        assert new_id != order_id


# ------------------------------------------------------------------ #
# P1-06: order_events テスト
# ------------------------------------------------------------------ #

class TestOrderEvents:
    def _base_order(self) -> dict:
        return {
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "market",
            "size": 0.01,
            "status": "CREATED",
        }

    def test_insert_order_event(self, storage: Storage) -> None:
        """insert_order_event で order_events にレコードが作成されること。"""
        import sqlite3
        order_id = storage.insert_order(self._base_order())
        event_id = storage.insert_order_event(
            order_id, "SUBMITTED", note="sent to exchange"
        )
        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM order_events WHERE id = ?", (event_id,)
        ).fetchone()
        conn.close()
        assert row["order_id"] == order_id
        assert row["event_type"] == "SUBMITTED"
        assert row["note"] == "sent to exchange"

    def test_partial_fill_event_with_price(self, storage: Storage) -> None:
        """PARTIAL_FILL イベントで fill_price / fill_size が記録されること。"""
        import sqlite3
        order_id = storage.insert_order(self._base_order())
        storage.insert_order_event(
            order_id, "PARTIAL_FILL",
            fill_price=5_000_000.0,
            fill_size=0.005,
            fee=250.0,
        )
        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM order_events WHERE order_id = ?", (order_id,)
        ).fetchone()
        conn.close()
        assert row["fill_price"] == pytest.approx(5_000_000.0)
        assert row["fill_size"] == pytest.approx(0.005)

    def test_insert_event_unknown_order_raises(self, storage: Storage) -> None:
        """存在しない order_id で insert_order_event すると ValueError が発生すること。"""
        with pytest.raises(ValueError, match="order_id=9999"):
            storage.insert_order_event(9999, "SUBMITTED")


# ------------------------------------------------------------------ #
# config_snapshot
# ------------------------------------------------------------------ #

class TestConfigSnapshot:
    def test_save_and_retrieve(self, storage: Storage) -> None:
        """save_config_snapshot で保存した設定が DB に記録される。"""
        import sqlite3
        run_id = str(uuid.uuid4())
        config = {"strategy": "sma_cross", "fast": 5, "slow": 20}
        storage.save_config_snapshot(run_id, config)

        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT run_id, config_json FROM config_snapshot WHERE run_id = ?", (run_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["run_id"] == run_id
        import json
        assert json.loads(row["config_json"]) == config

    def test_duplicate_run_id_raises(self, storage: Storage) -> None:
        """同一 run_id を 2 回保存すると ValueError が発生する。"""
        run_id = str(uuid.uuid4())
        storage.save_config_snapshot(run_id, {"a": 1})
        with pytest.raises(ValueError, match=run_id):
            storage.save_config_snapshot(run_id, {"b": 2})


# ------------------------------------------------------------------ #
# _compute_audit_hash ユーティリティ
# ------------------------------------------------------------------ #

class TestComputeAuditHash:
    def test_deterministic(self) -> None:
        """同じ入力に対して同じハッシュが返る。"""
        record = {"event_type": "signal", "direction": "BUY"}
        h1 = _compute_audit_hash("GENESIS", record)
        h2 = _compute_audit_hash("GENESIS", record)
        assert h1 == h2

    def test_different_prev_hash(self) -> None:
        """prev_hash が異なれば結果も異なる。"""
        record = {"event_type": "signal"}
        h1 = _compute_audit_hash("GENESIS", record)
        h2 = _compute_audit_hash("OTHER", record)
        assert h1 != h2

    def test_sha256_length(self) -> None:
        """SHA256 ハッシュは 64 文字の hex 文字列。"""
        h = _compute_audit_hash("GENESIS", {"x": 1})
        assert len(h) == 64


# ------------------------------------------------------------------ #
# P1-03: audit_log record_hash + UPDATE/DELETE trigger
# ------------------------------------------------------------------ #

class TestAuditLogTampering:
    def test_record_hash_stored_in_audit_log(self, storage: Storage) -> None:
        """insert_audit_log() 後に record_hash カラムに値が保存されること。"""
        import sqlite3
        storage.insert_audit_log("SIGNAL", strategy="test", direction="BUY")
        conn = sqlite3.connect(storage._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT record_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert row is not None
        assert row["record_hash"] and len(row["record_hash"]) == 64

    def test_verify_audit_chain_passes_after_inserts(self, storage: Storage) -> None:
        """正常な挿入後は verify_audit_chain() が True を返すこと。"""
        storage.insert_audit_log("SIGNAL", strategy="s1", direction="BUY", signal_confidence=0.8)
        storage.insert_audit_log("FILL", fill_price=5_000_000.0, fill_size=0.01)
        storage.insert_audit_log("KILL_SWITCH_ACTIVATED", signal_reason="max_drawdown")
        assert storage.verify_audit_chain() is True

    def test_verify_detects_tail_record_tampering(self, storage: Storage) -> None:
        """最終行の signal_reason を手動 UPDATE すると verify_audit_chain() が False を返すこと。
        （UPDATE trigger が無効化されている環境での改ざん検知を想定したテスト）
        """
        import sqlite3

        storage.insert_audit_log("SIGNAL", signal_reason="original")
        storage.insert_audit_log("FILL", fill_price=5_000_000.0)

        # trigger を迂回して直接 UPDATE で改ざんする
        conn = sqlite3.connect(storage._db_path)
        # trigger を一時無効化して改ざん
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET signal_reason = 'tampered' WHERE id = (SELECT MIN(id) FROM audit_log)"
        )
        conn.commit()
        conn.close()

        # verify が改ざんを検知すること
        assert storage.verify_audit_chain() is False

    def test_audit_log_update_blocked_by_trigger(self, storage: Storage) -> None:
        """audit_log への UPDATE が trigger で拒否されること。
        RAISE(ABORT, ...) は sqlite3.IntegrityError を送出する。
        """
        import sqlite3

        storage.insert_audit_log("SIGNAL", signal_reason="original")

        conn = sqlite3.connect(storage._db_path)
        with pytest.raises(sqlite3.IntegrityError, match="禁止"):
            conn.execute(
                "UPDATE audit_log SET signal_reason = 'hacked' WHERE id = 1"
            )
        conn.close()

    def test_audit_log_delete_blocked_by_trigger(self, storage: Storage) -> None:
        """audit_log への DELETE が trigger で拒否されること。
        RAISE(ABORT, ...) は sqlite3.IntegrityError を送出する。
        """
        import sqlite3

        storage.insert_audit_log("SIGNAL")

        conn = sqlite3.connect(storage._db_path)
        with pytest.raises(sqlite3.IntegrityError, match="禁止"):
            conn.execute("DELETE FROM audit_log WHERE id = 1")
        conn.close()
