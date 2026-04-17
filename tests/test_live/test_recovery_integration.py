"""起動リカバリーの実 Storage 統合テスト。

モックを使わず、実際の SQLite Storage に対して状態遷移をテストする。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cryptbot.data.storage import Storage
from cryptbot.live.engine import LiveEngine


def _make_storage(tmp_path) -> Storage:
    storage = Storage(
        str(tmp_path / "test.db"),
        str(tmp_path / "data"),
    )
    storage.initialize()
    return storage


class TestRecoveryIntegration:
    """実 Storage を使った起動リカバリー統合テスト。"""

    def test_created_orphan_cancelled_and_event_recorded(self, tmp_path):
        """実DB: CREATED orphan をキャンセルし order_events に記録する。"""
        storage = _make_storage(tmp_path)
        oid = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "limit",
            "size": 0.001,
            "price": 5_000_000.0,
            "strategy": "test",
            "status": "CREATED",
        })
        # exchange_order_id なし → orphan

        settings = MagicMock()
        settings.pair = "btc_jpy"
        engine = MagicMock()
        engine._storage = storage
        engine._settings = settings

        LiveEngine._cancel_orphan_orders(engine)

        conn = storage._connect()
        order = conn.execute(
            "SELECT status FROM orders WHERE id=?", (oid,)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type, note FROM order_events WHERE order_id=?", (oid,)
        ).fetchall()
        conn.close()

        assert order["status"] == "CANCELLED"
        event_types = [e["event_type"] for e in events]
        assert "CANCELLED" in event_types
        notes = [e["note"] for e in events]
        assert any("startup_orphan_recovery" in (n or "") for n in notes)

    def test_created_and_submitted_mixed_recovery(self, tmp_path):
        """実DB: CREATED orphan と SUBMITTED が混在する場合、orphan のみキャンセルされる。"""
        storage2 = _make_storage(tmp_path / "db2")
        orphan_id2 = storage2.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "limit",
            "size": 0.001,
            "price": 5_000_000.0,
            "strategy": "test",
            "status": "CREATED",
        })
        submitted_id2 = storage2.insert_order({
            "pair": "eth_jpy",
            "side": "BUY",
            "order_type": "limit",
            "size": 0.01,
            "price": 400_000.0,
            "strategy": "test",
            "status": "CREATED",
        })
        storage2.update_order_status(submitted_id2, "SUBMITTED", exchange_order_id="EX-99")

        settings = MagicMock()
        settings.pair = "btc_jpy"
        engine = MagicMock()
        engine._storage = storage2
        engine._settings = settings

        LiveEngine._cancel_orphan_orders(engine)

        conn = storage2._connect()
        orphan_row = conn.execute(
            "SELECT status FROM orders WHERE id=?", (orphan_id2,)
        ).fetchone()
        submitted_row = conn.execute(
            "SELECT status FROM orders WHERE id=?", (submitted_id2,)
        ).fetchone()
        orphan_events = conn.execute(
            "SELECT event_type, note FROM order_events WHERE order_id=?", (orphan_id2,)
        ).fetchall()
        conn.close()

        assert orphan_row["status"] == "CANCELLED"
        assert submitted_row["status"] == "SUBMITTED"
        # orphan のイベント記録も確認
        event_types = [e["event_type"] for e in orphan_events]
        assert "CANCELLED" in event_types
        notes = [e["note"] for e in orphan_events]
        assert any("startup_orphan_recovery" in (n or "") for n in notes)

    def test_partial_fill_snapshot_updated_without_status_change(self, tmp_path):
        """実DB: PARTIAL 注文に update_order_fill_snapshot → fill 更新、status 変化なし。"""
        storage = _make_storage(tmp_path)
        oid = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "limit",
            "size": 0.001,
            "price": 5_000_000.0,
            "strategy": "test",
            "status": "CREATED",
        })
        storage.update_order_status(oid, "SUBMITTED", exchange_order_id="EX-42")
        storage.update_order_status(oid, "PARTIAL", fill_price=5_000_000.0, fill_size=0.0005)

        storage.update_order_fill_snapshot(oid, fill_price=5_100_000.0, fill_size=0.0008)

        conn = storage._connect()
        row = conn.execute(
            "SELECT status, fill_price, fill_size FROM orders WHERE id=?", (oid,)
        ).fetchone()
        conn.close()

        assert row["status"] == "PARTIAL"
        assert row["fill_price"] == pytest.approx(5_100_000.0)
        assert row["fill_size"] == pytest.approx(0.0008)

    def test_partial_to_cancelled_transition_allowed(self, tmp_path):
        """実DB: PARTIAL → CANCELLED 遷移は valid。"""
        storage = _make_storage(tmp_path)
        oid = storage.insert_order({
            "pair": "btc_jpy",
            "side": "BUY",
            "order_type": "limit",
            "size": 0.001,
            "price": 5_000_000.0,
            "strategy": "test",
            "status": "CREATED",
        })
        storage.update_order_status(oid, "SUBMITTED", exchange_order_id="EX-43")
        storage.update_order_status(oid, "PARTIAL", fill_price=5_000_000.0, fill_size=0.0005)

        storage.update_order_status(oid, "CANCELLED")

        conn = storage._connect()
        row = conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()
        conn.close()

        assert row["status"] == "CANCELLED"
