"""PaperStateStore のテスト。"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from cryptbot.data.storage import Storage
from cryptbot.paper.state import PaperState, PaperStateStore
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
def store(storage: Storage) -> PaperStateStore:
    st = PaperStateStore(storage)
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
    def test_creates_table(self, store: PaperStateStore, storage: Storage) -> None:
        """initialize() が paper_state テーブルを作成する。"""
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_state'"
            ).fetchone()
        assert row is not None

    def test_idempotent(self, store: PaperStateStore) -> None:
        """二重呼び出しでもエラーにならない。"""
        store.initialize()  # 2回目


# ------------------------------------------------------------------ #
# load / save
# ------------------------------------------------------------------ #

class TestLoadSave:
    def test_load_returns_none_initially(self, store: PaperStateStore) -> None:
        """初回は None を返す。"""
        assert store.load() is None

    def test_save_and_load_round_trip(self, store: PaperStateStore) -> None:
        """保存 → 読み込みで値が一致する。"""
        portfolio = _make_portfolio(200_000.0)
        ts = datetime(2024, 1, 15, 9, 0, tzinfo=JST)
        state = PaperStateStore.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[0.01, -0.02],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=ts,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.balance == 200_000.0
        assert loaded.pending_signal_direction is None
        assert json.loads(loaded.recent_trade_returns) == [0.01, -0.02]
        assert loaded.last_processed_bar_ts is not None
        assert "2024-01-15" in loaded.last_processed_bar_ts

    def test_save_overwrites_single_row(self, store: PaperStateStore, storage: Storage) -> None:
        """複数回 save しても行が 1 つだけ存在する。"""
        for balance in [100_000.0, 110_000.0, 120_000.0]:
            portfolio = _make_portfolio(balance)
            state = PaperStateStore.from_portfolio_state(
                portfolio=portfolio,
                pending_signal=None,
                pending_cvar_action="normal",
                recent_trade_returns=[],
                position_entry_time=None,
                position_entry_price=0.0,
                last_processed_bar_ts=None,
            )
            store.save(state)

        with storage._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM paper_state").fetchone()[0]
        assert count == 1

        loaded = store.load()
        assert loaded is not None
        assert loaded.balance == 120_000.0

    def test_save_with_pending_signal(self, store: PaperStateStore) -> None:
        """pending_signal を含む状態の round-trip。"""
        portfolio = _make_portfolio()
        ts = datetime(2024, 3, 10, 10, 0, tzinfo=JST)
        signal = Signal(
            direction=Direction.BUY,
            confidence=0.8,
            reason="golden_cross",
            timestamp=ts,
        )
        state = PaperStateStore.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=signal,
            pending_cvar_action="half",
            recent_trade_returns=[-0.01, 0.02, -0.03],
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=ts,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.pending_signal_direction == "BUY"
        assert loaded.pending_signal_confidence == pytest.approx(0.8)
        assert loaded.pending_signal_reason == "golden_cross"
        assert loaded.pending_cvar_action == "half"

    def test_save_with_open_position(self, store: PaperStateStore) -> None:
        """ポジション保有中の状態の round-trip。"""
        import dataclasses
        portfolio = _make_portfolio()
        portfolio = dataclasses.replace(
            portfolio,
            position_size=0.001,
            entry_price=5_000_000.0,
            balance=95_000.0,
        )
        entry_time = datetime(2024, 2, 1, 9, 0, tzinfo=JST)
        state = PaperStateStore.from_portfolio_state(
            portfolio=portfolio,
            pending_signal=None,
            pending_cvar_action="normal",
            recent_trade_returns=[],
            position_entry_time=entry_time,
            position_entry_price=5_000_000.0,
            last_processed_bar_ts=None,
        )
        store.save(state)
        loaded = store.load()

        assert loaded is not None
        assert loaded.position_size == pytest.approx(0.001)
        assert loaded.entry_price == pytest.approx(5_000_000.0)
        assert loaded.position_entry_price == pytest.approx(5_000_000.0)
        assert loaded.position_entry_time is not None


# ------------------------------------------------------------------ #
# 変換ヘルパー
# ------------------------------------------------------------------ #

class TestConversionHelpers:
    def test_to_portfolio_state(self) -> None:
        """PaperState → PortfolioState の変換が正しい。"""
        today = date(2024, 5, 1)
        state = PaperState(
            balance=150_000.0,
            peak_balance=160_000.0,
            position_size=0.002,
            entry_price=4_800_000.0,
            daily_loss=500.0,
            weekly_loss=1200.0,
            monthly_loss=3000.0,
            consecutive_losses=2,
            last_reset_date="2024-05-01",
            last_weekly_reset="2024-04-29",
            last_monthly_reset="2024-05-01",
            pending_signal_direction=None,
            pending_signal_confidence=None,
            pending_signal_reason=None,
            pending_signal_ts=None,
            pending_cvar_action="normal",
            recent_trade_returns="[]",
            position_entry_time=None,
            position_entry_price=4_800_000.0,
            last_processed_bar_ts=None,
            updated_at="2024-05-01T09:00:00+09:00",
        )
        portfolio = PaperStateStore.to_portfolio_state(state)

        assert portfolio.balance == pytest.approx(150_000.0)
        assert portfolio.peak_balance == pytest.approx(160_000.0)
        assert portfolio.position_size == pytest.approx(0.002)
        assert portfolio.consecutive_losses == 2
        assert portfolio.last_reset_date == date(2024, 5, 1)

    def test_extract_pending_signal_none(self) -> None:
        """pending_signal_direction が None のとき None を返す。"""
        state = PaperState(
            balance=100_000.0, peak_balance=100_000.0,
            position_size=0.0, entry_price=0.0,
            daily_loss=0.0, weekly_loss=0.0, monthly_loss=0.0,
            consecutive_losses=0,
            last_reset_date="2024-01-01",
            last_weekly_reset="2024-01-01",
            last_monthly_reset="2024-01-01",
            pending_signal_direction=None,
            pending_signal_confidence=None,
            pending_signal_reason=None,
            pending_signal_ts=None,
            pending_cvar_action="normal",
            recent_trade_returns="[]",
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
            updated_at="2024-01-01T00:00:00+09:00",
        )
        assert PaperStateStore.extract_pending_signal(state) is None

    def test_extract_pending_signal_buy(self) -> None:
        """BUY シグナルを正しく復元する。"""
        ts = datetime(2024, 6, 1, 10, 0, tzinfo=JST)
        state = PaperState(
            balance=100_000.0, peak_balance=100_000.0,
            position_size=0.0, entry_price=0.0,
            daily_loss=0.0, weekly_loss=0.0, monthly_loss=0.0,
            consecutive_losses=0,
            last_reset_date="2024-06-01",
            last_weekly_reset="2024-06-01",
            last_monthly_reset="2024-06-01",
            pending_signal_direction="BUY",
            pending_signal_confidence=0.75,
            pending_signal_reason="ma_cross",
            pending_signal_ts=ts.isoformat(),
            pending_cvar_action="normal",
            recent_trade_returns="[]",
            position_entry_time=None,
            position_entry_price=0.0,
            last_processed_bar_ts=None,
            updated_at="2024-06-01T10:00:00+09:00",
        )
        signal = PaperStateStore.extract_pending_signal(state)
        assert signal is not None
        assert signal.direction == Direction.BUY
        assert signal.confidence == pytest.approx(0.75)
        assert signal.reason == "ma_cross"

    def test_extract_recent_returns_valid(self) -> None:
        """JSON 配列から float リストを復元する。"""
        state_mock = type("S", (), {
            "recent_trade_returns": json.dumps([0.01, -0.02, 0.005])
        })()
        result = PaperStateStore.extract_recent_returns(state_mock)
        assert result == pytest.approx([0.01, -0.02, 0.005])

    def test_extract_recent_returns_invalid(self) -> None:
        """壊れた JSON は空リストを返す。"""
        state_mock = type("S", (), {"recent_trade_returns": "invalid json"})()
        result = PaperStateStore.extract_recent_returns(state_mock)
        assert result == []

    def test_extract_position_entry_time(self) -> None:
        """position_entry_time を正しく復元する。"""
        ts = datetime(2024, 7, 1, 9, 0, tzinfo=JST)
        state_mock = type("S", (), {"position_entry_time": ts.isoformat()})()
        result = PaperStateStore.extract_position_entry_time(state_mock)
        assert result is not None
        assert result.tzinfo is not None
