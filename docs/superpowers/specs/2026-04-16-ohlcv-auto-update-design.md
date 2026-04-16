# OHLCV 自動更新設計書

**日付**: 2026-04-16
**対象システム**: BTC/JPY 現物 Long-only 自動売買システム
**ステータス**: 承認済み

---

## 背景・目的

`data/fetcher.py` の `DataFetcher` は存在するが、`LiveEngine` の実行フローに接続されていない。
現状は手動で Parquet を投入しなければ live/paper モードが動作しない。

本設計では **起動時バックフィル + バー境界ごとの更新** を自動化し、手動投入を不要にする。

---

## アーキテクチャ

### 新設クラス: `OhlcvUpdater`

```
src/cryptbot/data/ohlcv_updater.py
```

`DataFetcher` と `Storage` を受け取る薄いラッパー。
起動時の一括取得とバー境界の都度更新を単一の責務として持つ。

```
OhlcvUpdater(fetcher, storage, pair, timeframe, backfill_years)
  ├── async backfill() → int          # 不足年分を一括取得、取得したローソク足の合計を返す
  └── async update_latest() → bool    # 当年 Parquet を overwrite=True で更新
```

### 実行フロー

```
_run_live()
  ├── BitbankExchange インスタンス生成（Public API 用）
  ├── OhlcvUpdater 生成
  ├── OhlcvUpdater.backfill()          # 失敗 → return 1 (fail-closed)
  └── engine.run(assets, updater)

LiveEngine.run(assets, updater)
  └── _bar_loop(updater)
       ├── _wait_for_next_bar()
       ├── updater.update_latest()     # 失敗 → warning のみ（前回データで続行）
       ├── _load_ohlcv()
       └── run_one_bar(data)
```

---

## 詳細設計

### `OhlcvUpdater`

```python
class OhlcvUpdater:
    def __init__(
        self,
        fetcher: DataFetcher,
        pair: str,
        timeframe: str,
        backfill_years: int = 2,
    ) -> None: ...

    async def backfill(self) -> int:
        """当年から backfill_years 年前までの不足データを取得して保存する。
        既存年は DataFetcher 内の has_ohlcv() チェックでスキップ。
        Returns: 保存した合計ローソク足数
        Raises: ExchangeError（API 失敗時）
        """

    async def update_latest(self) -> bool:
        """当年 Parquet を overwrite=True で再取得して上書き保存する。
        Returns: True=成功, False=失敗（fail-open）
        """
```

**バックフィル範囲の決定ロジック**:
- `end_year = 現在年`
- `start_year = end_year - (backfill_years - 1)`
- 例: `backfill_years=2`, 2026年 → `[2025, 2026]`

### `LiveSettings` への追加

```python
ohlcv_backfill_years: int = Field(default=2, ge=1)
```

### `LiveEngine` への変更

```python
def __init__(self, ..., ohlcv_updater: "OhlcvUpdater | None" = None) -> None: ...

async def run(self, assets=None) -> None:
    # updater は _bar_loop に渡す（run シグネチャ変更なし）

async def _bar_loop(self) -> None:
    while True:
        await self._wait_for_next_bar()
        if self._ohlcv_updater is not None:
            await self._ohlcv_updater.update_latest()   # 失敗しても続行
        data = self._load_ohlcv()
        if data is not None:
            await self.run_one_bar(data)
```

### `main.py` への変更

`_run_live()` 内で `OhlcvUpdater` を生成し、`_live_async()` の中でバックフィルを実行する。
バックフィルと live 永続プロセスを同一イベントループで処理することで、
`asyncio.run()` の二重呼び出し問題を回避する。

```python
# _run_live() 内: OhlcvUpdater を生成して engine に渡す
from cryptbot.exchanges.bitbank import BitbankExchange
from cryptbot.data.fetcher import DataFetcher
from cryptbot.data.ohlcv_updater import OhlcvUpdater

pub_exchange = BitbankExchange()  # Public API 用（バックフィル + 定期更新）
fetcher = DataFetcher(exchange=pub_exchange, storage=storage)
ohlcv_updater = OhlcvUpdater(
    fetcher=fetcher,
    pair=settings.live.pair,
    timeframe=settings.live.timeframe,
    backfill_years=settings.live.ohlcv_backfill_years,
)

# _live_async() 内: バックフィルを API 疎通確認の前に実行
async def _live_async() -> None:
    try:
        n = await ohlcv_updater.backfill()
        print(f"[INFO] OHLCV バックフィル完了: {n} 本")
    except Exception as exc:
        print(f"[ERROR] OHLCV バックフィル失敗: {exc}", file=sys.stderr)
        raise LiveGateError(str(exc)) from exc

    assets = await verify_api_permissions(exchange)
    ...
    await engine.run(assets=assets)
```

`LiveEngine` 生成時に `ohlcv_updater=ohlcv_updater` を渡す。
`pub_exchange`（`BitbankExchange`）は `_live_async()` のスコープ内で生存し、
プロセス終了時に GC される（`httpx.AsyncClient` のライフタイムとして許容範囲）。

---

## エラーハンドリング方針

| タイミング | エラー | 動作 |
|-----------|--------|------|
| 起動時 `backfill()` | API エラー | `return 1`（fail-closed） |
| バー境界 `update_latest()` | API エラー | warning ログのみ・前回データで続行（fail-open） |
| `_load_ohlcv()` | データ不足 | warning ログ・バースキップ（既存挙動維持） |

---

## テスト計画

### `tests/test_data/test_ohlcv_updater.py`（新規 8 件）

| # | テスト | 内容 |
|---|--------|------|
| 1 | `test_backfill_fetches_missing_years` | 不足年を fetch_and_store で取得する |
| 2 | `test_backfill_skips_existing_years` | `has_ohlcv=True` の年はスキップ |
| 3 | `test_backfill_propagates_api_error` | API エラーは例外を伝播する |
| 4 | `test_backfill_returns_total_candles` | 取得ローソク足合計を返す |
| 5 | `test_update_latest_overwrites_current_year` | `overwrite=True` で当年を更新 |
| 6 | `test_update_latest_returns_false_on_error` | API エラーで `False` を返す |
| 7 | `test_bar_loop_calls_update_latest` | `_bar_loop()` が各バー前に `update_latest()` を呼ぶ |
| 8 | `test_bar_loop_skips_update_when_no_updater` | `updater=None` で呼ばれない（既存互換） |

### 既存テストへの影響

- `tests/test_live/test_engine.py`: `LiveEngine` コンストラクタに `ohlcv_updater=None` がデフォルトのため変更不要
- `tests/test_main/test_main.py`: `_run_live()` の live ゲート失敗テストはバックフィル前に return するため影響なし

---

## 完了条件

- `python -m pytest -q` が全通過（592 → 600 件以上）
- `live` モード起動時に `[INFO] OHLCV バックフィル完了: N 本` がコンソールに表示される
- `update_latest()` 失敗時にシステムが停止しない
