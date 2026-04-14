"""cryptbot エントリーポイント。

現状は paper モードのみ対応。live モードは未実装のため fail-closed で終了する。

使用例:
  python -m cryptbot.main --help
  python -m cryptbot.main             # paper モードで起動（Phase 2 実装待ち）
  python -m cryptbot.main --mode live # LiveTradingNotImplementedError で終了
"""
from __future__ import annotations

import argparse
import sys


class LiveTradingNotImplementedError(RuntimeError):
    """live トレードが未実装であることを示すエラー。

    このエラーが発生した場合、実資金は一切使用されない。
    Phase 2（paper engine）完了後に live 移行条件を満たしてから実装する。
    """


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cryptbot",
        description=(
            "cryptbot - BTC/JPY 自動売買システム\n\n"
            "【警告】現バージョンは paper モードのみ対応です。\n"
            "live モードは未実装です。実資金による取引は行えません。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="実行モード（デフォルト: paper）",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        dest="confirm_live",
        help="live モードの実行を確認するフラグ（現在未有効）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """cryptbot のエントリーポイント。

    Returns:
        終了コード（0: 正常, 1: エラー）
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # live モードは常に fail-closed
    # --confirm-live だけでは live 実行されない（実装後も複数条件が必要）
    if args.mode == "live" or args.confirm_live:
        print(
            "[ERROR] live トレードは未実装です。\n"
            "  Phase 2（paper engine）の完了と live 移行条件チェックが必要です。\n"
            "  README.md の「Live 移行条件チェックリスト」を参照してください。",
            file=sys.stderr,
        )
        raise LiveTradingNotImplementedError(
            "Live trading is not implemented. "
            "Complete Phase 2 (paper engine) and satisfy all live gate conditions first."
        )

    print(
        "[INFO] cryptbot paper モード\n"
        "  Phase 2 (paper engine) は未実装です。\n"
        "  バックテストを実行するには src/cryptbot/backtest/engine.py を参照してください。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
