"""BitbankExchange のユニットテスト（httpx.AsyncClient をモック）。"""
from __future__ import annotations

import json
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from cryptbot.exchanges.base import ExchangeError
from cryptbot.exchanges.bitbank import BitbankExchange

JST = ZoneInfo("Asia/Tokyo")

# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #


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


def _make_bitbank(mock_get: AsyncMock) -> BitbankExchange:
    """モック済みクライアントを持つ BitbankExchange を返す。"""
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = mock_get
    return BitbankExchange(client=client)


# ------------------------------------------------------------------ #
# Ticker
# ------------------------------------------------------------------ #


class TestGetTicker:
    TICKER_RESPONSE = {
        "success": 1,
        "data": {
            "sell": "14500000",
            "buy": "14499000",
            "high": "14600000",
            "low": "14300000",
            "last": "14500000",
            "vol": "12.5000",
            "timestamp": 1704067200000,
        },
    }

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_get = AsyncMock(return_value=_make_response(self.TICKER_RESPONSE))
        exchange = _make_bitbank(mock_get)

        ticker = await exchange.get_ticker("btc_jpy")

        assert ticker.pair == "btc_jpy"
        assert ticker.sell == 14_500_000.0
        assert ticker.buy == 14_499_000.0
        assert ticker.high == 14_600_000.0
        assert ticker.low == 14_300_000.0
        assert ticker.last == 14_500_000.0
        assert ticker.vol == 12.5
        assert ticker.timestamp.tzinfo is not None
        assert str(ticker.timestamp.tzinfo) == "Asia/Tokyo"
        mock_get.assert_called_once_with("https://public.bitbank.cc/btc_jpy/ticker")

    @pytest.mark.asyncio
    async def test_success_not_one_raises_exchange_error(self) -> None:
        body = {"success": 0, "data": {"code": 10000}}
        mock_get = AsyncMock(return_value=_make_response(body))
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")

        assert "success != 1" in str(exc_info.value)


# ------------------------------------------------------------------ #
# OrderBook
# ------------------------------------------------------------------ #


class TestGetOrderBook:
    ORDER_BOOK_RESPONSE = {
        "success": 1,
        "data": {
            "asks": [["14500000", "0.1000"], ["14501000", "0.2000"]],
            "bids": [["14499000", "0.3000"], ["14498000", "0.5000"]],
            "asks_over": "0",
            "bids_under": "0",
            "timestamp": 1704067200000,
        },
    }

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_get = AsyncMock(return_value=_make_response(self.ORDER_BOOK_RESPONSE))
        exchange = _make_bitbank(mock_get)

        ob = await exchange.get_order_book("btc_jpy")

        assert ob.pair == "btc_jpy"
        assert ob.asks == [(14_500_000.0, 0.1), (14_501_000.0, 0.2)]
        assert ob.bids == [(14_499_000.0, 0.3), (14_498_000.0, 0.5)]
        assert ob.timestamp.tzinfo is not None
        mock_get.assert_called_once_with("https://public.bitbank.cc/btc_jpy/depth")


# ------------------------------------------------------------------ #
# Candlesticks
# ------------------------------------------------------------------ #


class TestGetCandlesticks:
    # 長時間足（1day）は YYYY 形式で一括取得
    CANDLE_RESPONSE_1DAY = {
        "success": 1,
        "data": {
            "candlestick": [
                {
                    "type": "1day",
                    "ohlcv": [
                        ["14000000", "14200000", "13900000", "14100000", "5.0000", 1704067200000],
                        ["14100000", "14300000", "14000000", "14200000", "3.0000", 1704153600000],
                    ],
                }
            ],
            "timestamp": 1704153600000,
        },
    }

    @pytest.mark.asyncio
    async def test_success_year_format(self) -> None:
        """長時間足（1day）は YYYY 形式の単一 API コールで取得できる。"""
        mock_get = AsyncMock(return_value=_make_response(self.CANDLE_RESPONSE_1DAY))
        exchange = _make_bitbank(mock_get)

        candles = await exchange.get_candlesticks("btc_jpy", "1day", 2024)

        assert len(candles) == 2
        c0 = candles[0]
        assert c0.open == 14_000_000.0
        assert c0.high == 14_200_000.0
        assert c0.low == 13_900_000.0
        assert c0.close == 14_100_000.0
        assert c0.volume == 5.0
        assert c0.timestamp.tzinfo is not None
        assert str(c0.timestamp.tzinfo) == "Asia/Tokyo"
        mock_get.assert_called_once_with(
            "https://public.bitbank.cc/btc_jpy/candlestick/1day/2024"
        )

    @pytest.mark.asyncio
    async def test_invalid_timeframe_raises_value_error(self) -> None:
        mock_get = AsyncMock()
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ValueError, match="無効な timeframe"):
            await exchange.get_candlesticks("btc_jpy", "invalid_tf", 2024)

        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_year_format_timeframes_accepted(self) -> None:
        """長時間足（YYYY 形式）は単一コールで取得できる。"""
        year_format_tfs = BitbankExchange.VALID_TIMEFRAMES - BitbankExchange._DAY_FORMAT_TIMEFRAMES
        for tf in year_format_tfs:
            response = {
                "success": 1,
                "data": {
                    "candlestick": [{"type": tf, "ohlcv": []}],
                    "timestamp": 1704067200000,
                },
            }
            mock_get = AsyncMock(return_value=_make_response(response))
            exchange = _make_bitbank(mock_get)
            candles = await exchange.get_candlesticks("btc_jpy", tf, 2024)
            assert isinstance(candles, list)

    @pytest.mark.asyncio
    async def test_valid_day_format_timeframes_accepted(self) -> None:
        """短時間足（YYYYMMDD 形式）は日次コールで取得できる。"""
        with patch("cryptbot.exchanges.bitbank.asyncio.sleep"):
            for tf in BitbankExchange._DAY_FORMAT_TIMEFRAMES:
                response = {
                    "success": 1,
                    "data": {
                        "candlestick": [{"type": tf, "ohlcv": []}],
                        "timestamp": 1704067200000,
                    },
                }
                mock_get = AsyncMock(return_value=_make_response(response))
                exchange = _make_bitbank(mock_get)
                # 過去の完全な 1 ヶ月（2023-01）で日数を制御
                candles = await exchange._get_candlesticks_by_day("btc_jpy", tf, 2023)
                assert isinstance(candles, list)
                # 2023 は過去の年なので 365 日分のリクエストが発生する
                assert mock_get.call_count == 365

    @pytest.mark.asyncio
    async def test_timeframe_mismatch_raises_exchange_error(self) -> None:
        """APIレスポンスの type フィールドが要求した timeframe と不一致の場合に ExchangeError を raise。"""
        response = {
            "success": 1,
            "data": {
                "candlestick": [
                    {
                        "type": "1week",  # 要求した 1day と不一致
                        "ohlcv": [
                            ["14000000", "14200000", "13900000", "14100000", "5.0000", 1704067200000],
                        ],
                    }
                ],
                "timestamp": 1704153600000,
            },
        }
        mock_get = AsyncMock(return_value=_make_response(response))
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_candlesticks("btc_jpy", "1day", 2024)

        assert "1day" in str(exc_info.value)
        assert "見つかりませんでした" in str(exc_info.value)


class TestGetCandlesticksDayFormat:
    """短時間足（YYYYMMDD 形式）の get_candlesticks テスト。"""

    CANDLE_RESPONSE_1HOUR = {
        "success": 1,
        "data": {
            "candlestick": [
                {
                    "type": "1hour",
                    "ohlcv": [
                        ["14000000", "14200000", "13900000", "14100000", "5.0000", 1704067200000],
                        ["14100000", "14300000", "14000000", "14200000", "3.0000", 1704070800000],
                    ],
                }
            ],
            "timestamp": 1704153600000,
        },
    }

    @pytest.mark.asyncio
    async def test_day_format_calls_per_day(self) -> None:
        """1hour は YYYYMMDD 形式で各日に 1 回 API を呼び出す。"""
        with patch("cryptbot.exchanges.bitbank.asyncio.sleep"):
            mock_get = AsyncMock(return_value=_make_response(self.CANDLE_RESPONSE_1HOUR))
            exchange = _make_bitbank(mock_get)

            # 2023 は過去の完全な年（365 日）
            candles = await exchange.get_candlesticks("btc_jpy", "1hour", 2023)

        # 365 日 × 2 本 = 730 本
        assert len(candles) == 365 * 2
        assert mock_get.call_count == 365
        # 最初のコールが 20230101 形式であることを確認
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "20230101" in first_call_url
        last_call_url = mock_get.call_args_list[-1][0][0]
        assert "20231231" in last_call_url

    @pytest.mark.asyncio
    async def test_current_year_stops_at_yesterday(self) -> None:
        """当年の場合は昨日まで取得する（当日分は update_latest で更新）。"""
        from datetime import date, timedelta
        with patch("cryptbot.exchanges.bitbank.asyncio.sleep"):
            mock_get = AsyncMock(return_value=_make_response(self.CANDLE_RESPONSE_1HOUR))
            exchange = _make_bitbank(mock_get)

            current_year = date.today().year
            candles = await exchange.get_candlesticks("btc_jpy", "1hour", current_year)

        # 昨日まで = 今年の 1/1 から昨日まで
        yesterday = date.today() - timedelta(days=1)
        expected_days = (yesterday - date(current_year, 1, 1)).days + 1
        assert mock_get.call_count == expected_days
        assert len(candles) == expected_days * 2

    @pytest.mark.asyncio
    async def test_future_year_returns_empty(self) -> None:
        """来年以降はデータなし（start > end）で空リストを返す。"""
        from datetime import date
        future_year = date.today().year + 1
        mock_get = AsyncMock()
        exchange = _make_bitbank(mock_get)

        candles = await exchange.get_candlesticks("btc_jpy", "1hour", future_year)

        assert candles == []
        mock_get.assert_not_called()


# ------------------------------------------------------------------ #
# HTTP エラーハンドリング
# ------------------------------------------------------------------ #


class TestHttpErrorHandling:
    @pytest.mark.asyncio
    async def test_http_429_raises_exchange_error(self) -> None:
        mock_get = AsyncMock(return_value=_make_http_error_response(429))
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")

        err = exc_info.value
        assert err.status_code == 429
        assert "429" in err.message

    @pytest.mark.asyncio
    async def test_http_500_raises_exchange_error(self) -> None:
        mock_get = AsyncMock(return_value=_make_http_error_response(500))
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_order_book("btc_jpy")

        err = exc_info.value
        assert err.status_code == 500
        assert "500" in err.message

    @pytest.mark.asyncio
    async def test_timeout_raises_exchange_error(self) -> None:
        mock_get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        exchange = _make_bitbank(mock_get)

        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")

        err = exc_info.value
        assert err.status_code is None
        assert "タイムアウト" in err.message


# ------------------------------------------------------------------ #
# P2-02: retry/backoff テスト
# ------------------------------------------------------------------ #


class TestRetryBehavior:
    """tenacity retry: 429/5xx/timeout で再試行し、成功したら結果を返す。"""

    TICKER_RESPONSE = {
        "success": 1,
        "data": {
            "sell": "14500000", "buy": "14499000",
            "high": "14600000", "low": "14300000",
            "last": "14500000", "vol": "12.5000",
            "timestamp": 1704067200000,
        },
    }

    @pytest.mark.asyncio
    async def test_retry_429_then_success(self) -> None:
        """429 → 成功の順でレスポンスが来た場合、最終的に成功すること。"""
        mock_get = AsyncMock(side_effect=[
            _make_http_error_response(429),
            _make_response(self.TICKER_RESPONSE),
        ])
        exchange = _make_bitbank(mock_get)
        ticker = await exchange.get_ticker("btc_jpy")
        assert ticker.sell == 14_500_000.0
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_500_then_success(self) -> None:
        """500 → 成功の順でレスポンスが来た場合、最終的に成功すること。"""
        mock_get = AsyncMock(side_effect=[
            _make_http_error_response(500),
            _make_response(self.TICKER_RESPONSE),
        ])
        exchange = _make_bitbank(mock_get)
        ticker = await exchange.get_ticker("btc_jpy")
        assert ticker.sell == 14_500_000.0
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_timeout_then_success(self) -> None:
        """タイムアウト → 成功の順でレスポンスが来た場合、最終的に成功すること。"""
        mock_get = AsyncMock(side_effect=[
            httpx.TimeoutException("timeout"),
            _make_response(self.TICKER_RESPONSE),
        ])
        exchange = _make_bitbank(mock_get)
        ticker = await exchange.get_ticker("btc_jpy")
        assert ticker.sell == 14_500_000.0
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self) -> None:
        """3 回連続 500 → ExchangeError が最終的に送出されること。"""
        mock_get = AsyncMock(return_value=_make_http_error_response(500))
        exchange = _make_bitbank(mock_get)
        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")
        assert mock_get.call_count == 3  # 最大 3 試行
        assert "500" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_400_not_retried(self) -> None:
        """400（非リトライ対象）は即時 ExchangeError を送出し、リトライしないこと。"""
        mock_get = AsyncMock(return_value=_make_http_error_response(400))
        exchange = _make_bitbank(mock_get)
        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")
        assert mock_get.call_count == 1  # リトライなし
        assert "400" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_raw_response_sanitized(self) -> None:
        """raw_response に API キー相当の長い英数字列が含まれても [REDACTED] になること。"""
        long_key = "A" * 64
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500
        error_response.text = f"api_key={long_key}"
        http_error = httpx.HTTPStatusError(
            "HTTP 500", request=MagicMock(), response=error_response
        )
        error_response.raise_for_status = MagicMock(side_effect=http_error)
        mock_get = AsyncMock(return_value=error_response)

        exchange = _make_bitbank(mock_get)
        with pytest.raises(ExchangeError) as exc_info:
            await exchange.get_ticker("btc_jpy")
        # raw_response にキーが残っていないこと
        assert long_key not in (exc_info.value.raw_response or "")


# ------------------------------------------------------------------ #
# Private API（Phase 2）
# ------------------------------------------------------------------ #


class TestPrivateApiNotImplemented:
    @pytest.mark.asyncio
    async def test_get_assets_raises(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(NotImplementedError):
            await exchange.get_assets()

    @pytest.mark.asyncio
    async def test_place_order_raises(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(NotImplementedError):
            await exchange.place_order("btc_jpy", "buy", "market", 0.001)

    @pytest.mark.asyncio
    async def test_cancel_order_raises(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(NotImplementedError):
            await exchange.cancel_order("btc_jpy", "12345")

    @pytest.mark.asyncio
    async def test_get_open_orders_raises(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        with pytest.raises(NotImplementedError):
            await exchange.get_open_orders("btc_jpy")


# ------------------------------------------------------------------ #
# プロパティ
# ------------------------------------------------------------------ #


class TestBitbankProperties:
    def test_name(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        assert exchange.name == "bitbank"

    def test_supported_pairs(self) -> None:
        exchange = BitbankExchange(client=MagicMock(spec=httpx.AsyncClient))
        assert exchange.supported_pairs == ["btc_jpy"]
