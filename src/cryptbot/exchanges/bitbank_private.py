"""bitbank Private API アダプター（認証付き）。

Public API（BitbankExchange）を継承し、HMAC-SHA256 認証を追加する。
Phase 4 Live Trade で使用する Private API（注文・資産・キャンセル）を実装する。

警告: APIキーはログに出力しない。sanitize_error() を必ず通す。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from cryptbot.exchanges.base import ExchangeError
from cryptbot.exchanges.bitbank import BitbankExchange, _RetryableExchangeError, _RETRYABLE_STATUS
from cryptbot.utils.logger import sanitize_error


class BitbankPrivateExchange(BitbankExchange):
    """bitbank Private API アダプター。

    BitbankExchange を継承し、HMAC-SHA256 認証を追加する。
    注文・資産取得・キャンセルなど Private API を実装する。
    """

    PRIVATE_BASE_URL = "https://api.bitbank.cc/v1"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Args:
            api_key: bitbank API キー（settings.bitbank_api_key.get_secret_value()）
            api_secret: bitbank API シークレット（settings.bitbank_api_secret.get_secret_value()）
            client: 外部から注入する httpx.AsyncClient（テスト用）
        """
        super().__init__(client=client)
        self._api_key = api_key
        self._api_secret = api_secret
        self._last_nonce: int = 0  # monotonic nonce 防止

    # ------------------------------------------------------------------ #
    # 認証ヘルパー
    # ------------------------------------------------------------------ #

    def _nonce(self) -> str:
        """単調増加する nonce を生成する（ミリ秒 Unix 時刻ベース）。"""
        n = max(int(time.time() * 1000), self._last_nonce + 1)
        self._last_nonce = n
        return str(n)

    def _sign(self, nonce: str, path: str, body: str = "") -> str:
        """HMAC-SHA256 で署名する。

        bitbank の署名メッセージ = nonce + path + body
        """
        message = nonce + path + body
        return hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, path: str, body: str = "") -> dict[str, str]:
        """認証ヘッダーを生成する。"""
        nonce = self._nonce()
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-NONCE": nonce,
            "ACCESS-SIGNATURE": self._sign(nonce, path, body),
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    # HTTP ヘルパー
    # ------------------------------------------------------------------ #

    async def _post(self, path: str, body: dict) -> dict:
        """POST /v1{path} を認証付きで送信する。

        _get() と同じ tenacity retry（429/5xx/timeout、最大3回）を適用。
        """
        url = f"{self.PRIVATE_BASE_URL}{path}"
        body_json = json.dumps(body, separators=(",", ":"))

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=1, max=10, jitter=1),
                retry=retry_if_exception_type(_RetryableExchangeError),
                reraise=True,
            ):
                with attempt:
                    # POST: nonce と body のみで署名（path は含めない）
                    headers = self._auth_headers("", body_json)
                    try:
                        response = await self._client.post(
                            url,
                            content=body_json,
                            headers=headers,
                        )
                        response.raise_for_status()
                    except httpx.TimeoutException as exc:
                        raise _RetryableExchangeError(
                            f"リクエストタイムアウト: {url}",
                            status_code=None,
                            raw_response=sanitize_error(exc),
                        ) from exc
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status not in _RETRYABLE_STATUS:
                            raise ExchangeError(
                                f"HTTP エラー {status}: {url}",
                                status_code=status,
                                raw_response=sanitize_error(exc),
                            ) from exc
                        raise _RetryableExchangeError(
                            f"HTTP エラー {status}: {url}",
                            status_code=status,
                            raw_response=sanitize_error(exc),
                        ) from exc

                    body_resp = response.json()
                    if body_resp.get("success") != 1:
                        raise ExchangeError(
                            f"API エラー (success != 1): {url}",
                            status_code=response.status_code,
                            raw_response=sanitize_error(Exception(response.text)),
                        )
                    return body_resp  # type: ignore[return-value]

        except RetryError as exc:
            last = exc.last_attempt.exception()
            if isinstance(last, ExchangeError):
                raise last from exc
            raise ExchangeError(
                f"リトライ上限に達しました: {url}",
                status_code=None,
                raw_response=sanitize_error(exc),
            ) from exc

        raise ExchangeError(f"予期しない状態: {url}", status_code=None)  # pragma: no cover

    async def _get_private(self, path: str, params: dict | None = None) -> dict:
        """GET /v1{path} を認証ヘッダー付きで送信する。

        bitbank の署名は path + query string を含む full_path で行う。
        """
        full_path = path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            full_path = f"{path}?{qs}"

        url = f"{self.PRIVATE_BASE_URL}{full_path}"

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=1, max=10, jitter=1),
                retry=retry_if_exception_type(_RetryableExchangeError),
                reraise=True,
            ):
                with attempt:
                    # GET: 毎回新しい nonce を生成してヘッダーを作成
                    headers = self._auth_headers(full_path)
                    try:
                        response = await self._client.get(url, headers=headers)
                        response.raise_for_status()
                    except httpx.TimeoutException as exc:
                        raise _RetryableExchangeError(
                            f"リクエストタイムアウト: {url}",
                            status_code=None,
                            raw_response=sanitize_error(exc),
                        ) from exc
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status not in _RETRYABLE_STATUS:
                            raise ExchangeError(
                                f"HTTP エラー {status}: {url}",
                                status_code=status,
                                raw_response=sanitize_error(exc),
                            ) from exc
                        raise _RetryableExchangeError(
                            f"HTTP エラー {status}: {url}",
                            status_code=status,
                            raw_response=sanitize_error(exc),
                        ) from exc

                    body_resp = response.json()
                    if body_resp.get("success") != 1:
                        raise ExchangeError(
                            f"API エラー (success != 1): {url}",
                            status_code=response.status_code,
                            raw_response=sanitize_error(Exception(response.text)),
                        )
                    return body_resp  # type: ignore[return-value]

        except RetryError as exc:
            last = exc.last_attempt.exception()
            if isinstance(last, ExchangeError):
                raise last from exc
            raise ExchangeError(
                f"リトライ上限に達しました: {url}",
                status_code=None,
                raw_response=sanitize_error(exc),
            ) from exc

        raise ExchangeError(f"予期しない状態: {url}", status_code=None)  # pragma: no cover

    # ------------------------------------------------------------------ #
    # Private API（BitbankExchange の NotImplementedError スタブを上書き）
    # ------------------------------------------------------------------ #

    async def get_assets(self) -> dict[str, float]:
        """GET /v1/user/assets — 残高取得。

        Returns:
            {"btc": 0.001, "jpy": 100000.0, ...} 形式
        """
        data = await self._get_private("/user/assets")
        result = {}
        for asset in data["data"]["assets"]:
            result[asset["asset"]] = float(asset["free_amount"])
        return result

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
    ) -> dict:
        """POST /v1/user/spot/order — 注文発行。

        Returns:
            取引所が返す注文 dict（order_id を含む）
        """
        body: dict = {
            "pair": pair,
            "side": side,
            "type": order_type,
            "amount": str(size),
        }
        if price is not None:
            body["price"] = str(int(price))  # 整数として送信
        data = await self._post("/user/spot/order", body)
        return data["data"]

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        """POST /v1/user/spot/cancel_order — 注文キャンセル。

        Returns:
            True if successfully cancelled (CANCELED_UNFILLED or CANCELED_PARTIALLY_FILLED)

        Raises:
            ExchangeError: API エラー
        """
        data = await self._post("/user/spot/cancel_order", {
            "pair": pair,
            "order_id": int(order_id),
        })
        return data["data"].get("status") in {"CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED"}

    async def get_open_orders(self, pair: str) -> list[dict]:
        """GET /v1/user/spot/active_orders — 未決済注文一覧。"""
        data = await self._get_private("/user/spot/active_orders", {"pair": pair})
        return data["data"].get("orders", [])

    async def get_order(self, pair: str, order_id: str) -> dict:
        """GET /v1/user/spot/order — 注文情報取得。"""
        data = await self._get_private("/user/spot/order", {
            "pair": pair,
            "order_id": order_id,
        })
        return data["data"]
