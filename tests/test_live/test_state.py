"""LiveStateStore のテスト。"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from cryptbot.data.storage import Storage
from cryptbot.live.state import LiveState, LiveStateStore
from cryptbot.risk.manager import PortfolioState
from cryptbot.strategies.base import Direction, Signal
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(db_path=tmp_path / "test.db", data_dir=tmp_path / "data")
    s.initialize()
    return s


@pytest.fixture()
def store(storage: Storage) -> LiveStateStore:
    st = LiveStateStore(storage)
    st.initialize()
    return st


def _make_portfolio(balance: float = 100_000.0) -> PortfolioState:
    today = date(2024, 1, 15)
    return PortfolioState(
        balance=balance,
        peak_balance=balance,
        position_size=0.0,
        entry_price=0.0,
        daily_loss=0.0,
        weekly_loss=0.0,
        monthly_loss=0.0,
        consecutive_losses=0,
        last_reset_date=today,
        last_weekly_reset=today,
        last_monthly_reset=today,
    )


# ------------------------------------------------------------------ #
# initialize
# ------------------------------------------------------------------ #

class TestInitialize:
    def test_creates_table(self, store: LiveStateStore, storage: Storage) -> None:
        """initialize() が live_state テーブルを作成する。"""
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='live_state'"
            ).fetchone()
        assert row is not None

    def test_idempotent(self, store: LiveStateStore, storage: Storage) -> None:
        """initialize() を複数回呼んでも例外を送出しない。"""
        store.initialize()
        store.initialize()


# ------------------------------------------------------------------ #
# load / save
# ------------------------------------------------------------------ #

class TestLoadSave:
    def test_load_none_initially(self, store: LiveStateStore) -> None:
        """未保存状態では load() が None を返す。"""
        assert store.load() is None

    def test_save_and_load_roundtrip(self, store: LiveStateStore) -> None:
        """save() した状態を load() で復元できる。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.balance == state.balance
        assert loaded.peak_balance == state.peak_balance
        assert loaded.pending_signal_direction is None
        assert loaded.last_processed_bar_ts is None

    def test_upsert_overwrites(self, store: LiveStateStore) -> None:
        """2 回 save() すると最新値で上書きされる（id=1 のみ存在）。"""
        state1 = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(balance=100_000.0),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        store.save(state1)

        state2 = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(balance=200_000.0),
            pending_signal=None,
            pending_cvar_action="warning",
            recent_trade_returns=[0.01, -0.02],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        store.save(state2)

        loaded = store.load()
        assert loaded is not None
        assert loaded.balance == 200_000.0
        assert loaded.pending_cvar_action == "warning"

        # DB に行が 1 件だけあることを確認
        with store._storage._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM live_state").fetchone()[0]
        assert count == 1


# ------------------------------------------------------------------ #
# 変換ヘルパー
# ------------------------------------------------------------------ #

class TestToPortfolioState:
    def test_roundtrip(self) -> None:
        """PortfolioState → LiveState → PortfolioState が同値になる。"""
        original = _make_portfolio(balance=999_999.0)
        live_state = LiveStateStore.from_portfolio_state(
            portfolio=original,
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        restored = LiveStateStore.to_portfolio_state(live_state)

        assert restored.balance == original.balance
        assert restored.peak_balance == original.peak_balance
        assert restored.last_reset_date == original.last_reset_date


class TestExtractPendingSignal:
    def test_none_signal(self) -> None:
        """pending_signal_direction=None の場合は None を返す。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        assert LiveStateStore.extract_pending_signal(state) is None

    def test_buy_signal_roundtrip(self) -> None:
        """BUY シグナルを保存して復元できる。"""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=JST)
        signal = Signal(
            direction=Direction.BUY,
            confidence=0.85,
            reason="MA crossover",
            timestamp=ts,
        )
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=signal,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        restored = LiveStateStore.extract_pending_signal(state)

        assert restored is not None
        assert restored.direction == Direction.BUY
        assert abs(restored.confidence - 0.85) < 1e-9
        assert restored.reason == "MA crossover"

    def test_invalid_direction_returns_none(self) -> None:
        """不正な direction 値の場合は None を返す（防御的）。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        state.pending_signal_direction = "INVALID_DIR"
        assert LiveStateStore.extract_pending_signal(state) is None


class TestExtractRecentReturns:
    def test_empty_list(self) -> None:
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        assert LiveStateStore.extract_recent_returns(state) == []

    def test_float_list_roundtrip(self) -> None:
        returns = [0.01, -0.02, 0.005, -0.015]
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=returns,
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        restored = LiveStateStore.extract_recent_returns(state)
        assert restored == pytest.approx(returns)

    def test_corrupt_json_returns_empty(self) -> None:
        """破損した JSON の場合は空リストを返す（防御的）。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        state.recent_trade_returns = "NOT_VALID_JSON"
        assert LiveStateStore.extract_recent_returns(state) == []


class TestLastPrice:
    def test_last_price_roundtrip(self, store: LiveStateStore) -> None:
        """last_price が save/load でラウンドトリップする。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
            last_price=5_500_000.0,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.last_price == 5_500_000.0

    def test_last_price_default_zero(self, store: LiveStateStore) -> None:
        """last_price 引数省略時は 0.0 になる。"""
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.last_price == 0.0

    def test_migration_adds_last_price_column(self, storage: Storage) -> None:
        """既存 DB に last_price カラムがなくても initialize() でマイグレーションされる。"""
        # まず last_price なしのテーブルを作る
        with storage._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS live_state (
                    id INTEGER PRIMARY KEY,
                    balance REAL NOT NULL,
                    peak_balance REAL NOT NULL,
                    position_size REAL NOT NULL DEFAULT 0.0,
                    entry_price REAL NOT NULL DEFAULT 0.0,
                    daily_loss REAL NOT NULL DEFAULT 0.0,
                    weekly_loss REAL NOT NULL DEFAULT 0.0,
                    monthly_loss REAL NOT NULL DEFAULT 0.0,
                    consecutive_losses INTEGER NOT NULL DEFAULT 0,
                    last_reset_date TEXT NOT NULL,
                    last_weekly_reset TEXT NOT NULL,
                    last_monthly_reset TEXT NOT NULL,
                    pending_signal_direction TEXT,
                    pending_signal_confidence REAL,
                    pending_signal_reason TEXT,
                    pending_signal_ts TEXT,
                    pending_cvar_action TEXT NOT NULL DEFAULT 'normal',
                    recent_trade_returns TEXT NOT NULL DEFAULT '[]',
                    position_entry_time TEXT,
                    position_entry_price REAL NOT NULL DEFAULT 0.0,
                    last_processed_bar_ts TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

        # initialize() でマイグレーションされること
        store = LiveStateStore(storage)
        store.initialize()

        with storage._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(live_state)").fetchall()}
        assert "last_price" in cols


class TestExtractPositionEntryTime:
    def test_none_entry_time(self) -> None:
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
        )
        assert LiveStateStore.extract_position_entry_time(state) is None

    def test_entry_time_roundtrip(self) -> None:
        entry_time = datetime(2024, 1, 15, 9, 0, 0, tzinfo=JST)
        state = LiveStateStore.from_portfolio_state(
            portfolio=_make_portfolio(),
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=entry_time,
            position_entry_price=5_000_000.0,
            last_processed_bar_ts=None,
        )
        restored = LiveStateStore.extract_position_entry_time(state)
        assert restored is not None
        assert restored == entry_time
