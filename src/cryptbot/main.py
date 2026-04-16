"""cryptbot エントリーポイント。

paper モードは 1 バー処理して終了する（cron 呼び出し前提）。
live モードは asyncio 永続プロセスとして動作する。

使用例:
  python -m cryptbot.main --help
  python -m cryptbot.main                                   # paper モードで 1 バー処理
  python -m cryptbot.main --mode live --confirm-live        # live モードで起動
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


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
        description="cryptbot - BTC/JPY 自動売買システム",
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
        help="live モードの実行を明示的に確認するフラグ（--mode live と同時に必要）",
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


def _build_ml_components(settings):
    """ML モデルと劣化検知器を組み立てる。

    Args:
        settings: Settings インスタンス

    Returns:
        (ml_model, degradation_detector) タプル。
        enabled=False の場合は (None, None)。
        enabled=True でモデル未検出の場合は None（fail-closed シグナル）。
    """
    from cryptbot.models.degradation_detector import DegradationDetector
    from cryptbot.models.experiment_manager import ExperimentManager

    model_settings = settings.model
    if not model_settings.enabled:
        return (None, None)

    if model_settings.model_type == "lgbm":
        from cryptbot.models.lgbm_model import LightGBMModel
        model_cls = LightGBMModel
    else:
        from cryptbot.models.xgb_model import XGBoostModel
        model_cls = XGBoostModel

    manager = ExperimentManager(base_dir=model_settings.experiments_dir)

    if model_settings.active_experiment_id is not None:
        try:
            ml_model = manager.load_model(model_settings.active_experiment_id, model_cls)
            records = [r for r in manager.list_experiments()
                       if r.experiment_id == model_settings.active_experiment_id]
            baseline = records[0].metrics.get("accuracy", 0.6) if records else 0.6
        except FileNotFoundError:
            logger.error("ML モデルが見つかりません: %s", model_settings.active_experiment_id)
            return None
    else:
        records = manager.list_experiments()
        if not records:
            logger.error("ML が enabled ですが実験記録が見つかりません（experiments_dir=%s）",
                         model_settings.experiments_dir)
            return None
        ml_model = manager.load_latest_model(model_cls)
        if ml_model is None:
            logger.error("ML が enabled ですがモデルファイルのロードに失敗しました")
            return None
        baseline = records[0].metrics.get("accuracy", 0.6)

    degradation_detector = DegradationDetector(
        baseline_confidence=baseline,
        window=model_settings.degradation_window,
        drop_threshold_pct=model_settings.degradation_drop_pct,
    )
    return (ml_model, degradation_detector)


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

    ml_result = _build_ml_components(settings)
    if ml_result is None:
        print("[ERROR] ML が enabled ですがモデルが見つかりません。", file=sys.stderr)
        return 1
    ml_model, degradation_detector = ml_result

    state_store = PaperStateStore(storage)
    state_store.initialize()

    engine = PaperEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        settings=settings.paper,
        ml_model=ml_model,
        degradation_detector=degradation_detector,
        ml_confidence_threshold=settings.model.confidence_threshold,
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


def _run_live(args: argparse.Namespace) -> int:
    """live モードで asyncio 永続プロセスとして動作する。"""
    from cryptbot.config.settings import load_settings
    from cryptbot.data.storage import Storage
    from cryptbot.exchanges.bitbank_private import BitbankPrivateExchange
    from cryptbot.execution.live_executor import LiveExecutor
    from cryptbot.live.engine import LiveEngine
    from cryptbot.live.gate import LiveGateError, phase_4_gate, verify_api_permissions
    from cryptbot.live.state import LiveStateStore
    from cryptbot.risk.kill_switch import KillSwitch
    from cryptbot.risk.manager import RiskManager
    from cryptbot.exchanges.bitbank import BitbankExchange
    from cryptbot.data.fetcher import DataFetcher
    from cryptbot.data.ohlcv_updater import OhlcvUpdater

    settings = load_settings()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir)

    storage = Storage(db_path=db_path, data_dir=data_dir)
    storage.initialize()

    # Public API 用 Exchange（OHLCV バックフィル + 定期更新）
    pub_exchange = BitbankExchange()
    fetcher = DataFetcher(exchange=pub_exchange, storage=storage)
    ohlcv_updater = OhlcvUpdater(
        fetcher=fetcher,
        pair=settings.live.pair,
        timeframe=settings.live.timeframe,
        backfill_years=settings.live.ohlcv_backfill_years,
    )

    kill_switch = KillSwitch(storage)

    # fail-closed ゲートチェック（4 条件を全て検証）
    try:
        phase_4_gate(settings, args.confirm_live, kill_switch)
    except LiveGateError as exc:
        print(f"[ERROR] live ゲートチェック失敗: {exc}", file=sys.stderr)
        return 1

    # 取引所インスタンス生成
    api_key = settings.bitbank_api_key.get_secret_value()
    api_secret = settings.bitbank_api_secret.get_secret_value()
    exchange = BitbankPrivateExchange(api_key=api_key, api_secret=api_secret)

    # コンポーネント組み立て
    risk_manager = RiskManager(
        settings=settings.circuit_breaker,
        cvar_settings=settings.cvar,
        kill_switch=kill_switch,
    )
    strategy = _resolve_strategy(settings.live.strategy_name)

    ml_result = _build_ml_components(settings)
    if ml_result is None:
        print("[ERROR] ML が enabled ですがモデルが見つかりません。", file=sys.stderr)
        return 1
    ml_model, degradation_detector = ml_result

    state_store = LiveStateStore(storage)
    state_store.initialize()

    executor = LiveExecutor(
        exchange=exchange,
        storage=storage,
        strategy_name=settings.live.strategy_name,
    )

    engine = LiveEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        executor=executor,
        settings=settings.live,
        ml_model=ml_model,
        degradation_detector=degradation_detector,
        ml_confidence_threshold=settings.model.confidence_threshold,
        ohlcv_updater=ohlcv_updater,
    )

    async def _live_async() -> None:
        """API 疎通確認後にエンジンを起動する（単一イベントループ）。"""
        # OHLCV バックフィル（失敗時は LiveGateError として再 raise → return 1）
        try:
            n = await ohlcv_updater.backfill()
            print(
                f"[INFO] OHLCV バックフィル完了: {n} 本 "
                f"pair={settings.live.pair} tf={settings.live.timeframe}"
            )
        except Exception as exc:
            print(f"[ERROR] OHLCV バックフィル失敗: {exc}", file=sys.stderr)
            raise LiveGateError(str(exc)) from exc

        try:
            assets = await verify_api_permissions(exchange)
        except LiveGateError as exc:
            print(f"[ERROR] API 疎通確認失敗: {exc}", file=sys.stderr)
            raise

        jpy = assets.get("jpy", 0.0)
        btc = assets.get("btc", 0.0)
        print(
            f"[INFO] API 疎通確認完了: JPY={jpy:.0f}  BTC={btc:.6f}  "
            f"pair={settings.live.pair}  tf={settings.live.timeframe}"
        )

        await engine.run(assets=assets)

    try:
        asyncio.run(_live_async())
    except LiveGateError:
        return 1
    except KeyboardInterrupt:
        print("[INFO] シャットダウン完了")
    return 0


def main(argv: list[str] | None = None) -> int:
    """cryptbot のエントリーポイント。

    Returns:
        終了コード（0: 正常, 1: エラー）
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.mode == "live":
        return _run_live(args)

    return _run_paper(args)


if __name__ == "__main__":
    sys.exit(main())
