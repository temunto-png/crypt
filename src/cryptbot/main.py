"""cryptbot エントリーポイント。

paper モードは 1 バー処理して終了する（cron 呼び出し前提）。
live モードは未実装のため fail-closed で終了する。

使用例:
  python -m cryptbot.main --help
  python -m cryptbot.main             # paper モードで 1 バー処理
  python -m cryptbot.main --mode live # LiveTradingNotImplementedError で終了
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class LiveTradingNotImplementedError(RuntimeError):
    """live トレードが未実装であることを示すエラー。

    このエラーが発生した場合、実資金は一切使用されない。
    Phase 2（paper engine）完了後に live 移行条件を満たしてから実装する。
    """


# 戦略名 → クラスのマッピング（eval / 動的 import を使わない）
def _resolve_strategy(name: str):
    """戦略名から BaseStrategy インスタンスを返す。"""
    from cryptbot.strategies.ma_cross import MACrossStrategy
    from cryptbot.strategies.momentum import MomentumStrategy
    from cryptbot.strategies.mean_reversion import MeanReversionStrategy
    from cryptbot.strategies.volatility_filter import VolatilityFilterStrategy

    _STRATEGIES = {
        "ma_cross": MACrossStrategy,
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "volatility_filter": VolatilityFilterStrategy,
    }
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(
            f"未知の戦略名: {name!r}。指定可能な値: {list(_STRATEGIES.keys())}"
        )
    return cls()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cryptbot",
        description=(
            "cryptbot - BTC/JPY 自動売買システム\n\n"
            "【警告】live モードは未実装です。実資金による取引は行えません。"
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
    parser.add_argument(
        "--db",
        default="data/paper.db",
        help="SQLite DB パス（デフォルト: data/paper.db）",
    )
    parser.add_argument(
        "--data-dir",
        default="data/",
        help="OHLCV Parquet ディレクトリ（デフォルト: data/）",
    )
    return parser


def _run_paper(args: argparse.Namespace) -> int:
    """paper モードで 1 バー処理する。"""
    from cryptbot.config.settings import load_settings
    from cryptbot.data.storage import Storage
    from cryptbot.paper.engine import PaperEngine
    from cryptbot.paper.state import PaperStateStore
    from cryptbot.risk.kill_switch import KillSwitch
    from cryptbot.risk.manager import RiskManager

    settings = load_settings()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir)

    storage = Storage(db_path=db_path, data_dir=data_dir)
    storage.initialize()

    kill_switch = KillSwitch(storage)
    risk_manager = RiskManager(
        settings=settings.circuit_breaker,
        cvar_settings=settings.cvar,
        kill_switch=kill_switch,
    )
    strategy = _resolve_strategy(settings.paper.strategy_name)

    state_store = PaperStateStore(storage)
    state_store.initialize()

    engine = PaperEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        settings=settings.paper,
    )

    # OHLCV データを Storage から読み込む（warmup + 1 本）
    needed = settings.paper.warmup_bars + 1
    try:
        data = storage.load_ohlcv(
            pair=settings.paper.pair,
            timeframe=settings.paper.timeframe,
        )
    except Exception as exc:
        print(f"[ERROR] OHLCV データの読み込みに失敗しました: {exc}", file=sys.stderr)
        return 1

    if len(data) < needed:
        print(
            f"[WARNING] データ不足: {len(data)} 本（必要: {needed} 本）。"
            f" 先に OHLCV データを投入してください。",
            file=sys.stderr,
        )
        return 1

    # 最新の needed 本に絞る
    data = data.tail(needed).reset_index(drop=True)

    result = engine.run_one_bar(data)

    if result.skipped:
        print(f"[INFO] スキップ: {result.skip_reason} (bar={result.bar_timestamp})")
    else:
        signal_str = result.signal.direction.value if result.signal else "None"
        trade_str = f"pnl={result.trade.pnl:.0f}JPY" if result.trade else "no trade"
        print(
            f"[INFO] bar={result.bar_timestamp}  "
            f"signal={signal_str}  {trade_str}  "
            f"balance={result.portfolio.balance:.0f}JPY  "
            f"cvar={result.cvar_action}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """cryptbot のエントリーポイント。

    Returns:
        終了コード（0: 正常, 1: エラー）
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # live モードは常に fail-closed
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

    return _run_paper(args)


if __name__ == "__main__":
    sys.exit(main())
