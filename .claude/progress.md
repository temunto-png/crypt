# 暗号資産自動売買システム — 進捗

## 現在のフェーズ

**claude-mem 導入 Phase 1 進行中（セキュリティテスト実施中）**

- テスト数: **655 passed**（crypt 本体は変更なし）
- ブランチ: `feat/startup-recovery-hardening`（base = `53a3a51`）— crypt 本体の Important 2件はまだ未修正
- メモリ: プロジェクト内 Read/Edit/Write は承認不要に設定済み（2026-04-16）

## claude-mem 導入状況（2026-04-17）

### Phase 0: 完了

| 項目 | 状態 |
|---|---|
| Bun v1.3.12 | winget でインストール済み |
| claude-mem v12.1.6 | npx 経由でプラグインインストール済み |
| `WORKER_HOST` | `127.0.0.1` 確認済み |
| `PROVIDER` | `claude` のみ |
| `EXCLUDED_PROJECTS` | NEM Wallet / NanoWallet / ai-diagnosis-platform / Kink Vision（settings.json + PowerShell profile） |
| `CHROMA_ENABLED` | `false`（初期 PoC は SQLite のみ） |
| `MODE` | `code--ja` |
| hooks.json | Setup / SessionStart / UserPromptSubmit / PostToolUse(*) / PreToolUse(Read) / Stop / SessionEnd |
| rollback バックアップ | `~/.claude/settings.json.backup-before-claude-mem-2026-04-17` |

### Phase 1: セキュリティテスト結果（2026-04-17）

| テスト | 結果 | 備考 |
|---|---|---|
| SL-01: DB secret スキャン | **PASS** | sk-/ghp_/api_key/password パターン検出なし |
| SL-02: tool output secret 保存確認 | **PASS** | ダミー secret（sk-test-AAA/mysecret）は narrative/text/facts に保存されず。LLM要約のみ保存 |
| SL-03: 個別 observation 削除 | **PASS** | `DELETE FROM observations WHERE id=X` で削除・確認できた |
| MP-01: 誤情報注入テスト | **未完了** | Binance 誤情報を手動挿入済み。次セッションで search で検出・訂正できるか確認要 |
| MP-03: 有害 memory 削除 | **未完了** | uv 未インストールによりセマンティック検索不可。FTS 検索は動作中 |

### 重要メモ: uv インストール済み・vector search 未解決

- uv 0.11.7 を `~/.local/bin` にインストール済み（2026-04-17）
- worker 再起動済みだが "Vector search failed" のまま
- `smart-install.js` が `~/.local/bin` の uv を検索するコードを確認済み
- **原因推定**: worker プロセスの PATH に `~/.local/bin` が含まれていない。次の Claude Code セッション起動時に SessionStart hook 経由で `smart-install.js` が自動検出する可能性がある

**次セッションで対応:**
1. Claude Code を完全再起動 → SessionStart hook で smart-install.js が uv を自動検出するか確認
2. 解決しない場合: claude-mem settings.json に `CLAUDE_MEM_UV_PATH` 等の設定項目があるか確認
3. MP-01 誤情報（Binance, id=6）を search で検出・削除して MP-03 完了
4. crypt Important A / B を修正して master マージ

### Phase 1 残タスク

- [x] uv インストール（v0.11.7、`~/.local/bin`）
- [ ] Claude Code 完全再起動 → vector search 有効化確認
- [ ] MP-01: 誤情報注入後の回答品質確認
- [ ] MP-03: 有害 memory 特定・削除確認（vector search 有効化後）
- [ ] `docs/claude_mem_security_tests.md` に結果を記入
- [ ] `docs/claude_mem_rollout_checklist.md` Phase 1 チェックを更新

### 作成済みドキュメント

| ファイル | 内容 |
|---|---|
| `docs/claude_mem_rollout_checklist.md` | Phase 0〜2 チェックリスト（Phase 0 完了済み） |
| `docs/claude_mem_repo_registry.md` | 対象/除外 repo・owner・削除責任者 |
| `docs/claude_mem_karpathy_rules_proposal.md` | andrej-karpathy-skills 最小差分提案 |
| `docs/claude_mem_golden_tasks.md` | memory on/off 比較用 5 タスク |
| `docs/claude_mem_security_tests.md` | secret leakage / memory poisoning テスト手順 |
| `docs/magika_poc_plan.md` | Magika PoC 計画 |
| `docs/cognee_poc_plan.md` | cognee 比較 PoC 計画 |
| `docs/open_agents_threat_model.md` | open-agents threat model |
| `docs/claude_mem_rollback_and_deps.md` | rollback 手順 / dependency review |

## リポジトリ

- GitHub: https://github.com/temunto-png/crypt.git（branch: master、HEAD = `566c065`）
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

## 次セッション引継ぎ（2026-04-17 時点）

### 優先順

#### 1. vector search 有効化（5分）

Claude Code を完全再起動すると SessionStart hook の `smart-install.js` が `~/.local/bin/uv` を自動検出する可能性が高い。

```bash
# 再起動後に確認
curl -sf http://localhost:37777/health
# MCP ツールで /mem-search "Binance" を試す
```

依然失敗する場合 → `~/.claude-mem/settings.json` に `CLAUDE_MEM_UV_PATH` の設定項目があるか確認し、`C:/Users/temun/.local/bin/uv.exe` を明示指定する。

#### 2. MP-01 / MP-03 完了（10分）

vector search 不可の場合は FTS で代用：

```bash
# 誤情報（Binance, id=6）を検索・削除
sqlite3 "C:/Users/temun/.claude-mem/claude-mem.db" \
  "SELECT id, title FROM observations WHERE narrative LIKE '%Binance%';"
sqlite3 "C:/Users/temun/.claude-mem/claude-mem.db" "DELETE FROM observations WHERE id=6;"
sqlite3 "C:/Users/temun/.claude-mem/claude-mem.db" "SELECT id FROM observations WHERE id=6;"
# → 何も返らなければ PASS
```

完了後、`docs/claude_mem_security_tests.md` と `docs/claude_mem_rollout_checklist.md` Phase 1 チェックを更新する。

#### 3. crypt Important A の修正

- **ファイル**: `src/cryptbot/live/engine.py` — `_poll_active_orders()` の result 処理ブロック
- **問題**: `TIMEOUT_CANCELLED` かつ `executed_amount > 0` のとき LiveState が更新されない。BUY が部分約定後にタイムアウトキャンセルされると `position_size = 0` のまま BTC が取引所に残る
- **修正箇所**: `CANCELED_PARTIALLY_FILLED` の elif チェーンの直後に追加
  ```python
  elif result.status == "TIMEOUT_CANCELLED" and result.executed_amount > 0:
      # CANCELED_PARTIALLY_FILLED と同じ LiveState 更新ロジック
  ```
- **追加テスト**: `test_timeout_cancelled_buy_with_partial_fill_updates_live_state`

#### 4. crypt Important B の修正

- **ファイル**: `src/cryptbot/execution/live_executor.py:119-127` — `place_order()` validation
- **問題**: `isinstance(raw_order_id, (int, float))` が `""` / `"0"` を通過させる
- **修正**:
  ```python
  if not raw_order_id or not str(raw_order_id).strip() or int(str(raw_order_id)) <= 0:
  ```
- **追加テスト**: `test_empty_string_order_id_results_in_submit_failed`

#### 5. master マージ

Important A / B 修正後:
```bash
cd C:/tool/claude/crypt
python -m pytest -q  # 657+ passed を確認
git add src/cryptbot/live/engine.py src/cryptbot/execution/live_executor.py tests/...
git commit -m "fix: TIMEOUT_CANCELLED LiveState update and empty order_id validation"
git checkout master
git merge feat/startup-recovery-hardening
git push origin master
```

---

## 次のアクション

1. ~~**P2 実装を開始**~~（完了）
2. ~~Codex ソースレビュー対応~~（完了 — F1〜F7 全対応済み）
3. ~~**OHLCV 自動投入**~~（完了 — OhlcvUpdater 実装・LiveEngine 注入・バックフィル追加）
4. ~~**P2（F4/F5/F6）実装**~~（完了 — commit d85de98・テスト 601 passed）
5. ~~**詳細敵対的レビュー対応（NF1〜NF7）**~~（完了 — commit 未 push）
6. ~~**ポジション同期（リカバリー）実装**~~（完了 — 638 passed）
7. ~~**起動リカバリー堅牢化（P1/P2/P3）**~~ — 8/8 タスク完了、残2件対応後 master マージ
8. **【次セッション】残 Important 2件を修正して master にマージ** → 本番起動準備

### 次セッションで対応する Important 2件

#### A: `TIMEOUT_CANCELLED` で LiveState が更新されない
- **場所**: `src/cryptbot/live/engine.py` — `_poll_active_orders()` の result 処理ブロック（`FULLY_FILLED` / `CANCELED_PARTIALLY_FILLED` の elif チェーン）
- **問題**: `handle_partial_fill()` が `status="TIMEOUT_CANCELLED"` を返しても engine 側に処理分岐がない。BUY が部分約定済みでタイムアウトキャンセルされた場合 `LiveState.position_size = 0` のままで BTC が取引所に残る
- **修正**: `CANCELED_PARTIALLY_FILLED` ブランチと同じ LiveState 更新ロジックを `elif result.status == "TIMEOUT_CANCELLED" and result.executed_amount > 0:` で追加
- **テスト**: `test_timeout_cancelled_buy_with_partial_fill_updates_live_state`

#### B: `exchange_order_id` 空文字列が検証をすり抜ける
- **場所**: `src/cryptbot/execution/live_executor.py:119-127` — `place_order()` validation
- **問題**: `isinstance(raw_order_id, (int, float))` チェックが文字列 `""` / `"0"` を通過させる
- **修正**: `if not raw_order_id or not str(raw_order_id).strip() or int(str(raw_order_id)) <= 0:`
- **テスト**: `test_empty_string_order_id_results_in_submit_failed`

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
