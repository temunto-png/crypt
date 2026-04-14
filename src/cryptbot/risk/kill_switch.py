from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import Enum

from cryptbot.data.storage import Storage
from cryptbot.utils.time_utils import now_jst


class KillSwitchReason(str, Enum):
    MAX_DRAWDOWN = "max_drawdown"
    MONTHLY_LOSS = "monthly_loss"
    MANUAL = "manual"
    API_OUTAGE = "api_outage"


class KillSwitch:
    """Kill switch の状態管理。

    一度 activate() されたら deactivate() を明示的に呼ぶまで active のまま。
    自動解除しない。
    """

    def __init__(self, storage: Storage) -> None:
        self._active: bool = False
        self._reason: KillSwitchReason | None = None
        self._activated_at: datetime | None = None
        self._storage = storage
        # 起動時に永続化状態を復元する。DB 未初期化時は inactive のまま継続する。
        self.load_state()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def reason(self) -> KillSwitchReason | None:
        return self._reason

    def activate(
        self,
        reason: KillSwitchReason,
        portfolio_value: float,
        drawdown_pct: float,
    ) -> None:
        """Kill switch を発動する。audit_log に KILL_SWITCH_ACTIVATED を記録する。"""
        # 既に active の場合は何もしない（二重発動防止）
        if self._active:
            return

        self._active = True
        self._reason = reason
        self._activated_at = now_jst()

        self._storage.insert_audit_log(
            "KILL_SWITCH_ACTIVATED",
            signal_reason=reason.value,
            portfolio_value=portfolio_value,
            drawdown_pct=drawdown_pct,
        )

    def deactivate(self) -> None:
        """Kill switch を解除する。audit_log に KILL_SWITCH_DEACTIVATED を記録する。"""
        if not self._active:
            return

        self._active = False
        self._reason = None
        self._activated_at = None

        self._storage.insert_audit_log("KILL_SWITCH_DEACTIVATED")

    def load_state(self) -> None:
        """起動時に Storage から Kill switch の状態を復元する。

        audit_log の最新 KILL_SWITCH_ACTIVATED / KILL_SWITCH_DEACTIVATED イベントを確認し、
        プロセス再起動後も kill switch の active 状態を維持する。
        最新イベントが KILL_SWITCH_DEACTIVATED、またはイベントが存在しない場合は inactive。
        DB 未初期化（audit_log テーブルが存在しない）場合は inactive のまま継続する。
        """
        try:
            with self._storage._connect() as conn:
                row = conn.execute(
                    """
                    SELECT event_type, signal_reason, timestamp
                    FROM audit_log
                    WHERE event_type IN ('KILL_SWITCH_ACTIVATED', 'KILL_SWITCH_DEACTIVATED')
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.OperationalError:
            # audit_log テーブルが未作成（initialize() 前）の場合は inactive のまま
            self._active = False
            self._reason = None
            self._activated_at = None
            return

        if row is None or row["event_type"] == "KILL_SWITCH_DEACTIVATED":
            self._active = False
            self._reason = None
            self._activated_at = None
            return

        # 最新イベントが KILL_SWITCH_ACTIVATED → active を復元
        self._active = True
        try:
            self._reason = KillSwitchReason(row["signal_reason"])
        except (ValueError, KeyError, TypeError):
            self._reason = None
        try:
            self._activated_at = datetime.fromisoformat(row["timestamp"])
        except (TypeError, ValueError):
            self._activated_at = None
