# 暗号資産自動売買システム — 進捗

## 現在のフェーズ

**詳細敵対的レビュー 新規 P1 対応中（NF1/NF2 完了、NF3 未着手）**

- テスト数: **631 passed**
- ブランチ: `master`（HEAD = `654be13`、origin 未 push）
- 状態: NF1〜NF7 全完了
- メモリ: プロジェクト内 Read/Edit/Write は承認不要に設定済み（2026-04-16）

## リポジトリ

- GitHub: https://github.com/temunto-png/crypt.git（branch: master、HEAD = `303f425`）
- ローカル: `C:\tool\claude\crypt`
- 未追跡（意図的）: `.claude/`、`claude_code_adversarial_review_handoff.md`、`codex_review_handoff.md`、`docs/superpowers/`
- Codex 引き継ぎ書: `C:\tool\claude\crypt\codex_review_handoff.md`

## 完了事項

- [x] Phase 0〜3 + P2 adversarial review 全対応（pytest 477/477 passed）
- [x] **Phase 4 Task A1〜A4**: LiveSettings / BaseExchange.get_order() / Storage 変更（SUBMIT_FAILED・exclusive_transaction・マイグレーション）
- [x] **Phase 4 Task B**: BitbankPrivateExchange（HMAC-SHA256・monotonic nonce・retry nonce 再生成）+ テスト
- [x] **Phase 4 Task C**: LiveExecutor（TOC/TOU-safe 二重発注防止・PARTIAL_FILLED ライフサイクル・sanitize_error）+ テスト
- [x] **Phase 4 Task D**: LiveState + LiveStateStore + phase_4_gate + verify_api_permissions + テスト
- [x] **Phase 4 Task E**: LiveEngine（asyncio 永続プロセス・バーループ・PARTIAL_FILLED ポーラー）+ テスト
- [x] **Phase 4 Task F**: main.py `_run_live()` 実装（phase_4_gate・BitbankPrivateExchange・LiveEngine 統合）+ テスト更新

## Phase 4 実装済みファイル

| ファイル | 概要 |
|---------|------|
| `src/cryptbot/config/settings.py` | LiveSettings 追加 |
| `src/cryptbot/exchanges/base.py` | get_order() abstractmethod 追加 |
| `src/cryptbot/exchanges/bitbank.py` | get_order() stub 追加 |
| `src/cryptbot/exchanges/gmo.py` | get_order() stub 追加 |
| `src/cryptbot/exchanges/bitbank_private.py` | 新規: HMAC-SHA256 認証・place_order・cancel_order・get_order・get_assets |
| `src/cryptbot/data/storage.py` | SUBMIT_FAILED ステータス・exclusive_transaction・get_active_orders・get_order_exchange_id・マイグレーション |
| `src/cryptbot/execution/live_executor.py` | 新規: LiveExecutor（DuplicateOrderError・place_order・cancel_order・handle_partial_fill） |
| `src/cryptbot/live/__init__.py` | 新規 |
| `src/cryptbot/live/state.py` | 新規: LiveState dataclass + LiveStateStore |
| `src/cryptbot/live/gate.py` | 新規: LiveGateError・phase_4_gate（4条件 fail-closed）・verify_api_permissions |
| `src/cryptbot/live/engine.py` | 新規: LiveEngine（run_one_bar・run・_bar_loop・_partial_fill_loop） |
| `src/cryptbot/main.py` | _run_live() 実装・LiveTradingNotImplementedError 除去・live ルーティング |
| `tests/test_live/test_gate.py` | gate + verify_api_permissions テスト |
| `tests/test_live/test_state.py` | LiveStateStore CRUD + 変換ヘルパーテスト |
| `tests/test_live/test_engine.py` | LiveEngine run_one_bar / partial fill / graceful shutdown テスト |
| `tests/test_main/test_main.py` | live gate 失敗テスト更新（LiveTradingNotImplementedError → return 1） |

## 次のアクション

1. ~~**P2 実装を開始**~~（完了）
2. ~~Codex ソースレビュー対応~~（完了 — F1〜F7 全対応済み）
3. ~~**OHLCV 自動投入**~~（完了 — OhlcvUpdater 実装・LiveEngine 注入・バックフィル追加）
4. ~~**P2（F4/F5/F6）実装**~~（完了 — commit d85de98・テスト 601 passed）
5. ~~**詳細敵対的レビュー対応（NF1〜NF7）**~~（完了 — commit 未 push）
6. ポジション同期（リカバリー）実装（中優先度）
7. 本番起動準備

---

## 詳細敵対的レビュー 対応計画（2026-04-16）

引継ぎ書: `claude_code_detailed_adversarial_review_handoff.md`
テスト基準: 601 passed を維持しつつ追加テストを通過させる

### 未対応 P1（運用前にブロッカー）

| ID | 内容 | 主要ファイル |
|----|------|------------|
| NF1 | 約定結果が LiveState に反映されない — `handle_partial_fill()` が `FULLY_FILLED` 検出時に `balance`/`position_size`/`entry_price` を更新しない | `execution/live_executor.py:193-216`, `live/engine.py:374-385` |
| NF2 | 起動時に BTC 残高があるとき fail-closed にならない — 現状は警告のみ、`adopt_existing_position` 設定なしに継続する | `live/engine.py:573-591` |
| NF3 | `place_order()` 成功後の `order_submitted` 監査ログ失敗を握り潰す — 取引所に注文が存在するのに証跡が欠落 | `live/engine.py:430-458` |

### 未対応 P2（運用品質改善）

| ID | 内容 | 主要ファイル |
|----|------|------------|
| NF4 | `PARTIALLY_FILLED`/`CANCELED_UNFILLED`/`CANCELED_PARTIALLY_FILLED` をDB/LiveState に反映しない | `execution/live_executor.py:193-222` |
| NF5 | `load_ohlcv(verify=True)` が `verify_ohlcv_integrity()` を呼ばず、ハッシュ改ざんを検出できない | `data/storage.py:550-560`, `live/engine.py:463-468` |
| NF6 | `_bar_loop()` が `update_latest()` の失敗を見ておらず、stale data で取引を継続する | `live/engine.py:352-356`, `data/ohlcv_updater.py:52-69` |
| NF7 | ML劣化検知 baseline に `accuracy` を使っている（`mean_confidence` を使うべき） | `main.py:102-127` |

### 実装方針

**NF1**: `handle_partial_fill()` を `OrderSyncResult(side, status, executed_amount, average_price, terminal)` を返すように変更。`LiveEngine` 側で BUY/SELL ごとに `PortfolioState` と `LiveStateStore` を更新する。LiveState 更新失敗は fail-closed。

**NF2**: `_sync_initial_balance()` に BTC 残高チェックを追加。`btc >= min_order_size_btc` の場合は `LiveGateError` で停止。既存 LiveState あり時も `position_size` と実 BTC 残高を許容差で照合。

**NF3**: `_submit_pending()` の `try` ブロックを分割。`place_order()` 成功後に監査ログブロックを独立させ、失敗時は `LiveGateError` または kill switch。

**NF4**: `handle_partial_fill()` 内の bitbank ステータス処理を全列挙。`PARTIALLY_FILLED` → DB PARTIAL + 累積 amount 更新、`CANCELED_*` → DB 終端遷移 + 部分約定分を LiveState 反映。

**NF5**: `load_ohlcv(verify=True)` のループ内で `verify_ohlcv_integrity(pair, timeframe, year)` を呼び出す。live 用途では integrity NG を skip 扱いにせず fail-closed。

**NF6**: `_bar_loop()` で `update_latest()` の戻り値を確認し、`False` の場合は `run_one_bar()` をスキップ。連続失敗カウンタを追加し、閾値超過で kill switch。

**NF7**: `_build_ml_components()` の `baseline_confidence` を `ExperimentRecord.metrics["mean_confidence"]` から取得。欠落時は fail-closed（return 1）またはフォールバックログ。

### 実装チェックリスト

#### NF1 — 約定 LiveState 反映 ✅ 完了（commits: ffb115f, b9267d0）
- [x] `OrderSyncResult` dataclass を `execution/live_executor.py` に追加
- [x] `handle_partial_fill()` を `OrderSyncResult` 返却形式に変更（FULLY_FILLED / TIMEOUT_CANCELLED / WAITING）
- [x] `LiveEngine._poll_active_orders()` で `OrderSyncResult` を受け取り LiveState 更新
- [x] LiveState 更新失敗時の fail-closed 追加（`LiveGateError`）
- [x] BUY 約定後: `position_size`, `entry_price`, `position_entry_price`, `position_entry_time` 更新
- [x] SELL 約定後: `position_size=0`, `entry_price=0`, `position_entry_time=None`
- [x] テスト 6 件追加（FULLY_FILLED/WAITING/TIMEOUT_CANCELLED + LiveState 反映 3 件）

#### NF2 — 起動時 BTC 残高 fail-closed ✅ 完了（commit: 654be13）
- [x] `_sync_initial_balance()` 初回起動: BTC ≥ `min_order_size_btc` で `LiveGateError`（警告 → fail-closed）
- [x] 再起動: LiveState ノーポジ + 実 BTC あり → `LiveGateError`
- [x] 再起動: LiveState ポジあり + 実 BTC < min → `LiveGateError`
- [x] 再起動: BTC と `position_size` 乖離 > `balance_sync_tolerance_pct` → `LiveGateError`
- [x] テスト 4 件追加（`TestSyncInitialBalance` クラスに追加、611 passed）

#### NF3 — order_submitted 監査ログ fail-closed ✅ 完了（commit 未 push）
- [x] `_submit_pending()` の try ブロックを `place_order()` と監査ログで分割
- [x] 監査ログ失敗時は `LiveGateError` raise（fail-closed）
- [x] テスト 3 件追加（audit_log 失敗→LiveGateError / 両成功→True / place_order 失敗→False & audit 未呼び出し）

#### NF4 — キャンセル/部分約定 DB 反映 ✅ 完了
- [x] `handle_partial_fill()` に `PARTIALLY_FILLED`/`CANCELED_UNFILLED`/`CANCELED_PARTIALLY_FILLED` 分岐追加
- [x] `CANCELED_PARTIALLY_FILLED` で LiveEngine が LiveState を部分約定分で更新
- [x] テスト 6 件追加（executor 3 件 + engine 3 件）

#### NF5 — OHLCV ハッシュ検証 ✅ 完了
- [x] `load_ohlcv(verify=True)` のループ内で `verify_ohlcv_integrity()` を呼ぶ
- [x] `fail_closed: bool = False` 引数追加。live 経路（`_load_ohlcv()`）は `fail_closed=True` で呼出し
- [x] テスト 4 件追加（ハッシュ一致・不一致×fail_closed・未計算×fail_closed）

#### NF6 — OHLCV 更新失敗後の取引停止 ✅ 完了
- [x] `_bar_loop()` で `update_latest()` 戻り値を確認し、`False` で `run_one_bar()` スキップ
- [x] `_MAX_CONSECUTIVE_UPDATE_FAILURES = 3`、超過で `LiveGateError` raise
- [x] テスト 3 件追加（False→スキップ / True→呼出し / 連続3回→LiveGateError）

#### NF7 — ML baseline を mean_confidence に修正 ✅ 完了
- [x] `_build_ml_components()` の `baseline_confidence` を `metrics["mean_confidence"]` に変更
- [x] `mean_confidence` 欠落時は fail-closed（return None）
- [x] テスト 4 件追加

#### 完了条件
- [x] `python -m pytest -q` が PASS（631 passed）
- [ ] 各 fail-closed ケースで「停止すべき状態」と「継続してよい状態」が明確
- [ ] 復旧手順がログで追えること

## P2（F4/F5/F6）設計メモ（2026-04-16）

### 確定した設計方針

| Finding | 内容 | 方針 |
|---------|------|------|
| F4 | ML設定が live/paper の実行経路に未接続 | `_build_ml_components()` ヘルパーを `main.py` に追加。enabled=true でモデル未検出は fail-closed（return 1）。live + paper 両方に接続 |
| F5 | MLフィルタが特徴量空・例外時に fail-open | `apply_ml_filter()` に `fail_closed: bool = False` 引数追加。live/paper は `True`、backtest は `False`（デフォルト維持） |
| F6 | OHLCV整合性チェックが live 読み込み時に未使用 | `load_ohlcv()` に `verify: bool = False` 引数追加。live の `_load_ohlcv()` は `verify=True`。検証失敗ファイルはスキップ（バースキップ） |

### 変更ファイルリスト

| ファイル | Finding | 変更内容 |
|---------|---------|---------|
| `src/cryptbot/backtest/engine.py` | F5 | `apply_ml_filter()` に `fail_closed` 引数追加 |
| `src/cryptbot/live/engine.py` | F5, F6 | `apply_ml_filter()` 呼び出しに `fail_closed=True`、`_load_ohlcv()` に `verify=True` |
| `src/cryptbot/paper/engine.py` | F5 | `apply_ml_filter()` 呼び出しに `fail_closed=True` |
| `src/cryptbot/data/storage.py` | F6 | `load_ohlcv()` に `verify` 引数追加・ファイル単位の整合性検証ループ |
| `src/cryptbot/main.py` | F4 | `_build_ml_components()` 追加、`_run_paper()` / `_run_live()` に ML 組み立て接続 |

### 追加テスト

| テストファイル | クラス | 件数 |
|-------------|--------|------|
| `tests/test_backtest/test_engine.py` | `TestApplyMlFilter` | 4件 |
| `tests/test_data/test_storage.py` | `TestLoadOhlcvVerify` | 5件 |
| `tests/test_main/test_main.py` | `TestBuildMlComponents` | 4件 |

### 実装フェーズ（チェックリスト）

- [x] **F5-1**: `backtest/engine.py` — `apply_ml_filter()` に `fail_closed` 引数追加
- [x] **F5-2**: `live/engine.py` — `apply_ml_filter()` 呼び出しに `fail_closed=True`
- [x] **F5-3**: `paper/engine.py` — `apply_ml_filter()` 呼び出しに `fail_closed=True`
- [x] **F5-4**: `tests/test_backtest/test_engine.py` — `TestApplyMlFilter` 4件追加
- [x] **F6-1**: `data/storage.py` — `load_ohlcv()` に `verify` 引数追加
- [x] **F6-2**: `live/engine.py` — `_load_ohlcv()` に `verify=True`
- [x] **F6-3**: `tests/test_data/test_storage.py` — `TestLoadOhlcvVerify` 5件追加
- [x] **F4-1**: `main.py` — `_build_ml_components()` 追加・`_run_paper()` 接続
- [x] **F4-2**: `main.py` — `_run_live()` 接続
- [x] **F4-3**: `tests/test_main/test_main.py` — `TestBuildMlComponents` 4件追加
- [x] **確認**: `pytest -q` 全通過（601 passed）

## P1 バグ修正 完了（master マージ済み、2026-04-16）

ベース SHA `e20a7f6` → HEAD `8dd2986`

| Finding | 内容 | ファイル |
|---------|------|---------|
| F1 | `_poll_active_orders()` — SUBMITTED+PARTIAL を照合対象に | `live/engine.py` |
| F2 | `_sync_initial_balance()` — 起動時に取引所残高を同期、peak_balance 更新 | `live/engine.py`, `main.py`, `live/state.py` |
| F3 | `_submit_pending()` — BUY=リスク連動サイズ、SELL=ポジションサイズ | `live/engine.py` |
| F7 | `_record_signal()` — BUY のみ fail-closed | `live/engine.py` |

## Phase 4 主要な設計判断

| 項目 | 決定 | 理由 |
|------|------|------|
| asyncio 永続プロセス | asyncio.run(engine.run()) | cron より状態管理が容易・PARTIAL ポーリングに適合 |
| 二重発注防止 | BEGIN EXCLUSIVE + CREATED チェック | TOC/TOU 競合を SQLite レベルで防止 |
| bitbank POST 署名 | nonce + body（パスなし） | bitbank 仕様: GET は nonce+path、POST は nonce+body |
| nonce 再生成 | retry ループ内で毎回 _auth_headers() | 失効 nonce での再試行を防止 |
| PARTIAL タイムアウト | order_timeout_sec (default 7200s) | 30分ポーリング × 最大 4 回 = 2 時間後キャンセル |
| LiveState 初期残高 | 0 で初期化（_init_fresh_state） | 実際の残高は起動時に exchange から取得する想定 |
| テスト残高シード | _seed_state(state_store) | monthly_limit=0 による circuit breaker 即時発動を防止 |

## Phase 4 テスト状況（560 passed、全 Task 完了）

- `tests/test_execution/test_live_executor.py`: LiveExecutor テスト
- `tests/test_live/test_gate.py`: phase_4_gate / verify_api_permissions (28件)
- `tests/test_live/test_state.py`: LiveStateStore (28件)
- `tests/test_live/test_engine.py`: LiveEngine (17件)

## 過去の完了事項（Phase 0〜3）

### Phase 3 実装完了（pytest 477/477 passed）
- ML モデル（LightGBM / XGBoost）+ 劣化検知 + 実験管理
- PaperEngine に ML フィルター統合

### Phase 2 実装完了（pytest 417/417 passed）
- PaperSimulator / PaperExecutor / LiveExecutor スケルトン

### Phase 1 実装完了（pytest 288/288 passed）
- KillSwitch / RiskManager / CVaR / RegimeDetector / audit_log / order state machine

### Phase 0 実装完了（pytest 33/33 passed）
- 設定 / ロガー / 時刻ユーティリティ / 基盤

## 主要な設計判断（要約）

- 取引所: **bitbank**（APIドキュメント品質、OHLCV API充実）
- 銘柄: BTC/JPY 現物（Long-only）
- 時間足: 1hour
- コスト想定: Taker 0.1% + スリッページ 0.05% = 0.15%
- データ保存: SQLite + Parquet
- 設定: YAML + pydantic
- ログ: loguru (JSON Lines) + SQLite audit_log
