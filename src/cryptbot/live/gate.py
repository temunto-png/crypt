"""Phase 4 ライブゲート: live モード起動条件の検証。

fail-closed 設計: 全条件を満たす場合のみ通過する。
いずれかの条件を満たさない場合は LiveGateError を送出する。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptbot.config.settings import Settings
    from cryptbot.exchanges.base import BaseExchange
    from cryptbot.risk.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class LiveGateError(RuntimeError):
    """Phase 4 ライブゲートのいずれかの条件を満たさない場合に送出される。"""


def phase_4_gate(
    settings: "Settings",
    confirm_live: bool,
    kill_switch: "KillSwitch",
) -> None:
    """live モード起動の前提条件を検証する（fail-closed）。

    全ての条件を満たす場合のみ通過。いずれかが失敗すれば LiveGateError を送出。

    検証順:
    1. settings.mode == "live"
    2. confirm_live == True
    3. kill_switch が非アクティブ
    4. API キーが設定されている

    Args:
        settings: アプリケーション設定
        confirm_live: --confirm-live CLI フラグ
        kill_switch: KillSwitch インスタンス

    Raises:
        LiveGateError: いずれかの条件を満たさない場合
    """
    # 1. モード確認
    if settings.mode != "live":
        raise LiveGateError(
            f"TRADING_MODE が live ではありません（現在: {settings.mode!r}）。"
            " 環境変数 CRYPT_MODE=live を設定してください。"
        )

    # 2. --confirm-live フラグ確認
    if not confirm_live:
        raise LiveGateError(
            "--confirm-live フラグが指定されていません。"
            " live モードで実行するには --confirm-live を明示的に指定してください。"
        )

    # 3. kill switch 確認
    if kill_switch.active:
        raise LiveGateError(
            "kill_switch が有効です。live トレードを開始できません。"
            " kill_switch を無効化してから再実行してください。"
        )

    # 4. API キー確認
    api_key = settings.bitbank_api_key.get_secret_value()
    api_secret = settings.bitbank_api_secret.get_secret_value()
    if not api_key or not api_secret:
        raise LiveGateError(
            "bitbank API キーが設定されていません。"
            " CRYPT_BITBANK_API_KEY と CRYPT_BITBANK_API_SECRET を設定してください。"
        )

    logger.info("Phase 4 gate: all conditions passed")


async def verify_api_permissions(exchange: "BaseExchange") -> dict:
    """取引所 API の疎通確認とアセット残高を取得する。

    get_assets() を呼び出し、レスポンスが正常に返ることを確認する。

    Args:
        exchange: 取引所インスタンス（BitbankPrivateExchange を想定）

    Returns:
        アセット残高 dict（get_assets() のレスポンス）

    Raises:
        LiveGateError: API 疎通確認に失敗した場合
    """
    from cryptbot.utils.logger import sanitize_error

    try:
        assets = await exchange.get_assets()
        logger.info("API permissions verified: %d assets returned", len(assets))
        return assets
    except Exception as e:
        raise LiveGateError(
            f"bitbank API 疎通確認に失敗しました: {sanitize_error(e)}"
        ) from e
