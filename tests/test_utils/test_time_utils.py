"""time_utils ユニットテスト。"""
from datetime import datetime, timezone

import pytest

from cryptbot.utils.time_utils import (
    JST,
    UTC,
    align_to_hour,
    datetime_to_unix_ms,
    now_jst,
    to_jst,
    unix_ms_to_datetime,
)


class TestNowJst:
    def test_returns_jst_aware(self) -> None:
        dt = now_jst()
        assert dt.tzinfo is not None
        assert dt.tzinfo == JST


class TestToJst:
    def test_converts_utc_to_jst(self) -> None:
        utc_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        jst_dt = to_jst(utc_dt)
        # JST は UTC+9 なので 9 時間ずれる
        assert jst_dt.hour == 9
        assert jst_dt.tzinfo == JST

    def test_raises_on_naive_datetime(self) -> None:
        naive = datetime(2024, 1, 1, 0, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            to_jst(naive)


class TestAlignToHour:
    def test_truncates_minutes_and_seconds(self) -> None:
        dt = datetime(2024, 6, 15, 14, 37, 59, 123456, tzinfo=UTC)
        aligned = align_to_hour(dt)
        assert aligned.minute == 0
        assert aligned.second == 0
        assert aligned.microsecond == 0
        assert aligned.hour == 14  # 時間は変わらない

    def test_preserves_tzinfo(self) -> None:
        dt = datetime(2024, 6, 15, 14, 37, 0, tzinfo=JST)
        aligned = align_to_hour(dt)
        assert aligned.tzinfo == JST

    def test_raises_on_naive_datetime(self) -> None:
        naive = datetime(2024, 6, 15, 14, 37, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            align_to_hour(naive)


class TestUnixMsConversion:
    def test_roundtrip(self) -> None:
        original_ms = 1_700_000_000_000  # 2023-11-15 あたり
        dt = unix_ms_to_datetime(original_ms)
        back = datetime_to_unix_ms(dt)
        assert back == original_ms

    def test_unix_ms_to_datetime_is_jst(self) -> None:
        ms = 0  # 1970-01-01 00:00:00 UTC = 1970-01-01 09:00:00 JST
        dt = unix_ms_to_datetime(ms)
        assert dt.hour == 9
        assert dt.tzinfo == JST

    def test_datetime_to_unix_ms_raises_on_naive(self) -> None:
        naive = datetime(2024, 1, 1, 0, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            datetime_to_unix_ms(naive)
