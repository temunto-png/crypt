"""15min 足 MomentumStrategy グリッドサーチ + Walk-Forward 検証スクリプト。

Usage:
    python scripts/grid_search_wf.py [--fetch] [--backfill-years N]

    --fetch          15min OHLCV データをバックフィルしてから実行
    --backfill-years 取得年数（デフォルト: 5）
    --threshold      WF のみ実行する threshold を指定（グリッドサーチをスキップ）
    --window         WF のみ実行する momentum_window を指定（グリッドサーチをスキップ）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from cryptbot.backtest.engine import BacktestEngine
from cryptbot.backtest.metrics import calculate_metrics
from cryptbot.config.settings import load_settings
from cryptbot.data.normalizer import normalize
from cryptbot.data.storage import Storage
from cryptbot.risk.kill_switch import KillSwitch
from cryptbot.risk.manager import RiskManager
from cryptbot.strategies.momentum import MomentumStrategy


TIMEFRAME = "15min"
PAIR = "btc_jpy"
DB_PATH = ROOT / "data" / "cryptbot.db"
DATA_DIR = ROOT / "data"

# グリッドサーチ パラメータ候補
THRESHOLD_CANDIDATES = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
MOMENTUM_WINDOW_CANDIDATES = [3, 5, 10, 20]


async def fetch_15min(backfill_years: int) -> None:
    from cryptbot.data.fetcher import DataFetcher
    from cryptbot.data.ohlcv_updater import OhlcvUpdater
    from cryptbot.exchanges.bitbank import BitbankExchange

    storage = Storage(db_path=DB_PATH, data_dir=DATA_DIR)
    storage.initialize()
    exchange = BitbankExchange()  # public API のみ使用（認証不要）
    fetcher = DataFetcher(exchange=exchange, storage=storage)
    updater = OhlcvUpdater(
        fetcher=fetcher,
        pair=PAIR,
        timeframe=TIMEFRAME,
        backfill_years=backfill_years,
    )
    print(f"[FETCH] {PAIR} {TIMEFRAME} {backfill_years}年分をバックフィル中...")
    n = await updater.backfill()
    print(f"[FETCH] 完了: {n} 本保存")


def load_raw_data() -> pd.DataFrame:
    """正規化前の生 OHLCV を返す。momentum_window ごとに再正規化するために分離。"""
    storage = Storage(db_path=DB_PATH, data_dir=DATA_DIR)
    return storage.load_ohlcv(pair=PAIR, timeframe=TIMEFRAME)


def make_risk_manager() -> RiskManager:
    settings = load_settings()
    storage = Storage(db_path=DB_PATH, data_dir=DATA_DIR)
    ks = KillSwitch(storage)
    # バックテスト用: 本番 DB の kill switch 状態を引き継がないようリセット
    ks.reset()
    return RiskManager(
        settings=settings.circuit_breaker,
        cvar_settings=settings.cvar,
        kill_switch=ks,
    )


def run_grid_search(raw_data: pd.DataFrame) -> list[dict]:
    """threshold × momentum_window の全組み合わせでフル期間バックテストを実行。"""
    first_ts = raw_data["timestamp"].iloc[0]
    last_ts = raw_data["timestamp"].iloc[-1]
    print(f"\n{'='*72}")
    print(f"グリッドサーチ (フル期間: {first_ts:%Y-%m-%d} 〜 {last_ts:%Y-%m-%d})")
    print(f"threshold: {THRESHOLD_CANDIDATES}")
    print(f"momentum_window: {MOMENTUM_WINDOW_CANDIDATES}")
    print(f"{'='*72}")
    print(f"{'window':>7} {'threshold':>10} {'trades':>7} {'pnl%':>8} {'win%':>7} {'sharpe':>8} {'score':>8}")
    print("-" * 72)

    results = []
    for window in MOMENTUM_WINDOW_CANDIDATES:
        data = normalize(raw_data, momentum_window=window)
        for threshold in THRESHOLD_CANDIDATES:
            rm = make_risk_manager()
            engine = BacktestEngine(
                strategy=MomentumStrategy(threshold=threshold),
                risk_manager=rm,
                initial_balance=1_000_000.0,
            )
            result = engine.run(data, warmup_bars=50)
            m = calculate_metrics(result)

            # スコア: trades>=30 を重視 + sharpe
            trade_bonus = 1.0 if m.total_trades >= 30 else m.total_trades / 30.0
            score = m.sharpe_ratio * trade_bonus if m.sharpe_ratio > 0 else m.sharpe_ratio

            pnl_pct = m.total_pnl / result.initial_balance * 100
            win_pct = m.win_rate * 100

            print(f"{window:>7} {threshold:>10.1f} {m.total_trades:>7} {pnl_pct:>8.2f}% {win_pct:>6.1f}% {m.sharpe_ratio:>8.2f} {score:>8.3f}")
            results.append({
                "momentum_window": window,
                "threshold": threshold,
                "trades": m.total_trades,
                "pnl_pct": pnl_pct,
                "win_rate": win_pct,
                "sharpe": m.sharpe_ratio,
                "score": score,
            })

    return results


def run_walk_forward(raw_data: pd.DataFrame, threshold: float, momentum_window: int) -> None:
    """指定パラメータで Walk-Forward 検証を実行。"""
    data = normalize(raw_data, momentum_window=momentum_window)
    print(f"\n{'='*60}")
    print(f"Walk-Forward 検証 (threshold={threshold}, momentum_window={momentum_window}, timeframe=15min)")
    print(f"{'='*60}")

    rm = make_risk_manager()
    engine = BacktestEngine(
        strategy=MomentumStrategy(threshold=threshold),
        risk_manager=rm,
        initial_balance=1_000_000.0,
    )

    wf_results = engine.run_walk_forward(
        data,
        train_months=6,
        validation_months=4,
        step_months=4,
        warmup_bars=50,
    )

    print(f"{'W':>3} {'検証期間':>22} {'取引':>5} {'損益%':>8} {'勝率':>7} {'Sharpe':>8}")
    print("-" * 60)

    total_trades = 0
    for i, r in enumerate(wf_results, 1):
        if not r.trades:
            print(f"{i:>3} {'(データなし)':>22} {0:>5} {'--':>8} {'--':>7} {'--':>8}")
            continue
        m = calculate_metrics(r)
        pnl_pct = m.total_pnl / r.initial_balance * 100
        period = f"{r.start_time:%Y-%m} 〜 {r.end_time:%Y-%m}"
        print(f"{i:>3} {period:>22} {m.total_trades:>5} {pnl_pct:>8.2f}% {m.win_rate*100:>6.1f}% {m.sharpe_ratio:>8.2f}")
        total_trades += m.total_trades

    print(f"\n合計取引数: {total_trades}  (本番基準: 30+)")
    print(f"ウィンドウ数: {len(wf_results)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="15min グリッドサーチ + WF")
    parser.add_argument("--fetch", action="store_true", help="15min データをフェッチ")
    parser.add_argument("--backfill-years", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=None,
                        help="WFのみ実行する場合に threshold を指定（グリッドサーチをスキップ）")
    parser.add_argument("--window", type=int, default=None,
                        help="WFのみ実行する場合に momentum_window を指定（グリッドサーチをスキップ）")
    args = parser.parse_args()

    if args.fetch:
        asyncio.run(fetch_15min(args.backfill_years))

    raw_data = load_raw_data()
    print(f"[INFO] データ: {len(raw_data)} 本  {raw_data['timestamp'].iloc[0]:%Y-%m-%d} 〜 {raw_data['timestamp'].iloc[-1]:%Y-%m-%d}")

    if args.threshold is not None and args.window is not None:
        best_threshold = args.threshold
        best_window = args.window
    else:
        grid_results = run_grid_search(raw_data)
        # スコア最大の組み合わせを選択（ただし trades >= 5 以上のみ対象）
        valid = [r for r in grid_results if r["trades"] >= 5]
        if not valid:
            valid = grid_results
        best = max(valid, key=lambda r: r["score"])
        best_threshold = best["threshold"]
        best_window = best["momentum_window"]
        print(f"\n最良パラメータ: threshold={best_threshold}, momentum_window={best_window} "
              f"(score={best['score']:.3f}, trades={best['trades']})")

    run_walk_forward(raw_data, best_threshold, best_window)


if __name__ == "__main__":
    main()
