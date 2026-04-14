"""DataFetcher クラスのテスト。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from cryptbot.data.fetcher import DataFetcher
from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import Candle, ExchangeError
from cryptbot.utils.time_utils import JST


# ------------------------------------------------------------------ #
# ヘルパー
# ------------------------------------------------------------------ #

def _make_candles(year: int, count: int = 3) -> list[Candle]:
    """テスト用 Candle リストを生成する。"""
    return [
        Candle(
            timestamp=datetime(year, 1, i + 1, 0, 0, 0, tzinfo=JST),
            open=float(1000 + i * 10),
            high=float(1010 + i * 10),
            low=float(990 + i * 10),
            close=float(1005 + i * 10),
            volume=float(1.0 + i * 0.1),
        )
        for i in range(count)
    ]


# ------------------------------------------------------------------ #
# フィクスチャ
# ------------------------------------------------------------------ #

@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    """テスト用 Storage インスタンス（一時ディレクトリ使用）。"""
    s = Storage(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
    )
    s.initialize()
    return s


@pytest.fixture()
def mock_exchange() -> MagicMock:
    """BaseExchange のモック。"""
    exchange = MagicMock()
    exchange.name = "mock_exchange"
    exchange.get_candlesticks = AsyncMock()
    return exchange


@pytest.fixture()
def fetcher(mock_exchange: MagicMock, storage: Storage) -> DataFetcher:
    """DataFetcher インスタンス。"""
    return DataFetcher(exchange=mock_exchange, storage=storage)


# ------------------------------------------------------------------ #
# candles_to_dataframe テスト
# ------------------------------------------------------------------ #

class TestCandlesToDataframe:
    def test_returns_correct_columns(self) -> None:
        candles = _make_candles(2024, count=2)
        df = DataFetcher.candles_to_dataframe(candles)
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_dtypes(self) -> None:
        candles = _make_candles(2024, count=2)
        df = DataFetcher.candles_to_dataframe(candles)
        assert isinstance(df["timestamp"].dtype, pd.DatetimeTZDtype)
        assert str(df["timestamp"].dtype.tz) == "Asia/Tokyo"
        assert df["open"].dtype == "float64"
        assert df["high"].dtype == "float64"
        assert df["low"].dtype == "float64"
        assert df["close"].dtype == "float64"
        assert df["volume"].dtype == "float64"

    def test_timestamp_is_jst(self) -> None:
        candles = _make_candles(2024, count=2)
        df = DataFetcher.candles_to_dataframe(candles)
        assert df["timestamp"].dt.tz is not None
        # Asia/Tokyo であること
        tz_str = str(df["timestamp"].dt.tz)
        assert "Asia/Tokyo" in tz_str

    def test_sorted_by_timestamp(self) -> None:
        # 逆順に並んだ Candle を渡してもソートされること
        candles = list(reversed(_make_candles(2024, count=3)))
        df = DataFetcher.candles_to_dataframe(candles)
        timestamps = df["timestamp"].tolist()
        assert timestamps == sorted(timestamps)

    def test_values(self) -> None:
        candles = _make_candles(2024, count=1)
        df = DataFetcher.candles_to_dataframe(candles)
        assert df.iloc[0]["open"] == pytest.approx(1000.0)
        assert df.iloc[0]["high"] == pytest.approx(1010.0)
        assert df.iloc[0]["low"] == pytest.approx(990.0)
        assert df.iloc[0]["close"] == pytest.approx(1005.0)
        assert df.iloc[0]["volume"] == pytest.approx(1.0)

    def test_empty_candles_returns_empty_dataframe(self) -> None:
        df = DataFetcher.candles_to_dataframe([])
        assert df.empty
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_row_count_matches(self) -> None:
        candles = _make_candles(2024, count=5)
        df = DataFetcher.candles_to_dataframe(candles)
        assert len(df) == 5


# ------------------------------------------------------------------ #
# fetch_and_store テスト
# ------------------------------------------------------------------ #

class TestFetchAndStore:
    @pytest.mark.asyncio
    async def test_new_data_is_saved(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
        storage: Storage,
    ) -> None:
        """正常系: 新規データが保存され、ローソク足数が返される。"""
        candles = _make_candles(2024, count=3)
        mock_exchange.get_candlesticks.return_value = candles

        result = await fetcher.fetch_and_store("btc_jpy", "1hour", [2024])

        assert result == {2024: 3}
        mock_exchange.get_candlesticks.assert_awaited_once_with("btc_jpy", "1hour", 2024)
        assert storage.has_ohlcv("btc_jpy", "1hour", 2024)

    @pytest.mark.asyncio
    async def test_existing_data_is_skipped(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
        storage: Storage,
    ) -> None:
        """既存データがある場合はスキップされ、戻り値に含まれない。"""
        # 先にデータを保存
        candles = _make_candles(2024, count=3)
        df = DataFetcher.candles_to_dataframe(candles)
        storage.save_ohlcv(df, "btc_jpy", "1hour", 2024)

        result = await fetcher.fetch_and_store("btc_jpy", "1hour", [2024])

        assert result == {}
        mock_exchange.get_candlesticks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_overwrite_replaces_existing_data(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
        storage: Storage,
    ) -> None:
        """overwrite=True の場合、既存データを上書きする。"""
        # 先にデータを保存
        old_candles = _make_candles(2024, count=2)
        old_df = DataFetcher.candles_to_dataframe(old_candles)
        storage.save_ohlcv(old_df, "btc_jpy", "1hour", 2024)

        # 新しいデータで上書き
        new_candles = _make_candles(2024, count=5)
        mock_exchange.get_candlesticks.return_value = new_candles

        result = await fetcher.fetch_and_store(
            "btc_jpy", "1hour", [2024], overwrite=True
        )

        assert result == {2024: 5}
        mock_exchange.get_candlesticks.assert_awaited_once_with("btc_jpy", "1hour", 2024)

    @pytest.mark.asyncio
    async def test_multiple_years(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
    ) -> None:
        """複数年を一度に取得できる。"""
        mock_exchange.get_candlesticks.side_effect = [
            _make_candles(2022, count=2),
            _make_candles(2023, count=3),
            _make_candles(2024, count=4),
        ]

        result = await fetcher.fetch_and_store("btc_jpy", "1hour", [2022, 2023, 2024])

        assert result == {2022: 2, 2023: 3, 2024: 4}
        assert mock_exchange.get_candlesticks.await_count == 3

    @pytest.mark.asyncio
    async def test_exchange_error_propagates(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
    ) -> None:
        """ExchangeError は呼び出し元に伝播する。"""
        mock_exchange.get_candlesticks.side_effect = ExchangeError(
            "API error", status_code=503
        )

        with pytest.raises(ExchangeError):
            await fetcher.fetch_and_store("btc_jpy", "1hour", [2024])

    @pytest.mark.asyncio
    async def test_partial_skip_mixed_years(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
        storage: Storage,
    ) -> None:
        """一部の年が既存データ、残りが新規の場合、新規分のみ保存される。"""
        # 2022 は既存
        existing_df = DataFetcher.candles_to_dataframe(_make_candles(2022, count=2))
        storage.save_ohlcv(existing_df, "btc_jpy", "1hour", 2022)

        # 2023 は新規
        mock_exchange.get_candlesticks.return_value = _make_candles(2023, count=4)

        result = await fetcher.fetch_and_store("btc_jpy", "1hour", [2022, 2023])

        assert result == {2023: 4}
        # 2022 はスキップ（API 呼び出し 1 回のみ）
        mock_exchange.get_candlesticks.assert_awaited_once_with("btc_jpy", "1hour", 2023)


# ------------------------------------------------------------------ #
# fetch_range テスト
# ------------------------------------------------------------------ #

class TestFetchRange:
    @pytest.mark.asyncio
    async def test_range_calls_correct_years(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
    ) -> None:
        """start_year から end_year（含む）の年が全て取得される。"""
        mock_exchange.get_candlesticks.side_effect = [
            _make_candles(2022, count=1),
            _make_candles(2023, count=1),
            _make_candles(2024, count=1),
        ]

        result = await fetcher.fetch_range("btc_jpy", "1hour", 2022, 2024)

        assert set(result.keys()) == {2022, 2023, 2024}
        assert mock_exchange.get_candlesticks.await_count == 3

    @pytest.mark.asyncio
    async def test_single_year_range(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
    ) -> None:
        """start_year == end_year の場合、1 年分のみ取得される。"""
        mock_exchange.get_candlesticks.return_value = _make_candles(2024, count=2)

        result = await fetcher.fetch_range("btc_jpy", "1hour", 2024, 2024)

        assert result == {2024: 2}
        mock_exchange.get_candlesticks.assert_awaited_once_with("btc_jpy", "1hour", 2024)

    @pytest.mark.asyncio
    async def test_range_respects_overwrite_flag(
        self,
        fetcher: DataFetcher,
        mock_exchange: MagicMock,
        storage: Storage,
    ) -> None:
        """overwrite=True が fetch_and_store に正しく渡される。"""
        # 既存データを保存
        existing_df = DataFetcher.candles_to_dataframe(_make_candles(2024, count=1))
        storage.save_ohlcv(existing_df, "btc_jpy", "1hour", 2024)

        mock_exchange.get_candlesticks.return_value = _make_candles(2024, count=3)

        result = await fetcher.fetch_range(
            "btc_jpy", "1hour", 2024, 2024, overwrite=True
        )

        assert result == {2024: 3}
        mock_exchange.get_candlesticks.assert_awaited_once()
