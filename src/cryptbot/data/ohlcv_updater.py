"""OHLCV 自動更新モジュール。

DataFetcher のラッパーとして起動時バックフィルとバー境界更新を提供する。
"""
from __future__ import annotations

import logging
from datetime import datetime

from cryptbot.data.fetcher import DataFetcher
from cryptbot.utils.time_utils import JST

logger = logging.getLogger(__name__)


class OhlcvUpdater:
    """OHLCV データの自動取得・更新を担うクラス。

    Args:
        fetcher: DataFetcher インスタンス（BitbankExchange + Storage 注入済み）
        pair: 取引ペア（例: "btc_jpy"）
        timeframe: 時間足（例: "1hour"）
        backfill_years: バックフィル対象の年数（デフォルト: 2）
                        当年を含む直近 N 年分を取得する。
    """

    def __init__(
        self,
        fetcher: DataFetcher,
        pair: str,
        timeframe: str,
        backfill_years: int = 2,
    ) -> None:
        self._fetcher = fetcher
        self._pair = pair
        self._timeframe = timeframe
        self._backfill_years = backfill_years

    async def backfill(self) -> int:
        """当年から backfill_years 年前までの不足データを取得して保存する。

        既存年は DataFetcher 内の has_ohlcv() チェックでスキップされる。

        Returns:
            保存した合計ローソク足数（既存スキップ分は含まない）

        Raises:
            ExchangeError: API 通信エラー時
        """
        end_year = datetime.now(JST).year
        start_year = end_year - (self._backfill_years - 1)
        years = list(range(start_year, end_year + 1))

        logger.info(
            "OhlcvUpdater: バックフィル開始 pair=%s timeframe=%s years=%s",
            self._pair, self._timeframe, years,
        )
        result = await self._fetcher.fetch_and_store(
            self._pair, self._timeframe, years, overwrite=False
        )
        total = sum(result.values())
        logger.info("OhlcvUpdater: バックフィル完了 合計 %d 本", total)
        return total

    async def update_latest(self) -> bool:
        """当年の Parquet を overwrite=True で再取得して上書き保存する。

        バー境界ごとに呼び出すことで最新バーを Storage に反映する。
        失敗しても呼び出し元は前回データで処理を続行するため、例外は握りつぶす。

        Returns:
            True: 更新成功, False: 更新失敗
        """
        current_year = datetime.now(JST).year
        try:
            await self._fetcher.fetch_and_store(
                self._pair, self._timeframe, [current_year], overwrite=True
            )
            logger.debug("OhlcvUpdater: 当年(%d)データ更新完了", current_year)
            return True
        except Exception:
            logger.warning(
                "OhlcvUpdater: 当年(%d)データ更新失敗（前回データで続行）", current_year,
                exc_info=True,
            )
            return False
