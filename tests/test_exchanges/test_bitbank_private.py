"""BitbankPrivateExchange のユニットテスト（httpx.AsyncClient をモック）。"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cryptbot.exchanges.base import ExchangeError
from cryptbot.exchanges.bitbank_private import BitbankPrivateExchange

# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

TEST_API_KEY = "test_api_key_0123456789"
TEST_API_SECRET = "test_api_secret_0123456789abcdef"


def _make_response(body: dict, status_code: int = 200) -> MagicMock:
    """httpx.Response 相当のモックを作成する。"""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.json.return_value = body
    mock.text = json.dumps(body)
    mock.raise_for_status = MagicMock()
    return mock


def _make_http_error_response(status_code: int) -> MagicMock:
    """raise_for_status が HTTPStatusError を raise するモックを作成する。"""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.text = f"HTTP {status_code}"
    error = httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=MagicMock(),
        response=mock,
    )
    mock.raise_for_status = MagicMock(side_effect=error)
    return mock


def _make_private_exchange(
    mock_get: AsyncMock | None = None,
    mock_post: AsyncMock | None = None,
) -> BitbankPrivateExchange:
    """モック済みクライアントを持つ BitbankPrivateExchange を返す。"""
    client = MagicMock(spec=httpx.AsyncClient)
    if mock_get is not None:
        client.get = mock_get
    if mock_post is not None:
        client.post = mock_post
    return BitbankPrivateExchange(
        api_key=TEST_API_KEY,
        api_secret=TEST_API_SECRET,
        client=client,
    )


# ------------------------------------------------------------------ #
# 認証ヘッダーテスト
# ------------------------------------------------------------------ #


class TestAuthHeaders:
    def test_auth_headers_contain_required_keys(self) -> None:
        """ACCESS-KEY, ACCESS-NONCE, ACCESS-SIGNATURE が含まれること。"""
        exchange = _make_private_exchange()
        headers = exchange._auth_headers("/user/assets")
        assert "ACCESS-KEY" in headers
        assert "ACCESS-NONCE" in headers
        assert "ACCESS-SIGNATURE" in headers
        assert headers["ACCESS-KEY"] == TEST_API_KEY

    def test_sign_produces_correct_hmac(self) -> None:
        """既知の key + nonce + path + body から正しい HMAC が生成されること。"""
        exchange = _make_private_exchange()
        nonce = "1700000000000"
        path = "/user/assets"
        body = ""
        message = nonce + path + body
        expected = hmac.new(
            TEST_API_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert exchange._sign(nonce, path, body) == expected

    def test_sign_with_body(self) -> None:
        """POST の body を含む場合の HMAC 署名が正しいこと。"""
        exchange = _make_private_exchange()
        nonce = "1700000000000"
        path = "/user/spot/order"
        body = '{"pair":"btc_jpy","side":"buy","type":"market","amount":"0.001"}'
        message = nonce + path + body
        expected = hmac.new(
            TEST_API_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert exchange._sign(nonce, path, body) == expected

    def test_nonce_is_monotonically_increasing(self) -> None:
        """同一インスタンスで2回呼び出すと nonce が単調増加すること。"""
        exchange = _make_private_exchange()
        n1 = int(exchange._nonce())
        n2 = int(exchange._nonce())
        assert n2 > n1


# ------------------------------------------------------------------ #
# place_order テスト
# ------------------------------------------------------------------ #


class TestPlaceOrder:
    ORDER_RESPONSE = {
        "success": 1,
        "data": {
            "order_id": 12345,
            "pair": "btc_jpy",
            "side": "buy",
            "type": "market",
            "amount": "0.001",
            "status": "UNFILLED",
        },
    }

    @pytest.mark.asyncio
    async def test_place_order_sends_correct_body(self) -> None:
        """market 注文: body に pair/side/type/amount が含まれること。"""
        captured_body: list[str] = []

        async def mock_post(url: str, content: str | bytes, headers: dict) -> MagicMock:
            captured_body.append(content if isinstance(content, str) else content.decode())
            return _make_response(self.ORDER_RESPONSE)

        client = MagicMock(spec=httpx.AsyncClient)
        client.post = mock_post
        exchange = BitbankPrivateExchange(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            client=client,
        )

        result = await exchange.place_order("btc_jpy", "buy", "market", 0.001)

        assert result["order_id"] == 12345
        assert len(captured_body) == 1
        parsed = json.loads(captured_body[0])
        assert parsed["pair"] == "btc_jpy"
        assert parsed["side"] == "buy"
        assert parsed["type"] == "market"
        assert parsed["amount"] == "0.001"
        assert "price" not in parsed

    @pytest.mark.asyncio
    async def test_place_order_with_limit_price(self) -> None:
        """limit 注文: price が整数文字列として body に含まれること。"""
        captured_body: list[str] = []

        async def mock_post(url: str, content: str | bytes, headers: dict) -> MagicMock:
            captured_body.append(content if isinstance(content, str) else content.decode())
            return _make_response({
                "success": 1,
                "data": {
                    "order_id": 99999,
                    "pair": "btc_jpy",
                    "side": "buy",
                    "type": "limit",
                    "amount": "0.001",
                    "price": "14500000",
                    "status": "UNFILLED",
                },
            })

        client = MagicMock(spec=httpx.AsyncClient)
        client.post = mock_post
        exchange = BitbankPrivateExchange(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            client=client,
        )

        result = await exchange.place_order("btc_jpy", "buy", "limit", 0.001, price=14500000.5)

        assert result["order_id"] == 99999
        parsed = json.loads(captured_body[0])
        assert parsed["price"] == "14500000"  # int に丸めて文字列


# ------------------------------------------------------------------ #
# cancel_order テスト
# ------------------------------------------------------------------ #


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_returns_true_on_canceled(self) -> None:
        """CANCELED_UNFILLED ステータスのとき True を返すこと。"""
        mock_post = AsyncMock(return_value=_make_response({
            "success": 1,
            "data": {
                "order_id": 12345,
                "status": "CANCELED_UNFILLED",
            },
        }))
        exchange = _make_private_exchange(mock_post=mock_post)
        result = await exchange.cancel_order("btc_jpy", "12345")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_returns_false_on_other_status(self) -> None:
        """FULLY_FILLED ステータス（約定済み）のとき False を返すこと。"""
        mock_post = AsyncMock(return_value=_make_response({
            "success": 1,
            "data": {
                "order_id": 12345,
                "status": "FULLY_FILLED",
            },
        }))
        exchange = _make_private_exchange(mock_post=mock_post)
        result = await exchange.cancel_order("btc_jpy", "12345")
        assert result is False


# ------------------------------------------------------------------ #
# get_assets テスト
# ------------------------------------------------------------------ #


class TestGetAssets:
    ASSETS_RESPONSE = {
        "success": 1,
        "data": {
            "assets": [
                {"asset": "jpy", "free_amount": "100000.0"},
                {"asset": "btc", "free_amount": "0.00123456"},
                {"asset": "xrp", "free_amount": "500.5"},
            ]
        },
    }

    @pytest.mark.asyncio
    async def test_get_assets_parses_response(self) -> None:
        """assets 配列を {"asset_name": float} 形式に変換すること。"""
        mock_get = AsyncMock(return_value=_make_response(self.ASSETS_RESPONSE))
        exchange = _make_private_exchange(mock_get=mock_get)
        result = await exchange.get_assets()

        assert result["jpy"] == pytest.approx(100000.0)
        assert result["btc"] == pytest.approx(0.00123456)
        assert result["xrp"] == pytest.approx(500.5)


# ------------------------------------------------------------------ #
# get_order テスト
# ------------------------------------------------------------------ #


class TestGetOrder:
    ORDER_RESPONSE = {
        "success": 1,
        "data": {
            "order_id": 12345,
            "pair": "btc_jpy",
            "side": "buy",
            "type": "limit",
            "amount": "0.001",
            "price": "14500000",
            "status": "PARTIALLY_FILLED",
        },
    }

    @pytest.mark.asyncio
    async def test_get_order_returns_data(self) -> None:
        """order_id を含む注文 dict が返ること。"""
        mock_get = AsyncMock(return_value=_make_response(self.ORDER_RESPONSE))
        exchange = _make_private_exchange(mock_get=mock_get)
        result = await exchange.get_order("btc_jpy", "12345")
        assert result["order_id"] == 12345
        assert result["status"] == "PARTIALLY_FILLED"


# ------------------------------------------------------------------ #
# get_open_orders テスト
# ------------------------------------------------------------------ #


class TestGetOpenOrders:
    OPEN_ORDERS_RESPONSE = {
        "success": 1,
        "data": {
            "orders": [
                {"order_id": 1, "pair": "btc_jpy", "status": "UNFILLED"},
                {"order_id": 2, "pair": "btc_jpy", "status": "PARTIALLY_FILLED"},
            ]
        },
    }

    @pytest.mark.asyncio
    async def test_get_open_orders_returns_list(self) -> None:
        """未決済注文のリストが返ること。"""
        mock_get = AsyncMock(return_value=_make_response(self.OPEN_ORDERS_RESPONSE))
        exchange = _make_private_exchange(mock_get=mock_get)
        result = await exchange.get_open_orders("btc_jpy")
        assert len(result) == 2
        assert result[0]["order_id"] == 1
        assert result[1]["order_id"] == 2

    @pytest.mark.asyncio
    async def test_get_open_orders_returns_empty_list(self) -> None:
        """orders キーがない場合は空リストを返すこと。"""
        mock_get = AsyncMock(return_value=_make_response({
            "success": 1,
            "data": {},
        }))
        exchange = _make_private_exchange(mock_get=mock_get)
        result = await exchange.get_open_orders("btc_jpy")
        assert result == []


# ------------------------------------------------------------------ #
# retry テスト
# ------------------------------------------------------------------ #


class TestRetryBehavior:
    ASSETS_RESPONSE = {
        "success": 1,
        "data": {
            "assets": [
                {"asset": "jpy", "free_amount": "100000.0"},
            ]
        },
    }

    @pytest.mark.asyncio
    async def test_retry_on_429(self) -> None:
        """GET private: 429 が2回続いた後に成功すること。"""
        mock_get = AsyncMock(side_effect=[
            _make_http_error_response(429),
            _make_http_error_response(429),
            _make_response(self.ASSETS_RESPONSE),
        ])
        exchange = _make_private_exchange(mock_get=mock_get)
        result = await exchange.get_assets()
        assert result["jpy"] == pytest.approx(100000.0)
        assert mock_get.call_count == 3

    @pytest.mark.asyncio
    async def test_post_retry_on_429(self) -> None:
        """POST: 429 が1回続いた後に成功すること。"""
        mock_post = AsyncMock(side_effect=[
            _make_http_error_response(429),
            _make_response({
                "success": 1,
                "data": {
                    "order_id": 1,
                    "pair": "btc_jpy",
                    "side": "buy",
                    "type": "market",
                    "amount": "0.001",
                    "status": "UNFILLED",
                },
            }),
        ])
        exchange = _make_private_exchange(mock_post=mock_post)
        result = await exchange.place_order("btc_jpy", "buy", "market", 0.001)
        assert result["order_id"] == 1
        assert mock_post.call_count == 2


# ------------------------------------------------------------------ #
# エラーサニタイズテスト
# ------------------------------------------------------------------ #


class TestErrorSanitization:
    @pytest.mark.asyncio
    async def test_post_sanitizes_error_on_failure(self) -> None:
        """エラーメッセージに API キーが含まれないこと（sanitize_error が機能すること）。"""
        long_key = "A" * 64  # API キーパターン（英数字20文字以上）
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500
        error_response.text = f"api_key={long_key}"
        http_error = httpx.HTTPStatusError(
            "HTTP 500", request=MagicMock(), response=error_response
        )
        error_response.raise_for_status = MagicMock(side_effect=http_error)
        mock_post = AsyncMock(return_value=error_response)

        exchange = _make_private_exchange(mock_post=mock_post)
        with pytest.raises(ExchangeError) as exc_info:
            await exchange.cancel_order("btc_jpy", "12345")

        # raw_response にキーが残っていないこと
        assert long_key not in (exc_info.value.raw_response or "")
