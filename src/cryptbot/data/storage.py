"""SQLite + Parquet データ保存・読み込みモジュール。

SQLite: メタデータ・監査ログ・注文記録・設定スナップショット
Parquet: OHLCV 時系列データ（pyarrow 使用）

ファイルパス規則: data/{pair}/{timeframe}/{YYYY}.parquet
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cryptbot.utils.time_utils import JST, now_jst

# Parquet スキーマ定義
_OHLCV_SCHEMA = pa.schema([
    pa.field("timestamp", pa.timestamp("ns", tz="Asia/Tokyo")),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()),
])


def _compute_audit_hash(prev_hash: str, record: dict[str, Any]) -> str:
    """前レコードのハッシュ + 現レコードの内容から SHA256 を計算する。"""
    content = prev_hash + json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def _compute_file_hash(file_path: Path) -> str:
    """ファイルの SHA256 ハッシュを返す（64 KB チャンク読み込み）。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_ohlcv(df: pd.DataFrame) -> None:
    """OHLCV DataFrame の整合性を検証する。

    以下の条件のいずれかを満たす場合は ValueError を発生させる。

    - 必須カラムが不足している
    - 重複した timestamp がある
    - high < open または high < close（OHLC 不整合）
    - low > open または low > close（OHLC 不整合）
    - high < low（OHLC 不整合）
    - 価格（open/high/low/close）に 0 以下の値がある
    - volume に負の値がある
    - timestamp が単調増加でない（重複除く）

    Args:
        df: 検証対象の OHLCV DataFrame

    Raises:
        ValueError: 整合性チェックに失敗した場合
    """
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"必須カラムが不足しています: {missing}")

    if df.empty:
        return

    # 重複 timestamp
    if df["timestamp"].duplicated().any():
        dupes = df["timestamp"][df["timestamp"].duplicated()].iloc[0]
        raise ValueError(f"重複した timestamp が存在します: {dupes}")

    # OHLC 整合性
    if (df["high"] < df["open"]).any():
        raise ValueError("high が open を下回っている行があります")
    if (df["high"] < df["close"]).any():
        raise ValueError("high が close を下回っている行があります")
    if (df["low"] > df["open"]).any():
        raise ValueError("low が open を上回っている行があります")
    if (df["low"] > df["close"]).any():
        raise ValueError("low が close を上回っている行があります")
    if (df["high"] < df["low"]).any():
        raise ValueError("high が low を下回っている行があります")

    # 価格・出来高の妥当性
    price_cols = ["open", "high", "low", "close"]
    if (df[price_cols] <= 0).any().any():
        raise ValueError("価格カラム（open/high/low/close）に 0 以下の値があります")
    if (df["volume"] < 0).any():
        raise ValueError("volume に負の値があります")

    # 単調増加チェック（timestamp が tzaware の場合も対応）
    ts = df["timestamp"]
    if not ts.is_monotonic_increasing:
        raise ValueError("timestamp が単調増加ではありません（並び替えが必要です）")


class Storage:
    """SQLite + Parquet データ保存・読み込みクラス。

    Args:
      db_path: SQLite データベースファイルのパス
      data_dir: Parquet ファイルの格納ディレクトリ
    """

    def __init__(self, db_path: str | Path, data_dir: str | Path) -> None:
        self._db_path = Path(db_path)
        self._data_dir = Path(data_dir)

    def _connect(self) -> sqlite3.Connection:
        """SQLite 接続を返す（foreign_keys ON / WAL モード）。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        """テーブル作成（存在しない場合のみ）。ディレクトリも作成する。
        既存 DB に audit_log.record_hash カラムが存在しない場合は追加する（マイグレーション）。
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ohlcv_metadata (
                  id               INTEGER PRIMARY KEY AUTOINCREMENT,
                  pair             TEXT NOT NULL,
                  timeframe        TEXT NOT NULL,
                  from_dt          TEXT NOT NULL,
                  to_dt            TEXT NOT NULL,
                  file_path        TEXT NOT NULL,
                  row_count        INTEGER NOT NULL,
                  data_hash        TEXT NOT NULL DEFAULT '',
                  source_exchange  TEXT,
                  fetch_timestamp  TEXT,
                  created_at       TEXT NOT NULL,
                  UNIQUE(pair, timeframe, file_path)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                  id                INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp         TEXT NOT NULL,
                  event_type        TEXT NOT NULL,
                  strategy          TEXT,
                  direction         TEXT,
                  signal_confidence REAL,
                  signal_reason     TEXT,
                  fill_price        REAL,
                  fill_size         REAL,
                  fee               REAL,
                  slippage_pct      REAL,
                  portfolio_value   REAL,
                  drawdown_pct      REAL,
                  raw_market_data   TEXT,
                  prev_hash         TEXT NOT NULL,
                  record_hash       TEXT NOT NULL DEFAULT ''
                );

                -- audit_log の改ざん防止: UPDATE / DELETE を禁止する
                CREATE TRIGGER IF NOT EXISTS audit_log_no_update
                  BEFORE UPDATE ON audit_log
                BEGIN
                  SELECT RAISE(ABORT, 'audit_log への UPDATE は禁止されています');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
                  BEFORE DELETE ON audit_log
                BEGIN
                  SELECT RAISE(ABORT, 'audit_log への DELETE は禁止されています');
                END;

                CREATE TABLE IF NOT EXISTS orders (
                  id                INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at        TEXT NOT NULL,
                  pair              TEXT NOT NULL,
                  side              TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                  order_type        TEXT NOT NULL,
                  price             REAL,
                  size              REAL NOT NULL,
                  status            TEXT NOT NULL
                    CHECK(status IN ('CREATED', 'SUBMITTED', 'PARTIAL', 'FILLED', 'CANCELLED', 'FAILED')),
                  exchange_order_id TEXT,
                  fill_price        REAL,
                  fill_size         REAL,
                  fee               REAL,
                  strategy          TEXT,
                  updated_at        TEXT NOT NULL
                );

                -- Long-only MVP: 同一 pair の active（CREATED/SUBMITTED/PARTIAL）注文は 1 件のみ許容する
                -- (pair, side) から (pair) に変更: BUY active 中の SELL active を防ぐ
                CREATE UNIQUE INDEX IF NOT EXISTS orders_active_unique
                  ON orders(pair)
                  WHERE status IN ('CREATED', 'SUBMITTED', 'PARTIAL');

                -- 注文のイベント履歴（append-only: partial fill / cancel 等を記録）
                CREATE TABLE IF NOT EXISTS order_events (
                  id         INTEGER PRIMARY KEY AUTOINCREMENT,
                  order_id   INTEGER NOT NULL REFERENCES orders(id),
                  created_at TEXT NOT NULL,
                  event_type TEXT NOT NULL
                    CHECK(event_type IN ('SUBMITTED', 'PARTIAL_FILL', 'FILLED', 'CANCELLED', 'FAILED', 'ERROR')),
                  fill_price REAL,
                  fill_size  REAL,
                  fee        REAL,
                  note       TEXT
                );

                CREATE TABLE IF NOT EXISTS config_snapshot (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at  TEXT NOT NULL,
                  run_id      TEXT NOT NULL UNIQUE,
                  config_json TEXT NOT NULL
                );
            """)

            # マイグレーション: orders_active_unique を (pair, side) → (pair) に変更
            # Long-only MVP では同一 pair に active 注文は 1 件のみ許容する
            idx_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='orders_active_unique'"
            ).fetchone()
            if idx_row is not None and idx_row[0] is not None and "(pair, side)" in idx_row[0]:
                conn.execute("DROP INDEX orders_active_unique")
                conn.execute(
                    """
                    CREATE UNIQUE INDEX orders_active_unique
                      ON orders(pair)
                      WHERE status IN ('CREATED', 'SUBMITTED', 'PARTIAL')
                    """
                )

            # マイグレーション: record_hash カラムが存在しない既存 DB への対応
            al_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
            if "record_hash" not in al_cols:
                conn.execute(
                    "ALTER TABLE audit_log ADD COLUMN record_hash TEXT NOT NULL DEFAULT ''"
                )

            # マイグレーション: ohlcv_metadata に新カラムを追加
            meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(ohlcv_metadata)").fetchall()}
            if "data_hash" not in meta_cols:
                conn.execute(
                    "ALTER TABLE ohlcv_metadata ADD COLUMN data_hash TEXT NOT NULL DEFAULT ''"
                )
            if "source_exchange" not in meta_cols:
                conn.execute(
                    "ALTER TABLE ohlcv_metadata ADD COLUMN source_exchange TEXT"
                )
            if "fetch_timestamp" not in meta_cols:
                conn.execute(
                    "ALTER TABLE ohlcv_metadata ADD COLUMN fetch_timestamp TEXT"
                )

    # ------------------------------------------------------------------ #
    # OHLCV
    # ------------------------------------------------------------------ #

    def _ohlcv_path(self, pair: str, timeframe: str, year: int) -> Path:
        """Parquet ファイルパスを返す。"""
        for name, value in (("pair", pair), ("timeframe", timeframe)):
            if ".." in value or "/" in value or "\\" in value:
                raise ValueError(f"{name} に不正な文字が含まれています: {value!r}")
        return self._data_dir / pair / timeframe / f"{year}.parquet"

    def save_ohlcv(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
        year: int,
        *,
        source_exchange: str | None = None,
    ) -> Path:
        """OHLCV DataFrame を Parquet に書き込み、ohlcv_metadata に記録する。

        保存前に validate_ohlcv() で整合性チェックを行い、書き込み後に
        data_hash（SHA256）を計算してメタデータに保存する。

        Args:
          df: timestamp, open, high, low, close, volume カラムを持つ DataFrame
          pair: 通貨ペア（例: "btc_jpy"）
          timeframe: 時間足（例: "1hour"）
          year: 対象年
          source_exchange: データ取得元の取引所名（省略可）

        Returns:
          書き込んだ Parquet ファイルのパス

        Raises:
          ValueError: 必須カラムが不足している、または OHLCV 整合性チェックに失敗した場合
        """
        # 整合性チェック（重複・OHLC 不整合・価格異常 etc.）
        validate_ohlcv(df)

        file_path = self._ohlcv_path(pair, timeframe, year)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # timestamp を JST timezone-aware に変換
        ts_col = df["timestamp"]
        if hasattr(ts_col.dtype, "tz") and ts_col.dtype.tz is not None:
            ts_col = ts_col.dt.tz_convert("Asia/Tokyo")
        else:
            ts_col = ts_col.dt.tz_localize("Asia/Tokyo")

        df = df.copy()
        df["timestamp"] = ts_col

        table = pa.Table.from_pandas(
            df[["timestamp", "open", "high", "low", "close", "volume"]],
            schema=_OHLCV_SCHEMA,
            preserve_index=False,
        )
        pq.write_table(table, file_path, compression="snappy")

        # 書き込み後に data_hash を計算（ファイルバイト列の SHA256）
        data_hash = _compute_file_hash(file_path)

        # メタデータ記録
        from_dt = ts_col.min()
        to_dt = ts_col.max()
        created_at = now_jst().isoformat()
        fetch_timestamp = created_at

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ohlcv_metadata
                  (pair, timeframe, from_dt, to_dt, file_path, row_count,
                   data_hash, source_exchange, fetch_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair, timeframe, file_path) DO UPDATE SET
                  from_dt          = excluded.from_dt,
                  to_dt            = excluded.to_dt,
                  row_count        = excluded.row_count,
                  data_hash        = excluded.data_hash,
                  source_exchange  = excluded.source_exchange,
                  fetch_timestamp  = excluded.fetch_timestamp,
                  created_at       = excluded.created_at
                """,
                (
                    pair,
                    timeframe,
                    from_dt.isoformat(),
                    to_dt.isoformat(),
                    str(file_path),
                    len(df),
                    data_hash,
                    source_exchange,
                    fetch_timestamp,
                    created_at,
                ),
            )

        return file_path

    def has_ohlcv(self, pair: str, timeframe: str, year: int) -> bool:
        """指定した年の OHLCV データが既に保存されているかを確認する。

        メタデータの存在とファイルの存在を両方確認する。
        どちらか一方でも欠けている場合は False を返す。
        ハッシュ照合は行わない（verify_ohlcv_integrity() を使用）。

        Args:
          pair: 通貨ペア（例: "btc_jpy"）
          timeframe: 時間足（例: "1hour"）
          year: 対象年

        Returns:
          メタデータとファイルがともに存在する場合 True
        """
        path = self._ohlcv_path(pair, timeframe, year)
        if not path.exists():
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM ohlcv_metadata WHERE pair=? AND timeframe=? AND file_path=?",
                (pair, timeframe, str(path)),
            ).fetchone()
            return row is not None

    def verify_ohlcv_integrity(self, pair: str, timeframe: str, year: int) -> bool:
        """指定した年の Parquet ファイルのハッシュをメタデータと照合する。

        Args:
          pair: 通貨ペア
          timeframe: 時間足
          year: 対象年

        Returns:
          True: ファイルが存在し、メタデータのハッシュと一致する
          False: ファイルが存在しない、メタデータが存在しない、
                 または保存済みハッシュが一致しない（未計算の場合も False）
        """
        path = self._ohlcv_path(pair, timeframe, year)
        if not path.exists():
            return False

        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_hash FROM ohlcv_metadata WHERE pair=? AND timeframe=? AND file_path=?",
                (pair, timeframe, str(path)),
            ).fetchone()

        if row is None:
            return False

        stored_hash = row["data_hash"]
        if not stored_hash:
            # 旧データ（ハッシュ未計算）: 照合不可
            return False

        actual_hash = _compute_file_hash(path)
        return stored_hash == actual_hash

    def load_ohlcv(
        self,
        pair: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """条件に合う Parquet を読み込み結合して返す。

        Args:
          pair: 通貨ペア
          timeframe: 時間足
          start: フィルタ開始日時（timezone-aware）
          end: フィルタ終了日時（timezone-aware）

        Returns:
          OHLCV DataFrame（timestamp 昇順）。データなしの場合は空 DataFrame。

        Raises:
          ValueError: start/end が timezone-naive の場合
        """
        if start is not None and start.tzinfo is None:
            raise ValueError("start には timezone-aware な datetime が必要です。")
        if end is not None and end.tzinfo is None:
            raise ValueError("end には timezone-aware な datetime が必要です。")

        pair_dir = self._data_dir / pair / timeframe
        if not pair_dir.exists():
            return _empty_ohlcv_df()

        parquet_files = sorted(pair_dir.glob("*.parquet"))
        if not parquet_files:
            return _empty_ohlcv_df()

        frames: list[pd.DataFrame] = []
        for fp in parquet_files:
            table = pq.read_table(fp, schema=_OHLCV_SCHEMA)
            frames.append(table.to_pandas())

        if not frames:
            return _empty_ohlcv_df()

        df = pd.concat(frames, ignore_index=True)

        # timestamp を JST に統一
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("Asia/Tokyo")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Tokyo")

        if start is not None:
            start_jst = start.astimezone(JST)
            df = df[df["timestamp"] >= start_jst]
        if end is not None:
            end_jst = end.astimezone(JST)
            df = df[df["timestamp"] <= end_jst]

        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------ #
    # audit_log
    # ------------------------------------------------------------------ #

    def insert_audit_log(self, event_type: str, **kwargs: Any) -> None:
        """audit_log にレコードを追記する（ハッシュチェーン付き）。

        Args:
          event_type: イベント種別（signal/fill/risk_stop/kill_switch/no_trade）
          **kwargs: スキーマの任意フィールド
        """
        allowed_fields = {
            "strategy", "direction", "signal_confidence", "signal_reason",
            "fill_price", "fill_size", "fee", "slippage_pct",
            "portfolio_value", "drawdown_pct", "raw_market_data",
        }
        unknown = set(kwargs) - allowed_fields
        if unknown:
            raise ValueError(f"audit_log に未知のフィールドが指定されました: {unknown}")

        timestamp = now_jst().isoformat()

        # ハッシュチェーン: 最新レコードの prev_hash を取得
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, record_hash FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()

            if row is None:
                # 初回: GENESIS ハッシュから開始
                chain_seed = "GENESIS"
            else:
                # chain_seed = 前レコードの record_hash（prev_hash[i+1] = record_hash[i]）
                chain_seed = row["record_hash"] if row["record_hash"] else "GENESIS"

            # 現レコードのデータを組み立て
            record: dict[str, Any] = {
                "timestamp": timestamp,
                "event_type": event_type,
                "strategy": kwargs.get("strategy"),
                "direction": kwargs.get("direction"),
                "signal_confidence": kwargs.get("signal_confidence"),
                "signal_reason": kwargs.get("signal_reason"),
                "fill_price": kwargs.get("fill_price"),
                "fill_size": kwargs.get("fill_size"),
                "fee": kwargs.get("fee"),
                "slippage_pct": kwargs.get("slippage_pct"),
                "portfolio_value": kwargs.get("portfolio_value"),
                "drawdown_pct": kwargs.get("drawdown_pct"),
                "raw_market_data": kwargs.get("raw_market_data"),
            }

            # 現レコードの record_hash を計算（prev_hash + record 内容のハッシュ）
            current_record_hash = _compute_audit_hash(chain_seed, record)

            conn.execute(
                """
                INSERT INTO audit_log
                  (timestamp, event_type, strategy, direction, signal_confidence,
                   signal_reason, fill_price, fill_size, fee, slippage_pct,
                   portfolio_value, drawdown_pct, raw_market_data, prev_hash, record_hash)
                VALUES
                  (:timestamp, :event_type, :strategy, :direction, :signal_confidence,
                   :signal_reason, :fill_price, :fill_size, :fee, :slippage_pct,
                   :portfolio_value, :drawdown_pct, :raw_market_data, :prev_hash, :record_hash)
                """,
                {**record, "prev_hash": chain_seed, "record_hash": current_record_hash},
            )

    def verify_audit_chain(self) -> bool:
        """audit_log のハッシュチェーンを検証する。

        各レコードについて:
        1. stored_prev_hash が前レコードの record_hash と一致すること（チェーン連結の整合性）
        2. stored_record_hash == hash(prev_hash + record_fields)（レコード自身の改ざん検知、末尾行含む）

        Returns:
          全レコードのハッシュが一致する場合 True、改ざんが検出された場合 False
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id ASC"
            ).fetchall()

        if not rows:
            return True

        prev_record_hash = "GENESIS"
        for row in rows:
            record_dict = dict(row)
            stored_prev_hash = record_dict["prev_hash"]
            stored_record_hash = record_dict.get("record_hash", "")

            # 1. prev_hash チェーン整合性
            if stored_prev_hash != prev_record_hash:
                return False

            # record_hash 計算のためのフィールド（id / prev_hash / record_hash を除く）
            record_fields = {
                k: v for k, v in record_dict.items()
                if k not in ("id", "prev_hash", "record_hash")
            }

            # 2. record_hash 自身の整合性（末尾行改ざん検知）
            expected_record_hash = _compute_audit_hash(stored_prev_hash, record_fields)
            if stored_record_hash and stored_record_hash != expected_record_hash:
                return False

            prev_record_hash = stored_record_hash if stored_record_hash else expected_record_hash

        return True

    # ------------------------------------------------------------------ #
    # orders
    # ------------------------------------------------------------------ #

    def insert_order(self, order: dict[str, Any]) -> int:
        """orders テーブルに注文を挿入する。

        Args:
          order: 注文データ（pair, side, order_type, size, status 必須）

        Returns:
          挿入した行の id

        Raises:
          ValueError: 必須フィールドが不足している場合
        """
        required = {"pair", "side", "order_type", "size", "status"}
        missing = required - set(order)
        if missing:
            raise ValueError(f"order に必須フィールドが不足しています: {missing}")

        now = now_jst().isoformat()
        created_at = order.get("created_at", now)
        updated_at = order.get("updated_at", now)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders
                  (created_at, pair, side, order_type, price, size, status,
                   exchange_order_id, fill_price, fill_size, fee, strategy, updated_at)
                VALUES
                  (:created_at, :pair, :side, :order_type, :price, :size, :status,
                   :exchange_order_id, :fill_price, :fill_size, :fee, :strategy, :updated_at)
                """,
                {
                    "created_at": created_at,
                    "pair": order["pair"],
                    "side": order["side"],
                    "order_type": order["order_type"],
                    "price": order.get("price"),
                    "size": order["size"],
                    "status": order["status"],
                    "exchange_order_id": order.get("exchange_order_id"),
                    "fill_price": order.get("fill_price"),
                    "fill_size": order.get("fill_size"),
                    "fee": order.get("fee"),
                    "strategy": order.get("strategy"),
                    "updated_at": updated_at,
                },
            )
            return cursor.lastrowid  # type: ignore[return-value]

    # 許容される状態遷移マップ
    _VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
        "CREATED":   {"SUBMITTED", "CANCELLED", "FAILED"},
        "SUBMITTED": {"PARTIAL", "FILLED", "CANCELLED", "FAILED"},
        "PARTIAL":   {"FILLED", "CANCELLED", "FAILED"},
        "FILLED":    set(),       # 終端状態
        "CANCELLED": set(),       # 終端状態
        "FAILED":    set(),       # 終端状態
    }

    _VALID_STATUSES: frozenset[str] = frozenset(
        {"CREATED", "SUBMITTED", "PARTIAL", "FILLED", "CANCELLED", "FAILED"}
    )

    def update_order_status(
        self,
        order_id: int,
        status: str,
        **kwargs: Any,
    ) -> None:
        """orders テーブルの注文ステータスを更新する。

        状態遷移の妥当性を検証し、不正な遷移（FILLED → CREATED 等）を拒否する。

        Args:
          order_id: 更新対象の注文 ID
          status: 新しいステータス
          **kwargs: 追加更新フィールド（fill_price, fill_size, fee, exchange_order_id）

        Raises:
          ValueError: 対象の注文が存在しない場合、または不正な状態遷移の場合
        """
        if status not in self._VALID_STATUSES:
            raise ValueError(
                f"不正なステータス: {status!r}。"
                f"有効値: {sorted(self._VALID_STATUSES)}"
            )

        allowed_fields = {"fill_price", "fill_size", "fee", "exchange_order_id"}
        unknown = set(kwargs) - allowed_fields
        if unknown:
            raise ValueError(f"update_order_status に未知のフィールドが指定されました: {unknown}")

        updated_at = now_jst().isoformat()
        set_clauses = ["status = :status", "updated_at = :updated_at"]
        params: dict[str, Any] = {"status": status, "updated_at": updated_at, "order_id": order_id}

        for field in allowed_fields:
            if field in kwargs:
                set_clauses.append(f"{field} = :{field}")
                params[field] = kwargs[field]

        with self._connect() as conn:
            # 現在のステータスを取得して状態遷移を検証
            row = conn.execute(
                "SELECT status FROM orders WHERE id = :order_id", {"order_id": order_id}
            ).fetchone()
            if row is None:
                raise ValueError(f"order_id={order_id} の注文が存在しません。")

            current_status = row["status"]
            allowed_next = self._VALID_STATUS_TRANSITIONS.get(current_status, set())
            if status not in allowed_next:
                raise ValueError(
                    f"不正な状態遷移: {current_status} → {status}。"
                    f"許容される遷移先: {sorted(allowed_next) or '（終端状態）'}"
                )

            conn.execute(
                f"UPDATE orders SET {', '.join(set_clauses)} WHERE id = :order_id",
                params,
            )

    def insert_order_event(
        self,
        order_id: int,
        event_type: str,
        **kwargs: Any,
    ) -> int:
        """order_events テーブルにイベントを追加する（append-only）。

        Args:
          order_id: 対象の注文 ID
          event_type: イベント種別（SUBMITTED / PARTIAL_FILL / FILLED / CANCELLED / FAILED / ERROR）
          **kwargs: fill_price, fill_size, fee, note

        Returns:
          挿入した行の id

        Raises:
          ValueError: 未知のフィールドが指定された場合、または order_id が存在しない場合
        """
        allowed_fields = {"fill_price", "fill_size", "fee", "note"}
        unknown = set(kwargs) - allowed_fields
        if unknown:
            raise ValueError(f"insert_order_event に未知のフィールドが指定されました: {unknown}")

        created_at = now_jst().isoformat()
        with self._connect() as conn:
            # order_id 存在チェック（REFERENCES 制約より早期エラー）
            if conn.execute(
                "SELECT 1 FROM orders WHERE id = ?", (order_id,)
            ).fetchone() is None:
                raise ValueError(f"order_id={order_id} の注文が存在しません。")

            cursor = conn.execute(
                """
                INSERT INTO order_events
                  (order_id, created_at, event_type, fill_price, fill_size, fee, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    created_at,
                    event_type,
                    kwargs.get("fill_price"),
                    kwargs.get("fill_size"),
                    kwargs.get("fee"),
                    kwargs.get("note"),
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # config_snapshot
    # ------------------------------------------------------------------ #

    def save_config_snapshot(self, run_id: str, config: dict[str, Any]) -> None:
        """バックテスト実行時の設定スナップショットを保存する。

        Args:
          run_id: 実行 ID（UUID 推奨）
          config: 設定辞書

        Raises:
          ValueError: run_id が既に存在する場合
        """
        created_at = now_jst().isoformat()
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)

        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO config_snapshot (created_at, run_id, config_json) VALUES (?, ?, ?)",
                    (created_at, run_id, config_json),
                )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"run_id='{run_id}' は既に存在します。") from e


def _empty_ohlcv_df() -> pd.DataFrame:
    """空の OHLCV DataFrame を返す。"""
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
