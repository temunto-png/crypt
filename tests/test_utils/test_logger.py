"""logger ユーティリティのユニットテスト。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptbot.utils.logger import _AuditLogSink, get_audit_sink, sanitize_error


class TestSanitizeError:
    def test_masks_long_alphanumeric(self) -> None:
        e = ValueError("error: abcdefghijklmnopqrstuvwxyz1234567890ABCDEF")
        result = sanitize_error(e)
        assert "[REDACTED]" in result
        # 元のキー文字列が残っていないこと
        assert "abcdefghijklmnopqrstuvwxyz1234567890ABCDEF" not in result

    def test_preserves_short_strings(self) -> None:
        e = ValueError("timeout after 5s")
        result = sanitize_error(e)
        # 20 文字未満の英数字はそのまま残る
        assert "timeout" in result

    def test_handles_empty_exception(self) -> None:
        e = Exception("")
        assert sanitize_error(e) == ""

    def test_multiple_keys_in_message(self) -> None:
        key1 = "A" * 20
        key2 = "B" * 30
        e = ValueError(f"key1={key1} key2={key2}")
        result = sanitize_error(e)
        assert key1 not in result
        assert key2 not in result
        assert result.count("[REDACTED]") == 2


# --------------------------------------------------------------------------- #
# P2-04: _AuditLogSink
# --------------------------------------------------------------------------- #

class TestAuditLogSink:
    def _make_message(self, extra: dict) -> MagicMock:
        """loguru message オブジェクトのモックを作成する。"""
        msg = MagicMock()
        msg.record = {"extra": extra, "message": "test message"}
        return msg

    def test_not_ready_does_nothing(self) -> None:
        """attach_storage 未呼び出し時は insert_audit_log を呼ばない。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        msg = self._make_message({"audit_event_type": "signal"})
        sink(msg)
        storage.insert_audit_log.assert_not_called()

    def test_ready_calls_insert_audit_log(self) -> None:
        """attach_storage 後は insert_audit_log が呼ばれる。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        sink.attach_storage(storage)

        msg = self._make_message({
            "audit_event_type": "signal",
            "strategy": "ma_cross",
            "direction": "BUY",
            "signal_confidence": 0.8,
        })
        sink(msg)

        storage.insert_audit_log.assert_called_once()
        call_kwargs = storage.insert_audit_log.call_args
        assert call_kwargs.args[0] == "signal"
        assert call_kwargs.kwargs.get("strategy") == "ma_cross"
        assert call_kwargs.kwargs.get("direction") == "BUY"

    def test_unknown_extra_fields_filtered(self) -> None:
        """_ALLOWED_EXTRA 以外のフィールドは insert_audit_log に渡さない。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        sink.attach_storage(storage)

        msg = self._make_message({
            "audit_event_type": "fill",
            "fill_price": 5_000_000.0,
            "unknown_field": "should_be_filtered",
        })
        sink(msg)

        call_kwargs = storage.insert_audit_log.call_args.kwargs
        assert "unknown_field" not in call_kwargs
        assert "fill_price" in call_kwargs

    def test_insert_failure_does_not_raise(self) -> None:
        """insert_audit_log が例外を投げても sink は例外を伝播させない。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        storage.insert_audit_log.side_effect = RuntimeError("DB error")
        sink.attach_storage(storage)

        msg = self._make_message({"audit_event_type": "signal"})
        # 例外が伝播しないこと
        sink(msg)

    def test_signal_reason_fallback_to_message(self) -> None:
        """signal_reason が未設定の場合、record["message"] が signal_reason になる。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        sink.attach_storage(storage)

        msg = self._make_message({"audit_event_type": "log"})
        msg.record = {"extra": {"audit_event_type": "log"}, "message": "fallback_reason"}
        sink(msg)

        call_kwargs = storage.insert_audit_log.call_args.kwargs
        assert call_kwargs.get("signal_reason") == "fallback_reason"

    def test_default_event_type_is_log(self) -> None:
        """audit_event_type が extra にない場合、event_type='log' になる。"""
        sink = _AuditLogSink()
        storage = MagicMock()
        sink.attach_storage(storage)

        msg = self._make_message({})
        msg.record = {"extra": {}, "message": "no event type"}
        sink(msg)

        event_type = storage.insert_audit_log.call_args.args[0]
        assert event_type == "log"
