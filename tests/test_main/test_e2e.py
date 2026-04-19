"""CLI E2E テスト。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return {"db": db_path, "data_dir": data_dir, "tmp": tmp_path}


def _make_ohlcv_parquet(data_dir: Path, pair: str, timeframe: str, n: int = 200) -> None:
    """テスト用 Parquet を生成して data_dir に保存する。"""
    from cryptbot.data.storage import Storage

    # data_dir 直下に DB を作る（Storage の db_path として使う）
    storage = Storage(db_path=data_dir.parent / "test.db", data_dir=data_dir)
    storage.initialize()

    base_price = 5_000_000.0
    timestamps = pd.date_range("2025-01-01", periods=n, freq="15min", tz="Asia/Tokyo")
    prices = base_price + np.cumsum(np.random.randn(n) * 10_000)
    prices = np.maximum(prices, 1_000_000)

    close = prices * (1 + np.random.randn(n) * 0.0005)
    high = np.maximum(prices, close) * 1.001
    low = np.minimum(prices, close) * 0.999

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.uniform(0.1, 10.0, n),
    })

    storage.save_ohlcv(df, pair=pair, timeframe=timeframe, year=2025)


def test_paper_normalizes_ohlcv_for_momentum(tmp_env, monkeypatch):
    """Paper モードで normalize() が適用されて signal が記録されることを確認。"""
    import argparse
    from cryptbot.config.settings import Settings, PaperSettings
    from cryptbot.main import _run_paper

    pair = "btc_jpy"
    timeframe = "15min"
    _make_ohlcv_parquet(tmp_env["data_dir"], pair, timeframe, n=200)

    mock_settings = Settings(
        paper=PaperSettings(
            pair=pair,
            timeframe=timeframe,
            strategy_name="momentum",
            momentum_threshold=0.01,  # 非常に低い閾値 → 必ずシグナルが出る
            momentum_window=5,
            warmup_bars=50,
        )
    )

    # load_settings を mock（_run_paper 内で from cryptbot.config.settings import load_settings している）
    monkeypatch.setattr("cryptbot.config.settings.load_settings", lambda: mock_settings)

    args = argparse.Namespace(
        db=str(tmp_env["db"]),
        data_dir=str(tmp_env["data_dir"]),
        confirm_live=False,
    )
    ret = _run_paper(args)
    assert ret == 0, f"_run_paper returned {ret}"

    conn = sqlite3.connect(tmp_env["db"])
    rows = conn.execute(
        "SELECT direction FROM audit_log WHERE event_type='signal'"
    ).fetchall()
    conn.close()
    # normalize() が適用されると momentum カラムが生成され、閾値 0.01% 以上のモメンタムがあれば
    # BUY/SELL signal が出る。normalize() なしでは momentum カラムが存在せず常に HOLD になる。
    directions = {r[0] for r in rows}
    assert "BUY" in directions or "SELL" in directions, (
        "normalize() が適用されず momentum カラムが存在しないため、strategy が常に HOLD を返している。"
        f"記録された direction: {directions}"
    )
