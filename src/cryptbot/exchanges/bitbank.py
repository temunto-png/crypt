"""bitbank 取引所アダプター（Public API のみ、Phase 1 バックテスト用）。"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from cryptbot.exchanges.base import (
    BaseExchange,
    Candle,
    ExchangeError,
    OrderBook,
    Ticker,
)
from cryptbot.utils.logger import sanitize_error
from cryptbot.utils.time_utils import unix_ms_to_datetime

# retry 対象の HTTP ステータスコード（429 レート制限 + 5xx サーバーエラー）
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class _RetryableExchangeError(ExchangeError):
    """リトライ対象の ExchangeError（tenacity の retry 条件に使用）。"""


class BitbankExchange(BaseExchange):
    """bitbank 取引所アダプター。

    Phase 1 では Public API のみを使用する。
    Private API メソッドは NotImplementedError を raise する。
    """

    BASE_URL = "https://public.bitbank.cc"

    VALID_TIMEFRAMES = frozenset([
        "1min", "5min", "15min", "30min",
        "1hour", "4hour", "8hour", "12hour",
        "1day", "1week", "1month",
    ])

    # bitbank API の仕様: 短時間足は YYYYMMDD（日次）、長時間足は YYYY（年次）
    _DAY_FORMAT_TIMEFRAMES = frozenset(["1min", "5min", "15min", "30min", "1hour"])

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """
        Args:
            client: 外部から注入する httpx.AsyncClient（テスト用）。
                    None の場合はデフォルト設定で作成する。
        """
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={"User-Agent": "cryptbot/0.1.0"},
            )
            self._owns_client = True

    async def __aenter__(self) -> "BitbankExchange":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """自身が所有するクライアントをクローズする。"""
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # BaseExchange プロパティ
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return "bitbank"

    @property
    def supported_pairs(self) -> list[str]:
        return ["btc_jpy"]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def get_ticker(self, pair: str) -> Ticker:
        """GET /{pair}/ticker を呼び出してティッカー情報を返す。"""
        data = await self._get(f"/{pair}/ticker")
        d = data["data"]
        return Ticker(
            pair=pair,
            sell=float(d["sell"]),
            buy=float(d["buy"]),
            high=float(d["high"]),
            low=float(d["low"]),
            last=float(d["last"]),
            vol=float(d["vol"]),
            timestamp=unix_ms_to_datetime(int(d["timestamp"])),
        )

    async def get_order_book(self, pair: str) -> OrderBook:
        """GET /{pair}/depth を呼び出して板情報を返す。"""
        data = await self._get(f"/{pair}/depth")
        d = data["data"]
        asks = [(float(p), float(s)) for p, s in d["asks"]]
        bids = [(float(p), float(s)) for p, s in d["bids"]]
        return OrderBook(
            pair=pair,
            asks=asks,
            bids=bids,
            timestamp=unix_ms_to_datetime(int(d["timestamp"])),
        )

    async def get_candlesticks(
        self,
        pair: str,
        timeframe: str,
        year: int,
    ) -> list[Candle]:
        """指定した年の全 OHLCV を返す。

        短時間足（1min/5min/15min/30min/1hour）は YYYYMMDD 形式で日次リクエストし
        年全体を集約する。長時間足は YYYY 形式で一括取得する。

        Args:
            pair: 取引ペア（例: "btc_jpy"）
            timeframe: ローソク足タイプ（VALID_TIMEFRAMES に含まれる必要がある）
            year: 取得する年（例: 2024）

        Returns:
            Candle のリスト（timestamp 昇順）

        Raises:
            ValueError: timeframe が VALID_TIMEFRAMES に含まれない場合
            ExchangeError: API エラーの場合
        """
        if timeframe not in self.VALID_TIMEFRAMES:
            raise ValueError(
                f"無効な timeframe: {timeframe!r}。"
                f" 有効な値: {sorted(self.VALID_TIMEFRAMES)}"
            )
        if timeframe in self._DAY_FORMAT_TIMEFRAMES:
            return await self._get_candlesticks_by_day(pair, timeframe, year)
        return await self._get_candlesticks_by_year(pair, timeframe, year)

    async def _get_candlesticks_by_year(
        self, pair: str, timeframe: str, year: int
    ) -> list[Candle]:
        """長時間足: YYYY 形式で一括取得。"""
        data = await self._get(f"/{pair}/candlestick/{timeframe}/{year}")
        return self._parse_candlestick_response(data, timeframe)

    async def _get_candlesticks_by_day(
        self, pair: str, timeframe: str, year: int
    ) -> list[Candle]:
        """短時間足: YYYYMMDD 形式で日次リクエストし年全体を集約する。

        当年の場合は昨日まで取得する（当日バーは未確定のため）。
        """
        today = date.today()
        start = date(year, 1, 1)
        # 当年は昨日まで（当日分は update_latest で都度更新）
        end = min(date(year, 12, 31), today - timedelta(days=1))

        if start > end:
            return []

        candles: list[Candle] = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y%m%d")
            data = await self._get(f"/{pair}/candlestick/{timeframe}/{date_str}")
            candles.extend(self._parse_candlestick_response(data, timeframe))
            current += timedelta(days=1)
            # レート制限回避: bitbank の公開 API は 1req/0.1s が安全な上限
            await asyncio.sleep(0.15)

        return candles

    def _parse_candlestick_response(
        self, data: dict, timeframe: str
    ) -> list[Candle]:
        """candlestick API レスポンスから Candle リストを生成する。"""
        candlestick_list = data["data"]["candlestick"]
        candles: list[Candle] = []
        found = False
        for entry in candlestick_list:
            if entry.get("type") == timeframe:
                found = True
                for ohlcv in entry["ohlcv"]:
                    open_, high, low, close, volume, ts_ms = ohlcv
                    candles.append(
                        Candle(
                            timestamp=unix_ms_to_datetime(int(ts_ms)),
                            open=float(open_),
                            high=float(high),
                            low=float(low),
                            close=float(close),
                            volume=float(volume),
                        )
                    )
                break
        if not found and candlestick_list:
            raise ExchangeError(
                f"timeframe {timeframe!r} に対応するデータが見つかりませんでした",
                status_code=None,
            )
        return candles

    # ------------------------------------------------------------------ #
    # Private API（Phase 2 用）
    # ------------------------------------------------------------------ #

    async def get_assets(self) -> dict[str, float]:
        raise NotImplementedError("get_assets は Phase 2 で実装予定です。")

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        size: float,
        price: float | None = None,
    ) -> dict:
        raise NotImplementedError("place_order は Phase 2 で実装予定です。")

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        raise NotImplementedError("cancel_order は Phase 2 で実装予定です。")

    async def get_open_orders(self, pair: str) -> list[dict]:
        raise NotImplementedError("get_open_orders は Phase 2 で実装予定です。")

    async def get_order(self, pair: str, order_id: str) -> dict:
        raise NotImplementedError("get_order は Phase 4 で実装予定です。")

    # ------------------------------------------------------------------ #
    # 内部ヘルパー
    # ------------------------------------------------------------------ #

    async def _get(self, path: str) -> dict:
        """GET リクエストを送信し、レスポンス JSON を返す。

        タイムアウト・429・5xx に対して最大 3 回、指数バックオフ + ジッターで再試行する。
        4xx（429 以外）は即時 ExchangeError を送出する（リトライ不可）。

        Raises:
            ExchangeError: 全リトライ消費後も失敗した場合、または非リトライエラーの場合
        """
        url = f"{self.BASE_URL}{path}"

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential_jitter(initial=1, max=10, jitter=1),
                retry=retry_if_exception_type(_RetryableExchangeError),
                reraise=True,
            ):
                with attempt:
                    try:
                        response = await self._client.get(url)
                        response.raise_for_status()
                    except httpx.TimeoutException as exc:
                        # タイムアウトはリトライ対象
                        raise _RetryableExchangeError(
                            f"リクエストタイムアウト: {url}",
                            status_code=None,
                            raw_response=sanitize_error(exc),
                        ) from exc
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status not in _RETRYABLE_STATUS:
                            # 4xx（429 以外）はリトライ不可: ExchangeError として即時送出
                            raise ExchangeError(
                                f"HTTP エラー {status}: {url}",
                                status_code=status,
                                raw_response=sanitize_error(exc),
                            ) from exc
                        # リトライ対象: _RetryableExchangeError として送出
                        raise _RetryableExchangeError(
                            f"HTTP エラー {status}: {url}",
                            status_code=status,
                            raw_response=sanitize_error(exc),
                        ) from exc

                    body = response.json()
                    if body.get("success") != 1:
                        raise ExchangeError(
                            f"API エラー (success != 1): {url}",
                            status_code=response.status_code,
                            raw_response=sanitize_error(Exception(response.text)),
                        )
                    return body  # type: ignore[return-value]

        except RetryError as exc:
            # 全リトライ消費（reraise=True でも tenacity が RetryError を送出するケース対応）
            last = exc.last_attempt.exception()
            if isinstance(last, ExchangeError):
                raise last from exc
            raise ExchangeError(
                f"リトライ上限に達しました: {url}",
                status_code=None,
                raw_response=sanitize_error(exc),
            ) from exc

        # unreachable
        raise ExchangeError(f"予期しない状態: {url}", status_code=None)  # pragma: no cover
