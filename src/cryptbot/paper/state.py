"""Paper trading エンジンの状態永続化レイヤー。

SQLite の paper_state テーブルに PortfolioState と実行状態をまとめて保存する。
1 セッション = 1 行 (id=1) の UPSERT で管理する。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from cryptbot.data.storage import Storage
from cryptbot.risk.manager import PortfolioState
from cryptbot.strategies.base import Direction, Signal
from cryptbot.utils.time_utils import JST, now_jst

_STATE_ID = 1


@dataclass
class PaperState:
    """SQLite から読み書きされる paper trading の完全な状態。"""

    # PortfolioState フィールド
    balance: float
    peak_balance: float
    position_size: float
    entry_price: float
    daily_loss: float
    weekly_loss: float
    monthly_loss: float
    consecutive_losses: int
    last_reset_date: str        # ISO date "YYYY-MM-DD"
    last_weekly_reset: str
    last_monthly_reset: str

    # PaperEngine 固有フィールド
    pending_signal_direction: str | None    # "BUY" / "SELL" / "HOLD" / None
    pending_signal_confidence: float | None
    pending_signal_reason: str | None
    pending_signal_ts: str | None           # ISO8601
    pending_cvar_action: str                # "normal" / "warning" / "half" / "stop"
    recent_trade_returns: str               # JSON array, max 50 要素
    position_entry_time: str | None         # ISO8601, None = ノーポジション
    position_entry_price: float
    last_processed_bar_ts: str | None       # ISO8601, None = 初回実行

    updated_at: str                         # ISO8601


class PaperStateStore:
    """paper_state テーブルへの CRUD を担う I/O レイヤー。

    ビジネスロジックは持たない。Storage と同じ SQLite ファイルを使う。
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS paper_state (
            id                          INTEGER PRIMARY KEY,
            balance                     REAL    NOT NULL,
            peak_balance                REAL    NOT NULL,
            position_size               REAL    NOT NULL DEFAULT 0.0,
            entry_price                 REAL    NOT NULL DEFAULT 0.0,
            daily_loss                  REAL    NOT NULL DEFAULT 0.0,
            weekly_loss                 REAL    NOT NULL DEFAULT 0.0,
            monthly_loss                REAL    NOT NULL DEFAULT 0.0,
            consecutive_losses          INTEGER NOT NULL DEFAULT 0,
            last_reset_date             TEXT    NOT NULL,
            last_weekly_reset           TEXT    NOT NULL,
            last_monthly_reset          TEXT    NOT NULL,
            pending_signal_direction    TEXT,
            pending_signal_confidence   REAL,
            pending_signal_reason       TEXT,
            pending_signal_ts           TEXT,
            pending_cvar_action         TEXT    NOT NULL DEFAULT 'normal',
            recent_trade_returns        TEXT    NOT NULL DEFAULT '[]',
            position_entry_time         TEXT,
            position_entry_price        REAL    NOT NULL DEFAULT 0.0,
            last_processed_bar_ts       TEXT,
            updated_at                  TEXT    NOT NULL
        )
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def initialize(self) -> None:
        """paper_state テーブルを作成する。Storage.initialize() 後に呼ぶ。"""
        with self._storage._connect() as conn:
            conn.execute(self._CREATE_TABLE)

    def load(self) -> PaperState | None:
        """保存済み状態を返す。未保存の場合は None。"""
        with self._storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_state WHERE id = ?", (_STATE_ID,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d.pop("id", None)  # PaperState に id フィールドはない
        return PaperState(**d)

    def save(self, state: PaperState) -> None:
        """状態を UPSERT で保存する（id=1 の単一行を常に上書き）。"""
        with self._storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_state (
                    id, balance, peak_balance, position_size, entry_price,
                    daily_loss, weekly_loss, monthly_loss, consecutive_losses,
                    last_reset_date, last_weekly_reset, last_monthly_reset,
                    pending_signal_direction, pending_signal_confidence,
                    pending_signal_reason, pending_signal_ts,
                    pending_cvar_action, recent_trade_returns,
                    position_entry_time, position_entry_price,
                    last_processed_bar_ts, updated_at
                ) VALUES (
                    :id, :balance, :peak_balance, :position_size, :entry_price,
                    :daily_loss, :weekly_loss, :monthly_loss, :consecutive_losses,
                    :last_reset_date, :last_weekly_reset, :last_monthly_reset,
                    :pending_signal_direction, :pending_signal_confidence,
                    :pending_signal_reason, :pending_signal_ts,
                    :pending_cvar_action, :recent_trade_returns,
                    :position_entry_time, :position_entry_price,
                    :last_processed_bar_ts, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    balance                   = excluded.balance,
                    peak_balance              = excluded.peak_balance,
                    position_size             = excluded.position_size,
                    entry_price               = excluded.entry_price,
                    daily_loss                = excluded.daily_loss,
                    weekly_loss               = excluded.weekly_loss,
                    monthly_loss              = excluded.monthly_loss,
                    consecutive_losses        = excluded.consecutive_losses,
                    last_reset_date           = excluded.last_reset_date,
                    last_weekly_reset         = excluded.last_weekly_reset,
                    last_monthly_reset        = excluded.last_monthly_reset,
                    pending_signal_direction  = excluded.pending_signal_direction,
                    pending_signal_confidence = excluded.pending_signal_confidence,
                    pending_signal_reason     = excluded.pending_signal_reason,
                    pending_signal_ts         = excluded.pending_signal_ts,
                    pending_cvar_action       = excluded.pending_cvar_action,
                    recent_trade_returns      = excluded.recent_trade_returns,
                    position_entry_time       = excluded.position_entry_time,
                    position_entry_price      = excluded.position_entry_price,
                    last_processed_bar_ts     = excluded.last_processed_bar_ts,
                    updated_at                = excluded.updated_at
                """,
                {
                    "id": _STATE_ID,
                    "balance": state.balance,
                    "peak_balance": state.peak_balance,
                    "position_size": state.position_size,
                    "entry_price": state.entry_price,
                    "daily_loss": state.daily_loss,
                    "weekly_loss": state.weekly_loss,
                    "monthly_loss": state.monthly_loss,
                    "consecutive_losses": state.consecutive_losses,
                    "last_reset_date": state.last_reset_date,
                    "last_weekly_reset": state.last_weekly_reset,
                    "last_monthly_reset": state.last_monthly_reset,
                    "pending_signal_direction": state.pending_signal_direction,
                    "pending_signal_confidence": state.pending_signal_confidence,
                    "pending_signal_reason": state.pending_signal_reason,
                    "pending_signal_ts": state.pending_signal_ts,
                    "pending_cvar_action": state.pending_cvar_action,
                    "recent_trade_returns": state.recent_trade_returns,
                    "position_entry_time": state.position_entry_time,
                    "position_entry_price": state.position_entry_price,
                    "last_processed_bar_ts": state.last_processed_bar_ts,
                    "updated_at": state.updated_at,
                },
            )

    # ------------------------------------------------------------------
    # 変換ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def to_portfolio_state(state: PaperState) -> PortfolioState:
        """PaperState → PortfolioState に変換する。"""
        return PortfolioState(
            balance=state.balance,
            peak_balance=state.peak_balance,
            position_size=state.position_size,
            entry_price=state.entry_price,
            daily_loss=state.daily_loss,
            weekly_loss=state.weekly_loss,
            monthly_loss=state.monthly_loss,
            consecutive_losses=state.consecutive_losses,
            last_reset_date=date.fromisoformat(state.last_reset_date),
            last_weekly_reset=date.fromisoformat(state.last_weekly_reset),
            last_monthly_reset=date.fromisoformat(state.last_monthly_reset),
        )

    @staticmethod
    def from_portfolio_state(
        portfolio: PortfolioState,
        pending_signal: Signal | None,
        pending_cvar_action: str,
        recent_trade_returns: list[float],
        position_entry_time: datetime | None,
        position_entry_price: float,
        last_processed_bar_ts: datetime | None,
    ) -> PaperState:
        """PortfolioState + paper 固有フィールドから PaperState を組み立てる。"""
        if pending_signal is not None:
            sig_dir = pending_signal.direction.value
            sig_conf = pending_signal.confidence
            sig_reason = pending_signal.reason
            sig_ts = pending_signal.timestamp.isoformat()
        else:
            sig_dir = None
            sig_conf = None
            sig_reason = None
            sig_ts = None

        entry_ts_str = position_entry_time.isoformat() if position_entry_time else None
        bar_ts_str = last_processed_bar_ts.isoformat() if last_processed_bar_ts else None

        return PaperState(
            balance=portfolio.balance,
            peak_balance=portfolio.peak_balance,
            position_size=portfolio.position_size,
            entry_price=portfolio.entry_price,
            daily_loss=portfolio.daily_loss,
            weekly_loss=portfolio.weekly_loss,
            monthly_loss=portfolio.monthly_loss,
            consecutive_losses=portfolio.consecutive_losses,
            last_reset_date=portfolio.last_reset_date.isoformat(),
            last_weekly_reset=portfolio.last_weekly_reset.isoformat(),
            last_monthly_reset=portfolio.last_monthly_reset.isoformat(),
            pending_signal_direction=sig_dir,
            pending_signal_confidence=sig_conf,
            pending_signal_reason=sig_reason,
            pending_signal_ts=sig_ts,
            pending_cvar_action=pending_cvar_action,
            recent_trade_returns=json.dumps(recent_trade_returns),
            position_entry_time=entry_ts_str,
            position_entry_price=position_entry_price,
            last_processed_bar_ts=bar_ts_str,
            updated_at=now_jst().isoformat(),
        )

    @staticmethod
    def extract_pending_signal(state: PaperState) -> Signal | None:
        """PaperState から pending Signal を復元する。"""
        if state.pending_signal_direction is None:
            return None
        try:
            direction = Direction(state.pending_signal_direction)
        except ValueError:
            return None

        ts_raw = state.pending_signal_ts
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=JST)
            except ValueError:
                ts = now_jst()
        else:
            ts = now_jst()

        return Signal(
            direction=direction,
            confidence=state.pending_signal_confidence or 0.0,
            reason=state.pending_signal_reason or "",
            timestamp=ts,
        )

    @staticmethod
    def extract_recent_returns(state: PaperState) -> list[float]:
        """PaperState から recent_trade_returns を復元する。"""
        try:
            result = json.loads(state.recent_trade_returns)
            if isinstance(result, list):
                return [float(x) for x in result]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return []

    @staticmethod
    def extract_position_entry_time(state: PaperState) -> datetime | None:
        """PaperState から position_entry_time を復元する。"""
        if not state.position_entry_time:
            return None
        try:
            dt = datetime.fromisoformat(state.position_entry_time)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=JST)
            return dt
        except ValueError:
            return None
