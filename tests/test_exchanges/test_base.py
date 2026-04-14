"""BaseExchange / GmoCoinExchange のテスト。"""
from __future__ import annotations

import pytest

from cryptbot.exchanges.gmo import GmoCoinExchange


@pytest.fixture
def gmo() -> GmoCoinExchange:
    return GmoCoinExchange()


class TestGmoCoinExchangeProperties:
    def test_name(self, gmo: GmoCoinExchange) -> None:
        assert gmo.name == "gmo_coin"

    def test_supported_pairs(self, gmo: GmoCoinExchange) -> None:
        assert gmo.supported_pairs == ["btc_jpy"]


class TestGmoCoinExchangePublicApi:
    @pytest.mark.asyncio
    async def test_get_ticker_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.get_ticker("btc_jpy")

    @pytest.mark.asyncio
    async def test_get_order_book_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.get_order_book("btc_jpy")

    @pytest.mark.asyncio
    async def test_get_candlesticks_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.get_candlesticks("btc_jpy", "1hour", 2024)


class TestGmoCoinExchangePrivateApi:
    @pytest.mark.asyncio
    async def test_get_assets_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.get_assets()

    @pytest.mark.asyncio
    async def test_place_order_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.place_order("btc_jpy", "buy", "market", 0.001)

    @pytest.mark.asyncio
    async def test_cancel_order_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.cancel_order("btc_jpy", "12345")

    @pytest.mark.asyncio
    async def test_get_open_orders_raises(self, gmo: GmoCoinExchange) -> None:
        with pytest.raises(NotImplementedError):
            await gmo.get_open_orders("btc_jpy")
