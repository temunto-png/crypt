"""Live Executor — Phase 4 実装用スケルトン。

警告: このモジュールは直接 import しない。
TRADING_MODE=live 環境変数と --confirm-live CLI フラグの両方が揃った場合のみ有効化する。

## 実装すべき要件（live_trade_design.md 参照）
- PARTIAL_FILLED 完全ライフサイクル
    - 30分ポーリングで残量約定を監視
    - タイムアウト後に残量キャンセル
    - Kill Switch 発動時はキャンセル優先 → 部分約定分を成行クローズ
- 二重発注防止
    - BEGIN EXCLUSIVE トランザクション内でアクティブ注文確認 + CREATED 挿入（TOC/TOU 防止）
    - orders テーブルの UNIQUE 制約で DB レベルの重複拒否
- エラーの sanitize
    - str(e) ではなく sanitize_error(e) でエラー記録（APIキー漏洩防止）
- API キー管理
    - settings.bitbank_api_key.get_secret_value() で取り出す（SecretStr）
    - ログに残高・APIキーを直接出力しない
"""
from __future__ import annotations

from cryptbot.exchanges.base import BaseExchange, OrderBook
from cryptbot.execution.base import BaseExecutor, ExecutionResult


class LiveExecutor(BaseExecutor):
    """Live 注文実行エグゼキューター（Phase 4 スケルトン）。

    このクラスは直接インスタンス化しない。
    phase_4_gate() を経由してのみ利用可能にする（未実装）。
    """

    def __init__(self, exchange: BaseExchange, storage: object) -> None:
        self._exchange = exchange
        self._storage = storage

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
        order_book: OrderBook | None = None,
    ) -> ExecutionResult:
        """Live 注文発行（Phase 4 で実装）。

        Raises:
            NotImplementedError: 常に。Phase 4 実装まで使用不可。
        """
        raise NotImplementedError(
            "LiveExecutor.place_order は未実装です。"
            " Phase 4（live trading）の live 移行条件を満たしてから実装します。"
            " live_trade_design.md を参照してください。"
        )

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        """注文キャンセル（Phase 4 で実装）。

        Raises:
            NotImplementedError: 常に。
        """
        raise NotImplementedError(
            "LiveExecutor.cancel_order は未実装です。"
        )

    async def get_open_orders(self, pair: str) -> list[dict]:
        """未約定注文一覧取得（Phase 4 で実装）。

        Raises:
            NotImplementedError: 常に。
        """
        raise NotImplementedError(
            "LiveExecutor.get_open_orders は未実装です。"
        )
