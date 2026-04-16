"""OhlcvUpdater クラスのテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cryptbot.data.ohlcv_updater import OhlcvUpdater
from cryptbot.exchanges.base import ExchangeError


@pytest.fixture()
def mock_fetcher() -> MagicMock:
    f = MagicMock()
    f.fetch_and_store = AsyncMock(return_value={})
    return f


class TestBackfill:
    @pytest.mark.asyncio
    async def test_backfill_fetches_missing_years(self, mock_fetcher: MagicMock) -> None:
        """不足年を fetch_and_store で取得する。"""
        mock_fetcher.fetch_and_store = AsyncMock(return_value={2025: 100, 2026: 50})

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=2,
            )
            total = await updater.backfill()

        mock_fetcher.fetch_and_store.assert_called_once_with(
            "btc_jpy", "1hour", [2025, 2026], overwrite=False
        )
        assert total == 150

    @pytest.mark.asyncio
    async def test_backfill_skips_existing_years(self, mock_fetcher: MagicMock) -> None:
        """fetch_and_store が空の dict を返す（既存データはスキップ）場合、total=0。"""
        mock_fetcher.fetch_and_store = AsyncMock(return_value={})

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=2,
            )
            total = await updater.backfill()

        assert total == 0

    @pytest.mark.asyncio
    async def test_backfill_propagates_api_error(self, mock_fetcher: MagicMock) -> None:
        """API エラーは例外をそのまま伝播する（fail-closed）。"""
        mock_fetcher.fetch_and_store = AsyncMock(side_effect=ExchangeError("API error"))

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=2,
            )
            with pytest.raises(ExchangeError):
                await updater.backfill()

    @pytest.mark.asyncio
    async def test_backfill_returns_total_candles(self, mock_fetcher: MagicMock) -> None:
        """複数年のローソク足数の合計を返す。"""
        mock_fetcher.fetch_and_store = AsyncMock(return_value={2024: 8760, 2025: 8760, 2026: 100})

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=3,
            )
            total = await updater.backfill()

        assert total == 8760 + 8760 + 100
        mock_fetcher.fetch_and_store.assert_called_once_with(
            "btc_jpy", "1hour", [2024, 2025, 2026], overwrite=False
        )


class TestUpdateLatest:
    @pytest.mark.asyncio
    async def test_update_latest_overwrites_current_year(
        self, mock_fetcher: MagicMock
    ) -> None:
        """当年を overwrite=True で fetch_and_store する。"""
        mock_fetcher.fetch_and_store = AsyncMock(return_value={2026: 42})

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=2,
            )
            result = await updater.update_latest()

        assert result is True
        mock_fetcher.fetch_and_store.assert_called_once_with(
            "btc_jpy", "1hour", [2026], overwrite=True
        )

    @pytest.mark.asyncio
    async def test_update_latest_returns_false_on_error(
        self, mock_fetcher: MagicMock
    ) -> None:
        """API エラー時は例外を握りつぶして False を返す（fail-open）。"""
        mock_fetcher.fetch_and_store = AsyncMock(side_effect=ExchangeError("timeout"))

        with patch("cryptbot.data.ohlcv_updater.datetime") as mock_dt:
            mock_dt.now.return_value.year = 2026
            updater = OhlcvUpdater(
                fetcher=mock_fetcher,
                pair="btc_jpy",
                timeframe="1hour",
                backfill_years=2,
            )
            result = await updater.update_latest()

        assert result is False
