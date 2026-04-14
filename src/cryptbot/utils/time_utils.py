"""タイムゾーン変換・時刻アライン ユーティリティ。

bitbank の API は Unix ミリ秒タイムスタンプを返す。
システム内部では JST の timezone-aware datetime を使用する。

設計原則: timezone-naive な datetime は受け付けない。呼び出し元で tzinfo を
明示することを強制し、暗黙の UTC 仮定による誤変換を防ぐ。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
UTC = timezone.utc


def now_jst() -> datetime:
    """現在時刻を JST timezone-aware で返す。"""
    return datetime.now(JST)


def to_jst(dt: datetime) -> datetime:
    """datetime を JST に変換する。

    Args:
        dt: 変換元の timezone-aware datetime

    Returns:
        JST timezone-aware datetime

    Raises:
        ValueError: timezone-naive な datetime が渡された場合
    """
    if dt.tzinfo is None:
        raise ValueError(
            "to_jst には timezone-aware な datetime が必要です。"
            " tzinfo を設定してください（例: datetime(..., tzinfo=timezone.utc)）。"
        )
    return dt.astimezone(JST)


def align_to_hour(dt: datetime) -> datetime:
    """datetime を 1 時間境界に切り捨てアラインする。

    Args:
        dt: アライン対象の timezone-aware datetime

    Returns:
        minute=0, second=0, microsecond=0 にした datetime（tzinfo は保持）

    Raises:
        ValueError: timezone-naive な datetime が渡された場合
    """
    if dt.tzinfo is None:
        raise ValueError(
            "align_to_hour には timezone-aware な datetime が必要です。"
        )
    return dt.replace(minute=0, second=0, microsecond=0)


def unix_ms_to_datetime(ms: int) -> datetime:
    """Unix ミリ秒タイムスタンプを JST timezone-aware datetime に変換する。

    Args:
        ms: Unix ミリ秒タイムスタンプ（bitbank API が返す形式）

    Returns:
        JST timezone-aware datetime
    """
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(JST)


def datetime_to_unix_ms(dt: datetime) -> int:
    """datetime を Unix ミリ秒タイムスタンプに変換する。

    Args:
        dt: 変換元の timezone-aware datetime

    Returns:
        Unix ミリ秒タイムスタンプ

    Raises:
        ValueError: timezone-naive な datetime が渡された場合
    """
    if dt.tzinfo is None:
        raise ValueError(
            "datetime_to_unix_ms には timezone-aware な datetime が必要です。"
            " tzinfo を設定してください（例: datetime(..., tzinfo=timezone.utc)）。"
        )
    return int(dt.timestamp() * 1000)
