"""RiskManager / KillSwitch のテスト。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptbot.config.settings import CircuitBreakerSettings, CvarSettings
from cryptbot.data.storage import Storage
from cryptbot.risk.kill_switch import KillSwitch, KillSwitchReason
from cryptbot.risk.manager import PortfolioState, RiskManager


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    s = Storage(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )
    s.initialize()
    return s


@pytest.fixture()
def kill_switch(storage: Storage) -> KillSwitch:
    return KillSwitch(storage)


@pytest.fixture()
def cb_settings() -> CircuitBreakerSettings:
    return CircuitBreakerSettings(
        max_trade_loss_pct=0.02,
        daily_loss_pct=0.03,
        weekly_loss_pct=0.05,
        monthly_loss_pct=0.10,
        max_drawdown_pct=0.15,
        max_consecutive_losses=5,
    )


@pytest.fixture()
def cvar_settings() -> CvarSettings:
    return CvarSettings(
        warn_pct=-0.03,
        half_pct=-0.04,
        stop_pct=-0.05,
    )


@pytest.fixture()
def risk_manager(
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
    kill_switch: KillSwitch,
) -> RiskManager:
    return RiskManager(
        settings=cb_settings,
        cvar_settings=cvar_settings,
        kill_switch=kill_switch,
    )


def _make_portfolio(
    balance: float = 1_000_000.0,
    peak_balance: float = 1_000_000.0,
    position_size: float = 0.0,
    entry_price: float = 0.0,
    daily_loss: float = 0.0,
    weekly_loss: float = 0.0,
    monthly_loss: float = 0.0,
    consecutive_losses: int = 0,
    last_reset_date: date | None = None,
    last_weekly_reset: date | None = None,
    last_monthly_reset: date | None = None,
) -> PortfolioState:
    today = date(2026, 4, 14)
    return PortfolioState(
        balance=balance,
        peak_balance=peak_balance,
        position_size=position_size,
        entry_price=entry_price,
        daily_loss=daily_loss,
        weekly_loss=weekly_loss,
        monthly_loss=monthly_loss,
        consecutive_losses=consecutive_losses,
        last_reset_date=last_reset_date or today,
        last_weekly_reset=last_weekly_reset or today,
        last_monthly_reset=last_monthly_reset or today,
    )


# ------------------------------------------------------------------ #
# check_entry テスト
# ------------------------------------------------------------------ #

def test_check_entry_kill_switch_active(
    risk_manager: RiskManager,
    kill_switch: KillSwitch,
) -> None:
    """kill_switch active → denied。"""
    kill_switch.activate(KillSwitchReason.MANUAL, portfolio_value=1_000_000.0, drawdown_pct=0.0)
    portfolio = _make_portfolio()
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "kill_switch_active" in result.triggered_limits


def test_check_entry_daily_loss_exceeded(risk_manager: RiskManager) -> None:
    """日次損失超過 → denied。"""
    # daily_loss_pct=0.03, balance=1_000_000 → limit=30_000
    portfolio = _make_portfolio(daily_loss=30_000.0)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "daily_loss_exceeded" in result.triggered_limits


def test_check_entry_max_drawdown_exceeded(risk_manager: RiskManager) -> None:
    """最大ドローダウン超過 → denied。"""
    # max_drawdown_pct=0.15, peak=1_000_000 → limit=150_000, drawdown=balance差分
    portfolio = _make_portfolio(balance=849_000.0, peak_balance=1_000_000.0)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "max_drawdown_exceeded" in result.triggered_limits


def test_check_entry_consecutive_losses_exceeded(risk_manager: RiskManager) -> None:
    """連敗超過 → denied。"""
    # max_consecutive_losses=5
    portfolio = _make_portfolio(consecutive_losses=5)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "consecutive_losses_exceeded" in result.triggered_limits


def test_check_entry_position_already_open(risk_manager: RiskManager) -> None:
    """既存ポジション → denied。"""
    portfolio = _make_portfolio(position_size=0.01, entry_price=10_000_000.0)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "position_already_open" in result.triggered_limits


def test_check_entry_all_checks_passed(risk_manager: RiskManager) -> None:
    """全チェック通過 → allowed。"""
    portfolio = _make_portfolio()
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is True
    assert result.triggered_limits == []


# ------------------------------------------------------------------ #
# calculate_position_size テスト
# ------------------------------------------------------------------ #

def test_calculate_position_size_normal(risk_manager: RiskManager) -> None:
    """通常ケース: 30% * balance / price。"""
    portfolio = _make_portfolio(balance=1_000_000.0)
    price = 10_000_000.0
    # 1_000_000 * 0.30 / 10_000_000 = 0.03 BTC
    size = risk_manager.calculate_position_size(portfolio, price)
    assert size == pytest.approx(0.03, abs=1e-8)


def test_calculate_position_size_below_minimum(risk_manager: RiskManager) -> None:
    """最小注文未満 → 0.0。"""
    # balance * 0.30 / price < 0.0001 → balance=1, price=10_000_000
    portfolio = _make_portfolio(balance=1.0)
    size = risk_manager.calculate_position_size(portfolio, current_price=10_000_000.0)
    assert size == 0.0


def test_calculate_position_size_floor_to_unit(risk_manager: RiskManager) -> None:
    """0.0001 BTC 単位に切り捨て。"""
    # 1_050_000 * 0.30 / 10_000_000 = 0.0315 → 切り捨て → 0.0315
    # より端数が出るケース: balance=1_003_333, price=10_000_000
    # → 1_003_333 * 0.30 / 10_000_000 = 0.03009999 → floor → 0.0300
    portfolio = _make_portfolio(balance=1_003_333.0)
    size = risk_manager.calculate_position_size(portfolio, current_price=10_000_000.0)
    # 0.03009999 → floor at 0.0001 unit → 0.0300
    assert size == pytest.approx(0.0300, abs=1e-8)


# ------------------------------------------------------------------ #
# record_trade_result テスト
# ------------------------------------------------------------------ #

def test_record_trade_result_loss_updates_daily_loss(risk_manager: RiskManager) -> None:
    """損失時の daily_loss 更新、連敗 +1。
    エントリー時に 100_000 JPY を balance から差し引き済みとして、
    exit_proceeds=90_000（cash_delta）, realized_pnl=-10_000 のケース。
    balance: 900_000 + 90_000 = 990_000 が期待値。
    """
    today = date(2026, 4, 14)
    # エントリーで 100_000 差し引き済み → 残高 900_000
    portfolio = _make_portfolio(balance=900_000.0, consecutive_losses=2)
    updated = risk_manager.record_trade_result(
        portfolio, cash_delta=90_000.0, realized_pnl=-10_000.0, current_date=today
    )
    assert updated.daily_loss == pytest.approx(10_000.0)
    assert updated.weekly_loss == pytest.approx(10_000.0)
    assert updated.monthly_loss == pytest.approx(10_000.0)
    assert updated.consecutive_losses == 3
    # 900_000 + 90_000 = 990_000（元の 1_000_000 から損失 10_000 分を差し引いた値）
    assert updated.balance == pytest.approx(990_000.0)


def test_record_trade_result_profit_resets_consecutive_losses(risk_manager: RiskManager) -> None:
    """利益時の連敗リセット、peak_balance 更新。
    エントリーで 300_000 差し引き済み → 残高 700_000。
    exit_proceeds=350_000（cash_delta）, realized_pnl=+50_000。
    balance: 700_000 + 350_000 = 1_050_000。
    """
    today = date(2026, 4, 14)
    portfolio = _make_portfolio(balance=700_000.0, peak_balance=1_000_000.0, consecutive_losses=3)
    updated = risk_manager.record_trade_result(
        portfolio, cash_delta=350_000.0, realized_pnl=50_000.0, current_date=today
    )
    assert updated.consecutive_losses == 0
    assert updated.balance == pytest.approx(1_050_000.0)
    assert updated.peak_balance == pytest.approx(1_050_000.0)
    assert updated.daily_loss == 0.0


def test_record_trade_result_daily_reset_on_date_change(risk_manager: RiskManager) -> None:
    """日付変更時の日次リセット。
    エントリーで 50_000 差し引き済み → 残高 940_000。
    exit_proceeds=45_000（cash_delta）, realized_pnl=-5_000。
    balance: 940_000 + 45_000 = 985_000。
    """
    yesterday = date(2026, 4, 13)
    today = date(2026, 4, 14)
    # 昨日の損失が残っている状態（エントリー後残高 940_000）
    portfolio = _make_portfolio(
        balance=940_000.0,
        daily_loss=10_000.0,
        weekly_loss=10_000.0,
        last_reset_date=yesterday,
    )
    updated = risk_manager.record_trade_result(
        portfolio, cash_delta=45_000.0, realized_pnl=-5_000.0, current_date=today
    )
    # 日次は今日の損失のみ（リセット後に加算）
    assert updated.daily_loss == pytest.approx(5_000.0)
    assert updated.last_reset_date == today
    # 週次はリセットされない（月曜ではない、2026-04-14は火曜）
    assert updated.weekly_loss == pytest.approx(15_000.0)


# ------------------------------------------------------------------ #
# get_cvar_action テスト
# ------------------------------------------------------------------ #

def test_get_cvar_action_insufficient_samples(risk_manager: RiskManager) -> None:
    """サンプル不足 → "normal"。"""
    returns = [-0.01] * 19
    assert risk_manager.get_cvar_action(returns) == "normal"


def test_get_cvar_action_normal(risk_manager: RiskManager) -> None:
    """cvar > warn_pct(-0.03) → "normal"。"""
    # 全リターン -0.01（下位5%も -0.01 なのでCVaR=-0.01 > -0.03）
    returns = [-0.01] * 50
    assert risk_manager.get_cvar_action(returns) == "normal"


def test_get_cvar_action_warning(risk_manager: RiskManager) -> None:
    """warn_pct >= cvar > half_pct → "warning"。"""
    # 下位5%が約 -0.035 になるよう設定
    returns = [0.01] * 47 + [-0.035, -0.035, -0.035]
    assert risk_manager.get_cvar_action(returns) == "warning"


def test_get_cvar_action_stop(risk_manager: RiskManager) -> None:
    """cvar <= stop_pct(-0.05) → "stop"。"""
    # 下位5%が -0.06 以下
    returns = [0.01] * 47 + [-0.06, -0.07, -0.08]
    assert risk_manager.get_cvar_action(returns) == "stop"


def test_get_cvar_action_half(risk_manager: RiskManager) -> None:
    """half_pct >= cvar > stop_pct → "half"。"""
    # 下位5%が約 -0.045 になるよう設定（-0.04 と -0.05 の間）
    returns = [0.01] * 47 + [-0.045, -0.045, -0.045]
    assert risk_manager.get_cvar_action(returns) == "half"


# ------------------------------------------------------------------ #
# KillSwitch テスト
# ------------------------------------------------------------------ #

def test_kill_switch_activate(kill_switch: KillSwitch) -> None:
    """activate → active になること。"""
    assert kill_switch.active is False
    kill_switch.activate(
        KillSwitchReason.MAX_DRAWDOWN,
        portfolio_value=900_000.0,
        drawdown_pct=0.15,
    )
    assert kill_switch.active is True
    assert kill_switch.reason == KillSwitchReason.MAX_DRAWDOWN


def test_kill_switch_activate_no_double_activation(kill_switch: KillSwitch) -> None:
    """二重発動しないこと（2回目の activate は無視される）。"""
    kill_switch.activate(
        KillSwitchReason.MAX_DRAWDOWN,
        portfolio_value=900_000.0,
        drawdown_pct=0.15,
    )
    first_reason = kill_switch.reason
    # 別の理由で再度 activate
    kill_switch.activate(
        KillSwitchReason.MANUAL,
        portfolio_value=850_000.0,
        drawdown_pct=0.20,
    )
    # reason は変わらない
    assert kill_switch.reason == first_reason
    assert kill_switch.active is True


def test_kill_switch_deactivate(kill_switch: KillSwitch) -> None:
    """deactivate → inactive になること。"""
    kill_switch.activate(
        KillSwitchReason.MONTHLY_LOSS,
        portfolio_value=900_000.0,
        drawdown_pct=0.10,
    )
    assert kill_switch.active is True
    kill_switch.deactivate()
    assert kill_switch.active is False
    assert kill_switch.reason is None


def test_check_entry_weekly_loss_exceeded(risk_manager: RiskManager, cb_settings: CircuitBreakerSettings) -> None:
    """weekly_loss が超過 → denied。"""
    # weekly_loss_pct=0.05, balance=1_000_000 → limit=50_000
    portfolio = _make_portfolio(weekly_loss=50_000.0)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "weekly_loss_exceeded" in result.triggered_limits


def test_check_entry_monthly_loss_exceeded(risk_manager: RiskManager, cb_settings: CircuitBreakerSettings) -> None:
    """monthly_loss が超過 → denied。"""
    # monthly_loss_pct=0.10, balance=1_000_000 → limit=100_000
    portfolio = _make_portfolio(monthly_loss=100_000.0)
    result = risk_manager.check_entry(portfolio, signal_confidence=0.9, current_price=10_000_000.0)
    assert result.allowed is False
    assert "monthly_loss_exceeded" in result.triggered_limits


# ------------------------------------------------------------------ #
# P1-02: KillSwitch 永続化テスト
# ------------------------------------------------------------------ #

def test_kill_switch_load_state_restores_active(storage: Storage) -> None:
    """activate 後に新しいインスタンスを作ると __init__ で active が自動復元されること（P1-02 回帰テスト）。"""
    ks1 = KillSwitch(storage)
    ks1.activate(
        KillSwitchReason.MAX_DRAWDOWN,
        portfolio_value=900_000.0,
        drawdown_pct=0.20,
    )
    assert ks1.active is True

    # 新しいインスタンス作成 → __init__ 内の load_state() で active が復元される
    ks2 = KillSwitch(storage)
    assert ks2.active is True  # P1-02: __init__ で load_state() が呼ばれる
    assert ks2.reason == KillSwitchReason.MAX_DRAWDOWN


def test_kill_switch_load_state_inactive_after_deactivate(storage: Storage) -> None:
    """deactivate 後に load_state() すると inactive になること。"""
    ks1 = KillSwitch(storage)
    ks1.activate(
        KillSwitchReason.MANUAL,
        portfolio_value=800_000.0,
        drawdown_pct=0.15,
    )
    ks1.deactivate()

    ks2 = KillSwitch(storage)
    ks2.load_state()
    assert ks2.active is False
    assert ks2.reason is None


def test_kill_switch_load_state_no_events(storage: Storage) -> None:
    """イベントがない場合は load_state() 後も inactive であること。"""
    ks = KillSwitch(storage)
    assert ks.active is False


def test_kill_switch_load_state_uninitialzed_db(tmp_path: Path) -> None:
    """DB 未初期化（audit_log テーブルなし）でも __init__ が例外を発生させないこと（P1-02 回帰テスト）。"""
    s = Storage(db_path=tmp_path / "fresh.db", data_dir=tmp_path / "data")
    # initialize() を呼ばない → audit_log テーブルが存在しない
    ks = KillSwitch(s)  # OperationalError をキャッチして inactive のまま継続
    assert ks.active is False


# ------------------------------------------------------------------ #
# P1-03: 監査ログ失敗時は fail-closed（例外が伝播すること）
# ------------------------------------------------------------------ #

def test_kill_switch_activate_fails_if_audit_write_fails(tmp_path: Path) -> None:
    """audit_log への INSERT が失敗した場合、activate() が例外を伝播すること（P1-03 回帰テスト）。

    kill_switch.activate() は storage.insert_audit_log() を直接呼ぶため、
    DB 障害が発生した場合は例外が握りつぶされず呼び出し元に伝わる。
    """
    import unittest.mock as mock

    s = Storage(db_path=tmp_path / "fail.db", data_dir=tmp_path / "data")
    s.initialize()
    ks = KillSwitch(s)

    # insert_audit_log を強制的に失敗させる
    with mock.patch.object(s, "insert_audit_log", side_effect=RuntimeError("DB write failed")):
        with pytest.raises(RuntimeError, match="DB write failed"):
            ks.activate(
                KillSwitchReason.MANUAL,
                portfolio_value=1_000_000.0,
                drawdown_pct=0.0,
            )


# ------------------------------------------------------------------ #
# P1-01: check_max_trade_loss / check_circuit_breakers テスト
# ------------------------------------------------------------------ #

def test_check_max_trade_loss_not_triggered(risk_manager: RiskManager) -> None:
    """含み損が max_trade_loss_pct 未満 → False。"""
    # max_trade_loss_pct=0.02, entry=1_000_000, current=990_000 → -1% < 2%
    portfolio = _make_portfolio(position_size=0.01, entry_price=1_000_000.0)
    assert risk_manager.check_max_trade_loss(portfolio, current_price=990_000.0) is False


def test_check_max_trade_loss_triggered(risk_manager: RiskManager) -> None:
    """含み損が max_trade_loss_pct を超過 → True。"""
    # max_trade_loss_pct=0.02, entry=1_000_000, current=970_000 → -3% > 2%
    portfolio = _make_portfolio(position_size=0.01, entry_price=1_000_000.0)
    assert risk_manager.check_max_trade_loss(portfolio, current_price=970_000.0) is True


def test_check_max_trade_loss_no_position(risk_manager: RiskManager) -> None:
    """ポジションなし → 常に False。"""
    portfolio = _make_portfolio(position_size=0.0, entry_price=0.0)
    assert risk_manager.check_max_trade_loss(portfolio, current_price=500_000.0) is False


def test_check_circuit_breakers_drawdown_triggers_kill_switch(
    kill_switch: KillSwitch,
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
) -> None:
    """mark_balance ベースのドローダウンが max_drawdown_pct 超過で kill switch が発動すること。"""
    rm = RiskManager(cb_settings, cvar_settings, kill_switch)
    # max_drawdown_pct=0.15, peak=1_000_000 → limit=150_000
    portfolio = _make_portfolio(balance=1_000_000.0, peak_balance=1_000_000.0)
    mark_balance = 840_000.0  # drawdown 16% > 15%
    triggered = rm.check_circuit_breakers(portfolio, mark_balance)
    assert triggered is True
    assert kill_switch.active is True


def test_check_circuit_breakers_no_trigger(
    kill_switch: KillSwitch,
    cb_settings: CircuitBreakerSettings,
    cvar_settings: CvarSettings,
) -> None:
    """ドローダウン・月次損失がいずれも閾値未満 → False、kill switch 発動なし。"""
    rm = RiskManager(cb_settings, cvar_settings, kill_switch)
    portfolio = _make_portfolio(balance=1_000_000.0, peak_balance=1_000_000.0)
    mark_balance = 990_000.0  # drawdown 1%
    triggered = rm.check_circuit_breakers(portfolio, mark_balance)
    assert triggered is False
    assert kill_switch.active is False
