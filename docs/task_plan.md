# 暗号資産自動売買システム 実装計画

## 目標
BTC/JPY 現物 Long-only の自動売買システムを実装する。
MVP は paper trade まで。live 注文コードは設計のみ（Phase 4）。

## 参照ドキュメント
- `docs/master_design.md` — アーキテクチャ・戦略・バックテスト設計
- `docs/risk_policy.md` — リスク管理（動的 Circuit Breaker・Kill Switch）
- `docs/research_plan.md` — 研究方針・評価指標・FDR 補正
- `docs/live_trade_design.md` — live 設計（二段階ゲート・sanitize_error 等）
- `docs/exchange_comparison.md` — bitbank API 仕様
- `docs/model_comparison.md` — Phase 3 ML 候補

## 実装フェーズ

---

## Phase 0: プロジェクト基盤 [pending]

**目標**: 実行可能なプロジェクト骨格を作る。他フェーズの前提。

### Task 0-1: プロジェクト設定ファイル [pending]
- [ ] `pyproject.toml` — 依存関係定義（pandas, numpy, pydantic, httpx, loguru, pyarrow 等）
- [ ] `.env.example` — API キーのテンプレート（SecretStr 対応の説明付き）
- [ ] `.gitignore` — .env, __pycache__, *.pyc, reports/, logs/, *.db, *.parquet
- [ ] `config.example.yaml` — 全設定項目のサンプル（mode: paper デフォルト）
- [ ] `README.md` — セットアップ手順・手動停止手順・live 移行条件チェックリスト

### Task 0-2: pydantic 設定（Settings）[pending]
- [ ] `src/config/settings.py` — pydantic BaseSettings
  - `bitbank_api_key: SecretStr`、`bitbank_api_secret: SecretStr`
  - `mode: Literal["paper", "live"] = "paper"`
  - Circuit Breaker パーセンテージ（動的計算の元になる率）
  - Kill Switch 設定（`kill_switch.active`）
  - `backtest.test_start_index`、`backtest.test_set_evaluated`、`backtest.test_set_data_hash`
- [ ] `src/__init__.py`

### Task 0-3: ロガー設定 [pending]
- [ ] `src/utils/logger.py` — loguru の JSON Lines 設定
  - `sanitize_error(e: Exception) -> str` — APIキーパターンマスク
  - 監査ログ用ハンドラ（SQLite 書き込み専用）
- [ ] `src/utils/time_utils.py` — タイムゾーン変換、1hour アライン

**完了条件**: `python -m pytest` が 0 エラーで起動する

---

## Phase 1: コアコンポーネント [pending]

**目標**: バックテストが動く状態を作る。

### Task 1-1: データ層 [pending]
ファイル作成順: `storage.py` → `fetcher.py` → `normalizer.py`

- [ ] `src/data/storage.py`
  - SQLite: `orders`, `order_events`, `audit_log`（ハッシュチェーン）, `market_state` テーブル
  - Parquet: OHLCV 時系列の読み書き
  - 責務: SQLite = トランザクション・状態管理 / Parquet = OHLCV（分析用）
  - `audit_log` は INSERT のみ（UPDATE/DELETE 実装しない）
  - `compute_audit_hash(prev_hash, record) -> str`
- [ ] `src/data/fetcher.py`
  - bitbank Public API: Candlestick (1hour, 1day)、ticker、depth、transactions
  - httpx async、タイムアウト 5秒、リトライ 3回
  - レート制限: GET 10回/秒
- [ ] `src/data/normalizer.py`
  - OHLCV → `sma_short(20)`, `sma_long(50)`, `atr(14)`, RSI, BB, Momentum, Volume MA ratio を付与
  - `RegimeDetector.REQUIRED_COLUMNS` を満たすカラムを計算する責務を持つ

### Task 1-2: 取引所アダプタ [pending]
- [ ] `src/exchanges/base.py` — 抽象 ExchangeAdapter ABC
- [ ] `src/exchanges/bitbank.py` — Private API（注文・残高・板）
- [ ] `src/exchanges/gmo.py` — スタブ実装（将来用）

### Task 1-3: 戦略層 [pending]
- [ ] `src/strategies/base.py`
  - `BaseStrategy(ABC)` — `generate_signal(data) -> Signal`、`should_skip(market_state) -> bool`
  - `Signal`: direction (BUY/SELL/HOLD), confidence (0.0-1.0), reason (str)
  - `RegimeDetector` — `REQUIRED_COLUMNS` チェック、`detect(data) -> Regime`
  - Regime: TREND_UP, TREND_DOWN, RANGE, HIGH_VOL
  - HIGH_VOL / TREND_DOWN → 既存ポジション即時クローズフラグを返す
- [ ] `src/strategies/buy_and_hold.py`
- [ ] `src/strategies/ma_cross.py` — short_period=20, long_period=50
- [ ] `src/strategies/momentum.py` — lookback=14, threshold=2%
- [ ] `src/strategies/mean_reversion.py` — Long-only (BB 下限 BUY / 上限 SELL = クローズのみ)
- [ ] `src/strategies/volatility_filter.py` — MA Cross + ATR フィルター

### Task 1-4: リスク管理 [pending]
- [ ] `src/risk/manager.py`
  - 動的 Circuit Breaker: `current_balance * pct` で毎サイクル再計算
  - 連敗カウント: ラウンドトリップ単位
  - CVaR(95%) 計算（直近50トレード）、段階的対応（警告/半減/停止）
  - 市場環境停止条件（スプレッド、板厚、ボラ、API エラー）
- [ ] `src/risk/kill_switch.py`
  - `MANUAL_INTERVENTION_REQUIRED` 状態の実装
  - 解除: 環境変数 `KILL_SWITCH_OVERRIDE=1` + CLI `--override-kill-switch` + 対話確認
  - 失敗時の緊急アラートメッセージ（手動クローズ手順付き）

### Task 1-5: バックテストエンジン [pending]
- [ ] `src/backtest/engine.py`
  - コスト模倣: Taker 0.1% + スリッページ 0.05% = 0.15%
  - ウォークフォワード: 学習6ヶ月、検証4ヶ月、ステップ4ヶ月
  - テスト期間保護: `--enable-test-set` 1回限り（`test_set_evaluated` + データハッシュ）
  - `enable_test_set()` / `compute_data_hash()` 実装
  - データリーク防止チェック（未来参照ガード）
- [ ] `src/backtest/metrics.py`
  - `optimal_block_size(returns)` — Politis-Romano 推定
  - `sharpe_confidence_interval(returns, n_bootstrap=2000)` — Circular block bootstrap
  - `block_bootstrap_pvalue(returns, block_size)` — FDR 補正用 p-value
  - CVaR(95%)、MDD bootstrap、Profit Factor、Sortino、Calmar
  - ランダムエントリーベンチマーク（Monte Carlo 1000回）
  - `multipletests(p_values, method="fdr_bh")` による FDR 補正

### Task 1-6: レポート生成 [pending]
- [ ] `src/reports/generator.py`
  - 全戦略の評価指標比較テーブル（bootstrap CI 付き）
  - ADX 遅延コスト分析
  - 戦略間ホールディング期間リターン相関行列
  - Maker/Taker 感度分析テーブル
  - FDR 補正後 p-value テーブル
  - パラメータ安定性プロット

### Task 1-7: テスト（Phase 1）[pending]
- [ ] `tests/test_data/` — fetcher mock、normalizer 出力カラム検証
- [ ] `tests/test_strategies/` — 各戦略のシグナル生成、RegimeDetector カラム不足エラー
- [ ] `tests/test_backtest/` — テスト期間保護（2回目で例外）、metrics 計算
- [ ] `tests/test_risk/` — 動的 Circuit Breaker、CVaR 段階対応、Kill Switch 状態遷移

**完了条件**: `pytest tests/ -v` が全通過、バックテストが BTC/JPY 1hour で実行完了

---

## Phase 2: Paper Trade [pending]

**目標**: リアルタイムで 1hour サイクルの paper trade が動く状態を作る。

### Task 2-1: 非同期イベントループ [pending]
- [ ] `src/paper/engine.py`
  - シングルループ + `asyncio.Queue` (signal_q)
  - サイクル順序: Fetch → State → Risk → Regime → Position → Signal → Execute → Persist
  - HIGH_VOL / TREND_DOWN での即時クローズ処理
- [ ] `src/paper/simulator.py`
  - 板情報ベースの疑似約定（成行: 板走査、指値: best bid/ask 比較）
  - 手数料: Taker 0.1% / Maker 0%
  - スプレッド: 実測値

### Task 2-2: 執行層 [pending]
- [ ] `src/execution/base.py` — 抽象 Executor
- [ ] `src/execution/paper_executor.py` — Paper 注文（Simulator 経由）
- [ ] `src/execution/live_executor.py` — **スケルトンのみ**（デフォルト import 禁止）
  - `PARTIAL_FILLED` 完全ライフサイクル（ポーリング・タイムアウト・KS 発動時）
  - 二重発注防止: `BEGIN EXCLUSIVE` トランザクション + UNIQUE 制約
  - `sanitize_error()` でエラー記録
  - `SecretStr` によるAPIキー管理

### Task 2-3: Live 二段階ゲート [pending]
- [ ] `src/__main__.py` (または `src/main.py`)
  - `TRADING_MODE=live` 環境変数 + `--confirm-live` CLI フラグの両方を要求
  - 対話確認プロンプト
  - `KILL_SWITCH_OVERRIDE` + `--override-kill-switch` の kill switch 解除フロー

### Task 2-4: テスト（Phase 2）[pending]
- [ ] `tests/test_paper/` — Simulator 疑似約定ロジック、サイクル実行
- [ ] `tests/test_execution/` — 二重発注防止（EXCLUSIVE トランザクション）、live_executor mock
- [ ] バックテストとの乖離検証スクリプト

### Task 2-5: 監査ログ検証ツール [pending]
- [ ] `src/tools/verify_audit_log.py` — ハッシュチェーン整合性チェック
  - `python -m crypt.tools.verify_audit_log` で実行可能

**完了条件**:
- Paper trade が 24時間以上エラーなく連続稼働する
- バックテストとの乖離が許容範囲内（シグナル一致率 >90%、損益差 <20%）
- 全 live 移行条件チェックリスト（master_design.md §14）を paper で確認済み

---

## Phase 3: ML 拡張 [pending — MVP 後]

**目標**: ルールベース戦略のフィルターとして ML モデルを追加する。

- Kronos / LightGBM / XGBoost の調査（model_comparison.md 参照）
- `confidence_score < 0.6` → シグナル無効化
- モデル劣化検知

---

## Phase 4: Live Trade [pending — 設計のみ]

**目標**: live_trade_design.md の設計を実装する。

live 移行条件（master_design.md §14）を**全て**満たした後のみ着手。
ユーザーの明示的な許可なしには実装しない。

---

## エラーログ

| エラー | 試行回数 | 解決策 |
|-------|---------|-------|
| — | — | — |

## 重要な設計判断

| 項目 | 決定 |
|------|------|
| Live ゲート | `TRADING_MODE=live` env + `--confirm-live` CLI |
| Kill Switch 解除 | `KILL_SWITCH_OVERRIDE=1` env + `--override-kill-switch` + 対話確認 |
| Circuit Breaker | 固定額ではなく `current_balance * pct` で動的計算 |
| 連敗定義 | ラウンドトリップ（エントリー〜エグジット）単位 |
| APIキー管理 | pydantic `SecretStr` |
| エラー記録 | `sanitize_error(e)` 経由（APIキーパターンをマスク） |
| 二重発注防止 | SQLite `BEGIN EXCLUSIVE` + UNIQUE 制約 |
| テスト期間保護 | `test_set_evaluated` フラグ + データ SHA256 ハッシュ + git commit |
| 監査ログ | append-only + SHA256 ハッシュチェーン |
| SQLite/Parquet | SQLite = 状態・注文・監査 / Parquet = OHLCV のみ |
| 非同期設計 | シングルループ + asyncio.Queue (Producer/Consumer) |
| RegimeDetector | REQUIRED_COLUMNS チェック + normalizer.py が計算責務を持つ |
| CVaR | 直近50トレード、段階的対応（-3%警告/-4%半減/-5%停止） |
| HIGH_VOL/TREND_DOWN | 既存ポジション即時成行クローズ |
