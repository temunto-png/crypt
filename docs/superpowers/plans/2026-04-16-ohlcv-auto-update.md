# OHLCV 自動更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LiveEngine 起動時に OHLCV の不足データをバックフィルし、バー境界ごとに最新データを自動更新する。

**Architecture:** `OhlcvUpdater` クラスを `data/ohlcv_updater.py` に新設し、既存の `DataFetcher` のラッパーとして起動時バックフィル（不足年分を一括取得）とバー境界更新（当年を overwrite）を提供する。`LiveEngine` には `ohlcv_updater: OhlcvUpdater | None = None` として optional injection し、`_bar_loop()` 内の各バー処理前に `update_latest()` を呼ぶ。`main.py` の `_run_live()` では `_live_async()` 内でバックフィルを実行してから engine を起動する。

**Tech Stack:** Python 3.11+, pytest-asyncio, unittest.mock (AsyncMock / MagicMock), pandas, pyarrow

---

## ファイル構成

| 操作 | パス | 内容 |
|------|------|------|
| 新規作成 | `src/cryptbot/data/ohlcv_updater.py` | `OhlcvUpdater` クラス |
| 新規作成 | `tests/test_data/test_ohlcv_updater.py` | `OhlcvUpdater` テスト（8件） |
| 変更 | `src/cryptbot/config/settings.py` | `LiveSettings` に `ohlcv_backfill_years` 追加 |
| 変更 | `src/cryptbot/live/engine.py` | `ohlcv_updater` 注入・`_bar_loop()` 更新 |
| 変更 | `src/cryptbot/main.py` | `_run_live()` に `OhlcvUpdater` 生成・バックフィル追加 |

---

## Task 1: `LiveSettings` に `ohlcv_backfill_years` を追加

**Files:**
- Modify: `src/cryptbot/config/settings.py:80-90`

- [ ] **Step 1: `LiveSettings` に `ohlcv_backfill_years` フィールドを追加する**

`src/cryptbot/config/settings.py` の `LiveSettings` クラス内、`min_order_size_btc` の直後に追加:

```python
min_order_size_btc: float = Field(default=0.0001, gt=0)
ohlcv_backfill_years: int = Field(default=2, ge=1)
```

- [ ] **Step 2: テストを実行して既存テストが通ることを確認**

```bash
cd C:/tool/claude/crypt
python -m pytest tests/test_config/ -q
```

Expected: 全テスト PASS（新フィールドはデフォルト値があるため既存テストへの影響なし）

- [ ] **Step 3: コミット**

```bash
git add src/cryptbot/config/settings.py
git commit -m "feat(config): LiveSettings に ohlcv_backfill_years を追加"
```

---

## Task 2: `OhlcvUpdater` クラスを実装する

**Files:**
- Create: `src/cryptbot/data/ohlcv_updater.py`

- [ ] **Step 1: `tests/test_data/test_ohlcv_updater.py` を新規作成し、最初の failing テストを書く**

```python
"""OhlcvUpdater クラスのテスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cryptbot.data.ohlcv_updater import OhlcvUpdater


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
```

- [ ] **Step 2: テストを実行して FAIL することを確認**

```bash
python -m pytest tests/test_data/test_ohlcv_updater.py::TestBackfill::test_backfill_fetches_missing_years -v
```

Expected: `ModuleNotFoundError: No module named 'cryptbot.data.ohlcv_updater'`

- [ ] **Step 3: `src/cryptbot/data/ohlcv_updater.py` を作成して最小実装を書く**

```python
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
```

- [ ] **Step 4: 最初のテストを実行して PASS することを確認**

```bash
python -m pytest tests/test_data/test_ohlcv_updater.py::TestBackfill::test_backfill_fetches_missing_years -v
```

Expected: PASS

- [ ] **Step 5: 残りの `TestBackfill` テストを追加する**

`tests/test_data/test_ohlcv_updater.py` の `TestBackfill` クラスに追記:

```python
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
        from cryptbot.exchanges.base import ExchangeError
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
```

- [ ] **Step 6: テストを実行して PASS することを確認**

```bash
python -m pytest tests/test_data/test_ohlcv_updater.py::TestBackfill -v
```

Expected: 4件 PASS

- [ ] **Step 7: `TestUpdateLatest` テストを追加する**

`tests/test_data/test_ohlcv_updater.py` に `TestUpdateLatest` クラスを追加:

```python
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
        from cryptbot.exchanges.base import ExchangeError
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
```

- [ ] **Step 8: テストを実行して PASS することを確認**

```bash
python -m pytest tests/test_data/test_ohlcv_updater.py -v
```

Expected: 6件 PASS

- [ ] **Step 9: コミット**

```bash
git add src/cryptbot/data/ohlcv_updater.py tests/test_data/test_ohlcv_updater.py
git commit -m "feat(data): OhlcvUpdater クラスを追加（バックフィル + 定期更新）"
```

---

## Task 3: `LiveEngine` に `ohlcv_updater` を注入し `_bar_loop()` で呼ぶ

**Files:**
- Modify: `src/cryptbot/live/engine.py:85-108, 345-353`
- Modify: `tests/test_data/test_ohlcv_updater.py` — `TestBarLoop` クラスを追加

- [ ] **Step 1: `tests/test_live/test_engine.py` に `TestBarLoop` クラスを追加する（failing）**

`tests/test_live/test_engine.py` の末尾に追加:

```python
class TestBarLoop:
    @pytest.mark.asyncio
    async def test_bar_loop_calls_update_latest(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """_bar_loop() が各バー処理前に update_latest() を呼ぶ。"""
        mock_updater = MagicMock()
        mock_updater.update_latest = AsyncMock(return_value=True)

        engine = LiveEngine(
            strategy=AlwaysHoldStrategy(),
            risk_manager=risk_manager,
            state_store=state_store,
            storage=storage,
            executor=mock_executor,
            settings=settings,
            ohlcv_updater=mock_updater,
        )
        _seed_state(state_store)

        call_count = 0

        async def fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with patch.object(engine, "_wait_for_next_bar", side_effect=fake_wait):
            with patch.object(engine, "_load_ohlcv", return_value=None):
                try:
                    await engine._bar_loop()
                except asyncio.CancelledError:
                    pass

        assert mock_updater.update_latest.call_count >= 1

    @pytest.mark.asyncio
    async def test_bar_loop_skips_update_when_no_updater(
        self, risk_manager, state_store, storage, mock_executor, settings
    ) -> None:
        """ohlcv_updater=None の場合でも _bar_loop() がクラッシュしない。"""
        engine = LiveEngine(
            strategy=AlwaysHoldStrategy(),
            risk_manager=risk_manager,
            state_store=state_store,
            storage=storage,
            executor=mock_executor,
            settings=settings,
            ohlcv_updater=None,
        )
        _seed_state(state_store)

        call_count = 0

        async def fake_wait():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with patch.object(engine, "_wait_for_next_bar", side_effect=fake_wait):
            with patch.object(engine, "_load_ohlcv", return_value=None):
                try:
                    await engine._bar_loop()
                except asyncio.CancelledError:
                    pass

        assert call_count >= 1  # クラッシュせず到達できた
```

- [ ] **Step 2: テストを実行して FAIL することを確認**

```bash
python -m pytest tests/test_live/test_engine.py::TestBarLoop -v
```

Expected: `TypeError: LiveEngine.__init__() got an unexpected keyword argument 'ohlcv_updater'`

- [ ] **Step 3: `LiveEngine.__init__()` に `ohlcv_updater` パラメータを追加する**

`src/cryptbot/live/engine.py` の `__init__` シグネチャを変更:

```python
def __init__(
    self,
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    state_store: LiveStateStore,
    storage: Storage,
    executor: LiveExecutor,
    settings: LiveSettings,
    regime_detector: RegimeDetector | None = None,
    ml_model: "BaseMLModel | None" = None,
    degradation_detector: "DegradationDetector | None" = None,
    ml_confidence_threshold: float = 0.6,
    ohlcv_updater: "OhlcvUpdater | None" = None,
) -> None:
```

`__init__` の本体末尾に追加:

```python
    self._ohlcv_updater = ohlcv_updater
```

TYPE_CHECKING ブロックに追加（ファイル冒頭の `if TYPE_CHECKING:` セクション）:

```python
if TYPE_CHECKING:
    from cryptbot.data.ohlcv_updater import OhlcvUpdater
    from cryptbot.models.base import BaseMLModel
    from cryptbot.models.degradation_detector import DegradationDetector
```

- [ ] **Step 4: `_bar_loop()` で `update_latest()` を呼ぶように変更する**

`src/cryptbot/live/engine.py` の `_bar_loop()` を変更:

```python
async def _bar_loop(self) -> None:
    """バー確定を待って run_one_bar() を呼ぶ無限ループ。"""
    while True:
        await self._wait_for_next_bar()
        if self._ohlcv_updater is not None:
            await self._ohlcv_updater.update_latest()
        data = self._load_ohlcv()
        if data is not None:
            await self.run_one_bar(data)
        else:
            logger.warning("live engine: OHLCV データを取得できませんでした")
```

- [ ] **Step 5: テストを実行して PASS することを確認**

```bash
python -m pytest tests/test_live/test_engine.py::TestBarLoop -v
```

Expected: 2件 PASS

- [ ] **Step 6: 全テストを実行してリグレッションがないことを確認**

```bash
python -m pytest tests/test_live/test_engine.py -q
```

Expected: 全テスト PASS

- [ ] **Step 7: コミット**

```bash
git add src/cryptbot/live/engine.py tests/test_live/test_engine.py
git commit -m "feat(live): LiveEngine に OhlcvUpdater を注入し _bar_loop() でバー前更新を呼ぶ"
```

---

## Task 4: `main.py` に `OhlcvUpdater` 生成とバックフィルを追加する

**Files:**
- Modify: `src/cryptbot/main.py:215-308`
- Modify: `tests/test_main/test_main.py` — `TestOhlcvBackfill` クラスを追加

> **patch ターゲットの注意:** `_run_live()` 内の import は関数ローカル（`from module import Name`）。
> `cryptbot.main.X` ではなく、クラスが定義されているモジュールでパッチする。

- [ ] **Step 1: `tests/test_main/test_main.py` の末尾に `TestOhlcvBackfill` を追加する（failing）**

`tests/test_main/test_main.py` の末尾（`TestVerifyAuditLog` の後）に追加:

```python
class TestOhlcvBackfill:
    def test_backfill_failure_returns_1(self, tmp_path: Path, monkeypatch) -> None:
        """OHLCV バックフィル失敗時に main が 1 を返す。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from cryptbot.exchanges.base import ExchangeError

        monkeypatch.setenv("CRYPT_MODE", "live")
        monkeypatch.setenv("CRYPT_BITBANK_API_KEY", "test_key")
        monkeypatch.setenv("CRYPT_BITBANK_API_SECRET", "test_secret")

        db_path = tmp_path / "live.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        mock_updater = MagicMock()
        mock_updater.backfill = AsyncMock(side_effect=ExchangeError("API timeout"))

        with (
            patch("cryptbot.live.gate.phase_4_gate"),
            patch("cryptbot.exchanges.bitbank_private.BitbankPrivateExchange"),
            patch("cryptbot.exchanges.bitbank.BitbankExchange"),
            patch("cryptbot.data.fetcher.DataFetcher"),
            patch("cryptbot.data.ohlcv_updater.OhlcvUpdater", return_value=mock_updater),
        ):
            result = main(["--mode", "live", "--confirm-live",
                           "--db", str(db_path), "--data-dir", str(data_dir)])

        assert result == 1
```

- [ ] **Step 2: テストを実行して FAIL することを確認**

```bash
python -m pytest tests/test_main/test_main.py::TestOhlcvBackfill -v
```

Expected: `ModuleNotFoundError: No module named 'cryptbot.data.ohlcv_updater'`（Task 2 完了後は `AssertionError`）

- [ ] **Step 3: `main.py` の `_run_live()` に `OhlcvUpdater` 生成とバックフィルを追加する**

`src/cryptbot/main.py` の `_run_live()` 内、`from cryptbot.live.gate import ...` インポート群の直後に追加:

```python
    from cryptbot.exchanges.bitbank import BitbankExchange
    from cryptbot.data.fetcher import DataFetcher
    from cryptbot.data.ohlcv_updater import OhlcvUpdater
```

`storage.initialize()` の直後（`kill_switch = KillSwitch(storage)` の前）に追加:

```python
    # Public API 用 Exchange（OHLCV バックフィル + 定期更新）
    pub_exchange = BitbankExchange()
    fetcher = DataFetcher(exchange=pub_exchange, storage=storage)
    ohlcv_updater = OhlcvUpdater(
        fetcher=fetcher,
        pair=settings.live.pair,
        timeframe=settings.live.timeframe,
        backfill_years=settings.live.ohlcv_backfill_years,
    )
```

`engine = LiveEngine(...)` の呼び出しに `ohlcv_updater=ohlcv_updater` を追加:

```python
    engine = LiveEngine(
        strategy=strategy,
        risk_manager=risk_manager,
        state_store=state_store,
        storage=storage,
        executor=executor,
        settings=settings.live,
        ml_model=ml_model,
        degradation_detector=degradation_detector,
        ml_confidence_threshold=settings.model.confidence_threshold,
        ohlcv_updater=ohlcv_updater,
    )
```

`_live_async()` 関数の先頭（`verify_api_permissions` 呼び出しの前）にバックフィルを追加:

```python
    async def _live_async() -> None:
        """API 疎通確認後にエンジンを起動する（単一イベントループ）。"""
        # OHLCV バックフィル（失敗時は LiveGateError として再 raise → return 1）
        try:
            n = await ohlcv_updater.backfill()
            print(
                f"[INFO] OHLCV バックフィル完了: {n} 本 "
                f"pair={settings.live.pair} tf={settings.live.timeframe}"
            )
        except Exception as exc:
            print(f"[ERROR] OHLCV バックフィル失敗: {exc}", file=sys.stderr)
            raise LiveGateError(str(exc)) from exc

        try:
            assets = await verify_api_permissions(exchange)
        except LiveGateError as exc:
            print(f"[ERROR] API 疎通確認失敗: {exc}", file=sys.stderr)
            raise

        jpy = assets.get("jpy", 0.0)
        btc = assets.get("btc", 0.0)
        print(
            f"[INFO] API 疎通確認完了: JPY={jpy:.0f}  BTC={btc:.6f}  "
            f"pair={settings.live.pair}  tf={settings.live.timeframe}"
        )

        await engine.run(assets=assets)
```

- [ ] **Step 4: テストを実行して PASS することを確認**

```bash
python -m pytest tests/test_main/test_main.py::TestOhlcvBackfill -v
```

Expected: PASS

- [ ] **Step 5: 全テストを実行してリグレッションがないことを確認**

```bash
python -m pytest tests/test_main/ -q
```

Expected: 全テスト PASS

- [ ] **Step 6: コミット**

```bash
git add src/cryptbot/main.py tests/test_main/test_main.py
git commit -m "feat(main): _run_live() に OhlcvUpdater バックフィルを追加"
```

---

## Task 5: 全テストを通してコミット

- [ ] **Step 1: 全テストスイートを実行する**

```bash
cd C:/tool/claude/crypt
python -m pytest -q
```

Expected: 600 件以上 PASS、0 FAIL

- [ ] **Step 2: テスト件数を確認する**

```bash
python -m pytest -q 2>&1 | tail -5
```

Expected: `6XX passed` (592 件 + 8 件以上の新規テスト)

- [ ] **Step 3: `progress.md` を更新する**

`.claude/progress.md` の「次のアクション」セクションを更新:

```markdown
## 次のアクション

1. ~~**P2 実装を開始**~~（完了）
2. ~~Codex ソースレビュー対応~~（完了）
3. ~~**OHLCV 自動投入実装**~~（完了）
4. ポジション同期（リカバリー）実装
5. 本番起動準備
```

- [ ] **Step 4: 最終コミット**

```bash
git add .claude/progress.md
git commit -m "docs: progress.md を更新（OHLCV 自動投入完了）"
```

---

## 完了条件チェックリスト

- [ ] `python -m pytest -q` が全通過（600 件以上）
- [ ] `live` モード起動ログに `[INFO] OHLCV バックフィル完了: N 本` が表示される
- [ ] `update_latest()` が失敗してもシステムが停止しない
- [ ] 既存テスト（592件）がリグレッションなく通過する
