"""loguru ベースの構造化ロガー設定。

Usage:
    from crypt.utils.logger import setup_logger, sanitize_error
    setup_logger()
    from loguru import logger
    logger.info("ready")
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# API キーパターン: 英数字 20 文字以上の連続文字列を対象とする
# （bitbank のキーは英数字 64 文字程度）
# ---------------------------------------------------------------------------
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9]{20,}")


def sanitize_error(e: Exception) -> str:
    """例外メッセージから API キーパターンをマスクして返す。

    Args:
        e: マスク対象の例外

    Returns:
        API キーパターンを [REDACTED] に置換した文字列
    """
    return _SECRET_PATTERN.sub("[REDACTED]", str(e))


def setup_logger(
    log_dir: str | Path = "logs",
    level: str = "DEBUG",
    rotation: str = "1 day",
    retention: str = "30 days",
) -> None:
    """loguru を JSON Lines 形式で設定する。

    ハンドラ構成:
    - stderr: INFO 以上を人間可読フォーマットで出力
    - {log_dir}/crypt.jsonl: DEBUG 以上を JSON Lines で出力（日次ローテーション）

    Args:
        log_dir: ログ出力ディレクトリ（存在しない場合は作成する）
        level: ファイルへの最低ログレベル
        rotation: ログファイルのローテーション条件
        retention: ログファイルの保持期間
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger.remove()  # デフォルトハンドラを削除

    # コンソール出力（人間可読）
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        level="INFO",
        colorize=True,
    )

    # ファイル出力（JSON Lines）
    logger.add(
        log_path / "crypt.jsonl",
        serialize=True,
        rotation=rotation,
        retention=retention,
        level=level,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 監査ログハンドラ（Phase 1 で storage.py 実装後に接続する）
# ---------------------------------------------------------------------------


class _AuditLogSink:
    """SQLite audit_log テーブルへの書き込みシンク。

    setup_audit_logger() を呼び出すことで loguru ハンドラとして登録する。
    audit_log テーブルには INSERT のみ行い、UPDATE/DELETE は行わない。

    使用方法:
        1. setup_audit_logger() でハンドラ登録
        2. get_audit_sink().attach_storage(storage) で Storage を接続
        3. logger.bind(audit=True, audit_event_type="signal", ...).info("...") で記録

    推奨: バックテスト・paper engine などドメインコードでは
          storage.insert_audit_log() を直接呼ぶほうが明示的で安全。
          このシンクは既存の logger.bind(audit=True) 呼び出しを補助するものとして使う。

    エラーポリシー: audit write に失敗しても取引処理は停止しない。
                   失敗は stderr にのみ出力する。
    """

    # storage.insert_audit_log に渡せるフィールド名
    _ALLOWED_EXTRA = frozenset({
        "strategy", "direction", "signal_confidence", "signal_reason",
        "fill_price", "fill_size", "fee", "slippage_pct",
        "portfolio_value", "drawdown_pct", "raw_market_data",
    })

    def __init__(self) -> None:
        self._ready = False
        self._storage: object | None = None

    def attach_storage(self, storage: object) -> None:  # noqa: ANN001
        """storage.py の Storage インスタンスを接続する。"""
        self._storage = storage
        self._ready = True

    def __call__(self, message: object) -> None:
        """loguru ハンドラのエントリーポイント。

        record["extra"] から audit_log フィールドを抽出して Storage に書き込む。
        失敗時は stderr に出力するのみで例外を伝播させない。
        """
        if not self._ready or self._storage is None:
            return

        try:
            record = message.record  # type: ignore[union-attr]
            extra = record.get("extra", {})

            event_type: str = extra.get("audit_event_type", "log")

            # 許可フィールドのみを抽出
            kwargs = {k: v for k, v in extra.items() if k in self._ALLOWED_EXTRA}

            # signal_reason が未設定の場合はログメッセージを使用
            if "signal_reason" not in kwargs:
                kwargs["signal_reason"] = str(record.get("message", ""))

            self._storage.insert_audit_log(event_type, **kwargs)  # type: ignore[union-attr]

        except Exception as exc:  # noqa: BLE001
            # 監査ログ書き込み失敗は取引を止めない: stderr のみに記録
            sys.stderr.write(
                f"[AUDIT LOG ERROR] audit_log への書き込みに失敗しました: {exc}\n"
            )


_audit_sink = _AuditLogSink()


def setup_audit_logger() -> None:
    """監査ログ用の loguru ハンドラを登録する（Phase 1 で呼び出す）。"""
    logger.add(
        _audit_sink,
        level="INFO",
        filter=lambda record: record["extra"].get("audit") is True,
    )


def get_audit_sink() -> _AuditLogSink:
    """_AuditLogSink インスタンスを返す（Phase 1 で storage 接続に使用）。"""
    return _audit_sink
