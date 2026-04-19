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

    # load_settings を mock（cryptbot.main モジュールレベルでインポートしているため main.load_settings をパッチ）
    monkeypatch.setattr("cryptbot.main.load_settings", lambda: mock_settings)

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


def test_fetch_ohlcv_initializes_storage_on_fresh_db(tmp_env, monkeypatch):
    """新規 DB で fetch-ohlcv を実行しても初期化エラーが出ないことを確認。"""
    import argparse
    from unittest.mock import AsyncMock, MagicMock, patch
    from cryptbot.config.settings import Settings, PaperSettings, LiveSettings
    import cryptbot.main as main_mod

    mock_settings = Settings(
        paper=PaperSettings(pair="btc_jpy", timeframe="15min", momentum_window=20),
        live=LiveSettings(pair="btc_jpy", timeframe="15min", ohlcv_backfill_years=1),
    )
    monkeypatch.setattr(main_mod, "load_settings", lambda: mock_settings)

    mock_updater = MagicMock()
    mock_updater.backfill = AsyncMock(return_value=0)
    with patch("cryptbot.main.OhlcvUpdater", return_value=mock_updater):
        args = argparse.Namespace(
            db=str(tmp_env["db"]),
            data_dir=str(tmp_env["data_dir"]),
            backfill_years=None,
            fetch_profile="paper",
        )
        from cryptbot.main import _run_fetch_ohlcv
        ret = _run_fetch_ohlcv(args)
    assert ret == 0


def test_fetch_ohlcv_uses_paper_profile_settings(tmp_env, monkeypatch):
    """--fetch-profile paper のとき paper.pair と paper.timeframe が使われることを確認。"""
    import argparse
    from unittest.mock import AsyncMock, MagicMock, patch
    from cryptbot.config.settings import Settings, PaperSettings, LiveSettings
    import cryptbot.main as main_mod

    mock_settings = Settings(
        paper=PaperSettings(pair="btc_jpy", timeframe="15min", momentum_window=20),
        live=LiveSettings(pair="xrp_jpy", timeframe="1hour", ohlcv_backfill_years=1),
    )
    monkeypatch.setattr(main_mod, "load_settings", lambda: mock_settings)

    captured = {}

    def mock_updater_cls(**kwargs):
        captured.update(kwargs)
        m = MagicMock()
        m.backfill = AsyncMock(return_value=0)
        return m

    with patch("cryptbot.main.OhlcvUpdater", side_effect=mock_updater_cls):
        args = argparse.Namespace(
            db=str(tmp_env["db"]),
            data_dir=str(tmp_env["data_dir"]),
            backfill_years=None,
            fetch_profile="paper",
        )
        from cryptbot.main import _run_fetch_ohlcv
        _run_fetch_ohlcv(args)

    assert captured.get("pair") == "btc_jpy"
    assert captured.get("timeframe") == "15min"


def test_config_kill_switch_active_blocks_paper(tmp_env, monkeypatch):
    """settings.kill_switch.active=True のとき KillSwitch が発動することを確認。"""
    import argparse
    from cryptbot.config.settings import Settings, PaperSettings, KillSwitchSettings
    import cryptbot.main as main_mod

    mock_settings = Settings(
        paper=PaperSettings(pair="btc_jpy", timeframe="15min"),
        kill_switch=KillSwitchSettings(active=True),
    )
    monkeypatch.setattr(main_mod, "load_settings", lambda: mock_settings)
    _make_ohlcv_parquet(tmp_env["data_dir"], "btc_jpy", "15min", n=200)

    args = argparse.Namespace(
        db=str(tmp_env["db"]),
        data_dir=str(tmp_env["data_dir"]),
        confirm_live=False,
    )
    from cryptbot.main import _run_paper
    ret = _run_paper(args)
    assert ret == 0

    conn = sqlite3.connect(tmp_env["db"])
    rows = conn.execute(
        "SELECT event_type FROM audit_log WHERE event_type='KILL_SWITCH_ACTIVATED'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1, "KillSwitch が settings から発動されていない"


def test_paper_startup_config_snapshot_recorded(tmp_env, monkeypatch):
    """Paper 起動時に設定スナップショットが audit_log に記録されることを確認。"""
    import argparse
    import json
    from cryptbot.config.settings import Settings, PaperSettings
    import cryptbot.main as main_mod

    mock_settings = Settings(
        paper=PaperSettings(
            pair="btc_jpy", timeframe="15min",
            strategy_name="momentum", momentum_threshold=3.0, momentum_window=20,
        )
    )
    monkeypatch.setattr(main_mod, "load_settings", lambda: mock_settings)
    _make_ohlcv_parquet(tmp_env["data_dir"], "btc_jpy", "15min", n=200)

    args = argparse.Namespace(
        db=str(tmp_env["db"]),
        data_dir=str(tmp_env["data_dir"]),
        confirm_live=False,
    )
    from cryptbot.main import _run_paper
    _run_paper(args)

    conn = sqlite3.connect(tmp_env["db"])
    rows = conn.execute(
        "SELECT details FROM audit_log WHERE event_type='startup_config'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1, "startup_config が audit_log に記録されていない"

    details = json.loads(rows[0][0])
    assert details.get("strategy_name") == "momentum"
    assert details.get("momentum_threshold") == 3.0
    assert details.get("momentum_window") == 20
    assert details.get("timeframe") == "15min"
