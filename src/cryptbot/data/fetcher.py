"""非同期 OHLCV データ取得モジュール。

bitbank Public API から年単位で candlestick データを取得し、
Storage に Parquet 形式で保存する。
"""
from __future__ import annotations

import pandas as pd
from loguru import logger

from cryptbot.data.storage import Storage
from cryptbot.exchanges.base import BaseExchange, Candle
from cryptbot.utils.time_utils import JST


class DataFetcher:
    """bitbank から OHLCV データを取得して Storage に保存するクラス。

    Args:
        exchange: 取引所アダプター（BaseExchange の実装）
        storage: データ保存先（Storage インスタンス）
    """

    def __init__(
        self,
        exchange: BaseExchange,
        storage: Storage,
    ) -> None:
        self._exchange = exchange
        self._storage = storage

    async def fetch_and_store(
        self,
        pair: str,
        timeframe: str,
        years: list[int],
        *,
        overwrite: bool = False,
    ) -> dict[int, int]:
        """指定した年のデータを取得して保存する。

        Args:
            pair: 取引ペア（例: "btc_jpy"）
            timeframe: 時間足（例: "1hour"）
            years: 取得する年のリスト（例: [2022, 2023, 2024]）
            overwrite: True の場合、既存データを上書きする

        Returns:
            {year: 保存したローソク足数} の dict
            既存データでスキップした年は dict に含めない

        Raises:
            ExchangeError: 取引所 API でエラーが発生した場合
        """
        result: dict[int, int] = {}

        for year in years:
            if not overwrite and self._storage.has_ohlcv(pair, timeframe, year):
                logger.info(f"Skipping {pair} {timeframe} {year}: already exists")
                continue

            logger.info(f"Fetching {pair} {timeframe} {year}...")
            candles = await self._exchange.get_candlesticks(pair, timeframe, year)
            df = self.candles_to_dataframe(candles)
            self._storage.save_ohlcv(df, pair, timeframe, year)
            n = len(df)
            logger.info(f"Saved {n} candles for {pair} {timeframe} {year}")
            result[year] = n

        return result

    async def fetch_range(
        self,
        pair: str,
        timeframe: str,
        start_year: int,
        end_year: int,
        *,
        overwrite: bool = False,
    ) -> dict[int, int]:
        """start_year から end_year（含む）の範囲でデータを取得する。

        fetch_and_store の薄いラッパー。

        Args:
            pair: 取引ペア（例: "btc_jpy"）
            timeframe: 時間足（例: "1hour"）
            start_year: 取得開始年（含む）
            end_year: 取得終了年（含む）
            overwrite: True の場合、既存データを上書きする

        Returns:
            {year: 保存したローソク足数} の dict

        Raises:
            ExchangeError: 取引所 API でエラーが発生した場合
        """
        years = list(range(start_year, end_year + 1))
        return await self.fetch_and_store(pair, timeframe, years, overwrite=overwrite)

    @staticmethod
    def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
        """Candle のリストを OHLCV DataFrame に変換する。

        Args:
            candles: Candle オブジェクトのリスト

        Returns:
            columns: timestamp(datetime, JST), open, high, low, close, volume (全て float64)
            timestamp でソート済み
        """
        if not candles:
            df = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            ).astype(
                {
                    "timestamp": pd.DatetimeTZDtype(tz=JST),
                    "open": "float64",
                    "high": "float64",
                    "low": "float64",
                    "close": "float64",
                    "volume": "float64",
                }
            )
            return df

        records = [
            {
                "timestamp": c.timestamp,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in candles
        ]

        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)

        # timezone-aware でない場合は JST を付与、ある場合は JST に変換
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(JST)
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert(JST)

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype("float64")

        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
