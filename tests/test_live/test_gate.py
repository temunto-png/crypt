"""phase_4_gate / verify_api_permissions のテスト。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from pydantic import SecretStr

from cryptbot.live.gate import LiveGateError, phase_4_gate, verify_api_permissions


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_settings(
    mode: str = "live",
    api_key: str = "test_key_abc123",
    api_secret: str = "test_secret_xyz789",
) -> MagicMock:
    """Settings モックを生成する。"""
    settings = MagicMock()
    settings.mode = mode
    settings.bitbank_api_key = SecretStr(api_key)
    settings.bitbank_api_secret = SecretStr(api_secret)
    return settings


def _make_kill_switch(active: bool = False) -> MagicMock:
    """KillSwitch モックを生成する。"""
    ks = MagicMock()
    # active は property なので type(ks).active を設定
    type(ks).active = PropertyMock(return_value=active)
    return ks


# ------------------------------------------------------------------ #
# phase_4_gate: 正常系
# ------------------------------------------------------------------ #

class TestPhase4GatePass:
    def test_all_conditions_met(self) -> None:
        """全条件を満たす場合は例外を送出しない。"""
        phase_4_gate(
            settings=_make_settings(),
            confirm_live=True,
            kill_switch=_make_kill_switch(active=False),
        )

    def test_returns_none(self) -> None:
        """戻り値は None（副作用のみ）。"""
        result = phase_4_gate(
            settings=_make_settings(),
            confirm_live=True,
            kill_switch=_make_kill_switch(active=False),
        )
        assert result is None


# ------------------------------------------------------------------ #
# phase_4_gate: 異常系
# ------------------------------------------------------------------ #

class TestPhase4GateFail:
    def test_wrong_mode_raises(self) -> None:
        """mode が live でない場合は LiveGateError。"""
        with pytest.raises(LiveGateError, match="TRADING_MODE"):
            phase_4_gate(
                settings=_make_settings(mode="paper"),
                confirm_live=True,
                kill_switch=_make_kill_switch(active=False),
            )

    def test_no_confirm_live_raises(self) -> None:
        """confirm_live=False の場合は LiveGateError。"""
        with pytest.raises(LiveGateError, match="--confirm-live"):
            phase_4_gate(
                settings=_make_settings(),
                confirm_live=False,
                kill_switch=_make_kill_switch(active=False),
            )

    def test_kill_switch_active_raises(self) -> None:
        """kill_switch が active の場合は LiveGateError。"""
        with pytest.raises(LiveGateError, match="kill_switch"):
            phase_4_gate(
                settings=_make_settings(),
                confirm_live=True,
                kill_switch=_make_kill_switch(active=True),
            )

    def test_empty_api_key_raises(self) -> None:
        """API キーが空の場合は LiveGateError。"""
        with pytest.raises(LiveGateError, match="API キー"):
            phase_4_gate(
                settings=_make_settings(api_key=""),
                confirm_live=True,
                kill_switch=_make_kill_switch(active=False),
            )

    def test_empty_api_secret_raises(self) -> None:
        """API シークレットが空の場合は LiveGateError。"""
        with pytest.raises(LiveGateError, match="API キー"):
            phase_4_gate(
                settings=_make_settings(api_secret=""),
                confirm_live=True,
                kill_switch=_make_kill_switch(active=False),
            )

    def test_mode_checked_before_confirm(self) -> None:
        """モードチェックは confirm_live チェックより先に実行される。"""
        with pytest.raises(LiveGateError, match="TRADING_MODE"):
            phase_4_gate(
                settings=_make_settings(mode="paper"),
                confirm_live=False,          # こちらも NG だが先にモードで落ちる
                kill_switch=_make_kill_switch(active=False),
            )

    def test_confirm_checked_before_kill_switch(self) -> None:
        """confirm_live チェックは kill_switch チェックより先に実行される。"""
        with pytest.raises(LiveGateError, match="--confirm-live"):
            phase_4_gate(
                settings=_make_settings(),
                confirm_live=False,
                kill_switch=_make_kill_switch(active=True),  # こちらも NG
            )


# ------------------------------------------------------------------ #
# verify_api_permissions: 正常系
# ------------------------------------------------------------------ #

class TestVerifyApiPermissionsPass:
    @pytest.mark.asyncio
    async def test_returns_assets(self) -> None:
        """get_assets() が成功した場合はアセット dict を返す。"""
        expected = {"btc": 0.5, "jpy": 500_000.0}
        exchange = MagicMock()
        exchange.get_assets = AsyncMock(return_value=expected)

        result = await verify_api_permissions(exchange)

        assert result == expected
        exchange.get_assets.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_assets_ok(self) -> None:
        """get_assets() が空 dict を返しても例外を送出しない。"""
        exchange = MagicMock()
        exchange.get_assets = AsyncMock(return_value={})

        result = await verify_api_permissions(exchange)
        assert result == {}


# ------------------------------------------------------------------ #
# verify_api_permissions: 異常系
# ------------------------------------------------------------------ #

class TestVerifyApiPermissionsFail:
    @pytest.mark.asyncio
    async def test_exchange_error_raises_gate_error(self) -> None:
        """get_assets() が例外を送出した場合は LiveGateError にラップされる。"""
        exchange = MagicMock()
        exchange.get_assets = AsyncMock(side_effect=RuntimeError("connection refused"))

        with pytest.raises(LiveGateError, match="API 疎通確認"):
            await verify_api_permissions(exchange)

    @pytest.mark.asyncio
    async def test_original_error_is_chained(self) -> None:
        """LiveGateError には元例外が __cause__ として付与される。"""
        original_exc = ValueError("invalid response")
        exchange = MagicMock()
        exchange.get_assets = AsyncMock(side_effect=original_exc)

        with pytest.raises(LiveGateError) as exc_info:
            await verify_api_permissions(exchange)

        assert exc_info.value.__cause__ is original_exc

    @pytest.mark.asyncio
    async def test_api_key_not_leaked_in_error(self) -> None:
        """エラーメッセージに API キーが含まれない（sanitize_error 経由）。

        sanitize_error は 20文字以上の英数字列を [REDACTED] に置換する。
        """
        # 20文字以上の純英数字（アンダースコアなし）= sanitize_error にマスクされる
        secret = "SecretApiKey1234567890XYZ"
        exchange = MagicMock()
        exchange.get_assets = AsyncMock(
            side_effect=RuntimeError(f"auth failed: key={secret}")
        )

        with pytest.raises(LiveGateError) as exc_info:
            await verify_api_permissions(exchange)

        # sanitize_error がキーをマスクするため生のシークレットは含まれない
        assert secret not in str(exc_info.value)
