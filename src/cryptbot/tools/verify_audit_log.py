"""audit_log ハッシュチェーン検証ツール。

使用例:
  python -m cryptbot.tools.verify_audit_log --db path/to/cryptbot.db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cryptbot.tools.verify_audit_log",
        description="audit_log のハッシュチェーンを検証し、改ざんの有無を報告する。",
    )
    parser.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="SQLite データベースファイルのパス",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """audit_log 検証エントリーポイント。

    Returns:
        終了コード（0: 正常, 1: 改ざん検知またはエラー）
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] DB ファイルが存在しません: {db_path}", file=sys.stderr)
        return 1

    # Storage を使って verify
    from cryptbot.data.storage import Storage

    # data_dir は検証のみなので任意のパスで OK
    storage = Storage(db_path=db_path, data_dir=db_path.parent / "data")

    try:
        result = storage.verify_audit_chain()
    except Exception as e:
        print(f"[ERROR] 検証中にエラーが発生しました: {e}", file=sys.stderr)
        return 1

    if result:
        print(f"[OK] audit_log のハッシュチェーンは正常です。DB: {db_path}")
        return 0
    else:
        print(
            f"[FAIL] audit_log に改ざんが検出されました。DB: {db_path}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
