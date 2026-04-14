# Progress Log — 暗号資産自動売買システム

## Session 2026-04-14

### 完了
- 設計ドキュメント3ラウンドレビュー（リスクマネージャー・SWE・セキュリティエンジニア視点）
  - Critical 7件、Important 12件を特定・修正
  - 修正ファイル: risk_policy.md / master_design.md / live_trade_design.md / research_plan.md
- 実装計画（task_plan.md）・知見ファイル（findings.md）・進捗ファイル（progress.md）作成

### 主要な修正内容
| ID | ファイル | 変更 |
|----|---------|------|
| R-C1 | risk_policy.md | Circuit Breaker を `current_balance * pct` 動的計算に変更 |
| R-C2 / SC-C1 | risk_policy.md | Kill Switch 解除を env + CLI 二段階に変更、失敗後 MANUAL_INTERVENTION_REQUIRED 状態を追加 |
| R-I2 | risk_policy.md | 連敗 = ラウンドトリップ単位と明示 |
| R-I3 | risk_policy.md | CVaR 計算ウィンドウ・更新頻度・段階対応（-3%/-4%/-5%）を定義 |
| S-C1 | master_design.md | live 起動を `TRADING_MODE=live` env + `--confirm-live` CLI 二段階ゲートに変更 |
| S-I4 | master_design.md | SQLite（状態・注文・監査）と Parquet（OHLCV）の責務分離を明示 |
| R-I1 / R-I4 | master_design.md | HIGH_VOL / TREND_DOWN で既存ポジションを即時成行クローズと明記 |
| S-I3 | master_design.md | RegimeDetector.REQUIRED_COLUMNS と normalizer.py の計算責務を定義 |
| SC-I2 | master_design.md | 監査ログに SHA256 ハッシュチェーンを設計 |
| SC-I3 | live_trade_design.md | APIキーを pydantic SecretStr で管理するコード例を追加 |
| SC-C2 | live_trade_design.md | sanitize_error() 関数でAPIキーパターンをマスクしてDB記録 |
| SC-I1 | live_trade_design.md | .env パーミッション 600 / git-secrets を手順書に追加 |
| SC-I4 | live_trade_design.md | 外部アラートのペイロードをホワイトリストに限定 |
| S-C2 | live_trade_design.md | place_order を BEGIN EXCLUSIVE + UNIQUE 制約で TOC/TOU 防止 |
| S-I1 | live_trade_design.md | PARTIAL_FILLED の完全ライフサイクル（KS発動時含む）を定義 |
| S-I2 | live_trade_design.md | 非同期をシングルループ + asyncio.Queue (Producer/Consumer) に設計 |
| S-C3 | research_plan.md | テスト期間保護を test_set_evaluated + データハッシュ + git commit の三重チェックに強化 |

### 現在の状態
- 設計フェーズ完了（全3ラウンドレビュー + 修正済み）
- 実装計画（task_plan.md）確定
- コードは未実装（docs/ + 計画ファイルのみ）
- Phase 0 Task 0-1 から実装開始可能

### 次のアクション
- Phase 0 Task 0-1: pyproject.toml / .gitignore / .env.example / config.example.yaml / README.md
- Phase 0 Task 0-2: src/config/settings.py（pydantic Settings + SecretStr）
- Phase 0 Task 0-3: src/utils/logger.py（sanitize_error / JSON Lines）

---

## Session 2026-04-14（敵対的レビュー対応）

### 完了
敵対的レビュー（`claude_code_review_handoff.md`）で指摘された P0/P1/P2 全7件を修正。

| ID | 内容 | 変更ファイル |
|----|------|------------|
| P0-01 | YAML設定が読み込まれない — `customise_sources` → `settings_customise_sources`（pydantic-settings v2 正規フック） | `config/settings.py`, `test_config/test_settings.py` |
| P0-02 | README と実装の乖離 — paper engine 未実装を明記、audit log コマンドに `--db` 追記、live 手順の env 変数修正 | `README.md` |
| P1-01 | CVaR half 制御が無効 — `pending_cvar_action` を state 変数として保持し `_execute_pending` に渡す。`get_cvar_action([])` を削除 | `backtest/engine.py`, `test_backtest/test_engine.py` |
| P1-02 | Kill Switch 復元が呼ばれない — `KillSwitch.__init__` で `load_state()` を自動呼び出し。DB 未初期化時は `OperationalError` をキャッチして inactive 継続 | `risk/kill_switch.py`, `test_risk/test_risk.py` |
| P1-03 | 監査ログ fail-open — `kill_switch.activate()` の直接呼び出し経路が fail-closed であることをテストで明示 | `test_risk/test_risk.py` |
| P1-04 | Live readiness が CVaR 閾値を見ていない — `check_live_readiness` に `cvar_warn_pct` パラメータ追加（デフォルト `-3.0%`） | `backtest/benchmark.py`, `test_backtest/test_benchmark.py` |
| P2-01 | `(pair, side)` ユニーク制約 — `orders_active_unique` を `(pair)` 単位に変更。既存 DB へのマイグレーション追加 | `data/storage.py`, `test_data/test_storage.py` |

### テスト結果
- `python -m pytest -q`: **359 passed**（修正前 346、追加 +13 件）

### 現在の状態
- 敵対的レビュー指摘事項 P0/P1/P2 全件修正済み
- paper engine・live engine は引き続き未実装（fail-closed）
- 次は paper engine 実装（`src/cryptbot/paper/`）か、追加バックテスト改善

### 次のアクション
- ~~paper engine 実装~~ → **完了**（下記 Session 参照）
- Live readiness チェックへの `CvarSettings.warn_pct` 自動連携（現在は手動で `cvar_warn_pct=-3.0` 指定）

---

## Session 2026-04-14（paper engine 実装）

### 完了
paper engine を実装（`src/cryptbot/paper/`）。

| 変更 | 内容 |
|------|------|
| `src/cryptbot/paper/__init__.py` | 新規（空） |
| `src/cryptbot/paper/state.py` | `PaperState` dataclass + `PaperStateStore`（SQLite I/O） |
| `src/cryptbot/paper/engine.py` | `PaperEngine`（cron-friendly, 1 bar per invocation） |
| `src/cryptbot/backtest/engine.py` | `calc_trade_record` / `calc_mark_balance` / `apply_regime_override` をモジュールレベル関数に切り出し（PaperEngine と共有） |
| `src/cryptbot/config/settings.py` | `PaperSettings` + `_StrategyName` Literal 型追加 |
| `src/cryptbot/main.py` | paper モードを `PaperEngine.run_one_bar()` に接続 |
| `tests/test_paper/` | state / engine / consistency テスト 27 件追加 |
| `tests/test_main/test_main.py` | paper mode テスト更新（OHLCV データ準備） |

### テスト結果
- `python -m pytest -q`: **387 passed**（実装前 359、追加 +28 件）

### 設計上の重要決定
- **cron-friendly**: 1 invocation = 1 bar。状態は `paper_state` テーブル (id=1 の単一行 UPSERT) に永続化
- **バックテスト一致**: 同一 OHLCV・戦略・設定で `BacktestEngine` と最終残高が一致することを consistency テストで検証済み
- **HOLD pending 最適化**: HOLD シグナルは pending として保存しない（DB 汚染防止）
- **fail-closed**: audit_log への fill 記録は例外を上位に伝播（orders テーブルは fail-open）
- **ライブデータ取得は今回スコープ外**: `Storage.load_ohlcv()` から既存 Parquet を読む。データ更新は別 cron

### 現在の状態
- paper engine 実装完了・動作確認済み
- `python -m cryptbot.main --db data/paper.db --data-dir data/` で起動可能
- live engine は引き続き未実装（fail-closed）

### 次のアクション
- OHLCV データ投入スクリプト（bitbank API から取得）
- Live readiness チェックへの `CvarSettings.warn_pct` 自動連携
- live engine 実装（paper engine 完了後の次フェーズ）
