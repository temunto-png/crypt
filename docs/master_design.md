# 暗号資産自動売買システム マスター設計書

## 1. システム概要

10万円の現物 BTC/JPY 取引を対象とした、ルールベースの暗号資産自動売買システム。
MVP は paper trade までに限定し、live 注文は設計のみ行う。

### 目的と制約

- **最終目的**: 利益を上げること（ただし保証はしない）
- **初期目的**: 破滅的損失の回避、手数料込みでの検証、再現性の確認
- **取引所**: bitbank（MVP）
- **銘柄**: BTC/JPY 現物
- **初期資金**: 10万円
- **実行環境**: ローカル PC（RTX 4070 Ti SUPER 16GB VRAM, Ryzen 7 9800X3D, 32GB RAM）

### 強制ルール

1. MVP は paper trade までに限定する
2. Live 注文コードは明示的に要求されるまで有効化しない
3. API キーはコードに直書きしない、出金権限を付けない
4. 手数料・スプレッド・スリッページ込みの期待値を主指標にする
5. OHLCV だけのバックテスト結果を過信しない
6. 全売買判断に監査ログを残す
7. 取引しない判断を戦略の一部として扱う
8. モデルの予測値をそのまま注文に変換しない

## 2. アーキテクチャ

```
┌─────────────────────────────────────────────────┐
│                   Config Layer                   │
│            (YAML + .env + pydantic)              │
├─────────────────────────────────────────────────┤
│  Data Layer  │  Strategy Layer │   Risk Layer    │
│  - Fetcher   │  - BaseStrategy │  - Position     │
│  - Storage   │  - MA Cross     │  - Drawdown     │
│  - Normalizer│  - Momentum     │  - Kill Switch  │
│              │  - Mean Revert  │  - Daily Limit   │
├──────────────┼─────────────────┼─────────────────┤
│          Exchange Adapter (Abstract)             │
│    ┌──────────┐  ┌───────────┐  ┌────────────┐  │
│    │ Bitbank   │  │ GMO Coin  │  │ (Future)   │  │
│    └──────────┘  └───────────┘  └────────────┘  │
├─────────────────────────────────────────────────┤
│           Execution Layer                        │
│    ┌──────────────┐  ┌───────────────────────┐  │
│    │ Paper Engine  │  │ Live Engine (disabled)│  │
│    └──────────────┘  └───────────────────────┘  │
├─────────────────────────────────────────────────┤
│  Backtest Engine  │  Report Generator  │  Logs  │
└─────────────────────────────────────────────────┘
```

### 主要な設計判断

| 判断項目 | 選択 | 理由 |
|---------|------|------|
| データ保存 | SQLite + Parquet 併用（責務定義は下記参照） | SQLite: 注文・ログの構造化データ。Parquet: OHLCV の時系列データ（高速読み込み） |
| 設定管理 | YAML + pydantic（APIキーは環境変数のみ） | 型安全な設定読み込み、バリデーション付き |
| HTTP クライアント | httpx | async 対応、タイムアウト制御が容易 |
| ログ | loguru | 構造化ログ、回転、フィルタリングが簡潔 |
| テスト | pytest | Python 標準、フィクスチャとパラメタライズが強力 |
| 実行モード切替 | 環境変数 `TRADING_MODE` + CLI フラグの二段階ゲート | config.yaml の 1 行変更だけでは live 起動できない |
| APIキー管理 | pydantic `SecretStr` | str() 変換でマスクされ、ログへの平文漏洩を防ぐ |

### SQLite / Parquet の責務分離（S-I4）

| ストレージ | 対象データ | 理由 |
|-----------|-----------|------|
| **SQLite** | 注文・状態管理（`orders`, `order_events`）、監査ログ（`audit_log`）、設定スナップショット | 書き込みが頻繁、整合性・トランザクションが必要 |
| **Parquet** | OHLCV ヒストリカルデータ、バックテスト結果の時系列出力 | 追記のみ、分析用途、高速列ベース読み込み |

クエリが SQLite と Parquet を跨ぐ設計は禁止。各レイヤーの責務を超えるデータアクセスが発生した場合はアーキテクチャを再検討すること。

### Live 実行モードの二段階ゲート（S-C1）

`config.yaml` の `mode: live` 単独では live 注文を起動できない。以下の両方が揃った場合のみ有効化する:

```
Step 1: 環境変数を設定（セッション単位の明示的な宣言）
  export TRADING_MODE=live

Step 2: CLI フラグを渡して起動
  python -m crypt.main --confirm-live
  > Live 取引モードで起動しようとしています
  > 取引所: bitbank / 銘柄: BTC/JPY / 資金: {balance} JPY
  > 続行しますか？ [yes/NO]: yes
```

- `TRADING_MODE` 環境変数がない場合、`--confirm-live` フラグがあっても paper で起動する
- `--confirm-live` なしでは `TRADING_MODE=live` があっても live 起動を拒否し、警告ログを記録する
- テスト環境では `TRADING_MODE` を設定しないことで誤 live 起動を防ぐ

## 3. ディレクトリ構成

```
crypt/
  README.md
  .env.example
  config.example.yaml
  pyproject.toml
  docs/
    master_design.md          # 本ドキュメント
    research_plan.md
    exchange_comparison.md
    model_comparison.md
    risk_policy.md
    live_trade_design.md
  src/
    __init__.py
    config/
      settings.py             # pydantic Settings
    data/
      fetcher.py              # Public API データ取得
      storage.py              # SQLite/Parquet 保存・読み込み
      normalizer.py           # OHLCV 正規化
    exchanges/
      base.py                 # 抽象 Exchange adapter
      bitbank.py
      gmo.py
    strategies/
      base.py                 # BaseStrategy ABC
      buy_and_hold.py
      ma_cross.py
      momentum.py
      mean_reversion.py
      volatility_filter.py
    backtest/
      engine.py               # バックテストエンジン
      metrics.py              # 評価指標計算
    paper/
      engine.py               # Paper trade エンジン
      simulator.py            # 疑似約定シミュレータ
    risk/
      manager.py              # リスク管理
      kill_switch.py          # Kill switch
    execution/
      base.py                 # 抽象注文実行
      paper_executor.py       # Paper 注文
      live_executor.py        # Live 注文（設計のみ、無効化）
    models/                   # Phase 3 用（MVP では空）
    reports/
      generator.py            # レポート生成
    utils/
      logger.py               # loguru 設定
      time_utils.py
  tests/
    test_data/
    test_strategies/
    test_backtest/
    test_risk/
    test_exchanges/
  reports/                    # 生成されたレポート出力先
  logs/                       # ログ出力先
```

## 4. 取引所選定

**MVP 取引所: bitbank**

選定理由の詳細は [exchange_comparison.md](exchange_comparison.md) を参照。

要約:
1. API ドキュメントの品質が最も高い（GitHub 公開、日英対応）
2. OHLCV Candlestick API が充実（1min〜1month、日付指定可）
3. 注文タイプが豊富（stop, stop_limit, take_profit, stop_loss）
4. レート制限が明確（GET 10回/秒、POST 6回/秒）
5. ユーザーがアカウント保有済み

BTC/JPY 仕様:
- Maker 手数料: 0%
- Taker 手数料: 0.1%
- 最小注文数量: 0.0001 BTC
- 価格刻み: 1 JPY
- 数量刻み: 0.0001 BTC

## 5. 戦略設計

### 共通インターフェース

```python
class BaseStrategy(ABC):
    def __init__(self, config: StrategyConfig): ...

    @abstractmethod
    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """HOLD / BUY / SELL + confidence (0.0-1.0) を返す"""

    def should_skip(self, market_state: MarketState) -> bool:
        """ノートレード条件の判定"""
```

`Signal` は `direction` (BUY/SELL/HOLD), `confidence` (0.0-1.0), `reason` (str) を持つ。
全シグナルはリスク管理レイヤーを通過してから執行判断される。

### レジーム検出とシグナル選択

**重要**: トレンド戦略（MA Cross、Momentum）と平均回帰戦略（Mean Reversion）は
矛盾する市場仮説に基づく。同一レジームで両方を同時運用しない。

```python
class RegimeDetector:
    """ADX + ATR でレジームを分類する"""

    # 入力 DataFrame の期待スキーマ（S-I3）
    # 呼び出し元（DataPipeline）が事前に計算して渡す責任を持つ
    REQUIRED_COLUMNS = ["close", "high", "low", "atr", "sma_short", "sma_long"]
    # atr: ATR(14), sma_short: SMA(20), sma_long: SMA(50) — normalizer.py で計算済みであること

    def detect(self, data: pd.DataFrame) -> Regime:
        missing = [c for c in self.REQUIRED_COLUMNS if c not in data.columns]
        if missing:
            raise ValueError(f"RegimeDetector: 必要なカラムが不足: {missing}")

        adx = calculate_adx(data, period=14)
        atr_ratio = data["atr"] / data["atr"].rolling(20).median()

        if atr_ratio.iloc[-1] > 2.0:
            return Regime.HIGH_VOL  # 全戦略停止
        if adx.iloc[-1] > 25 and data["sma_short"].iloc[-1] > data["sma_long"].iloc[-1]:
            return Regime.TREND_UP
        if adx.iloc[-1] > 25 and data["sma_short"].iloc[-1] < data["sma_long"].iloc[-1]:
            return Regime.TREND_DOWN
        return Regime.RANGE
```

**入力スキーマの責務分担**:
- `normalizer.py` が OHLCV から `atr`, `sma_short`, `sma_long` を計算して DataFrame に付与する
- `RegimeDetector.detect()` はカラムの存在チェックのみ行い、計算はしない
- カラム名を変更する場合は `REQUIRED_COLUMNS` と `normalizer.py` を同時に更新する

**レジーム別の有効戦略とポジション処理**:

| レジーム | 有効戦略 | 無効戦略 | 既存ポジション処理 |
|---------|---------|---------|-----------------|
| TREND_UP | MA Cross または Vol Filter のどちらか一方, Momentum | Mean Reversion | 保持継続 |
| TREND_DOWN | MA Cross または Vol Filter のどちらか一方, Momentum | Mean Reversion | **即時クローズ**（Long-only のため下落トレンドは保有リスク大） |
| RANGE | Mean Reversion | MA Cross, Momentum, Vol Filter | 保持継続（Mean Reversion のエグジット条件に委ねる） |
| HIGH_VOL | なし（全停止） | 全戦略 | **即時成行クローズ**（急変相場での継続保有禁止） |

**TREND_DOWN / HIGH_VOL でのポジション即時クローズの詳細**:
- レジーム検出後、当該サイクル内でポジションクローズ注文を発行する
- クローズ注文は成行（Taker）で執行する。HIGH_VOL 時はスリッページが大きい可能性があるが、継続保有リスクより優先する
- クローズ後は新規エントリー禁止状態を維持する（次のレジーム判定まで）
- paper trade では記録のみ

MA Cross と Vol Filter の同時運用は相関が高いため禁止。どちらを使うかは
バックテストのパラメータ安定性分析の結果で選択する。

**ADX 遅延リスク**: ADX は14時間分の遅行指標。レジーム転換直後から ADX が
閾値を超えるまでの期間（最大14時間）は誤ったレジームで戦略が動作し続ける。
バックテストレポートに以下を必ず含める:
- レジーム転換イベントの特定（Trend↔Range の切替時点）
- 転換後 0〜14 時間の損益寄与率（「遅延コスト」の定量化）
- 遅延コストが全損益に占める割合が 20% を超える場合、ADX period を短縮するか
  確認ラグを設ける（例: ADX > 25 が 3時間連続した場合のみレジーム転換とみなす）

### MVP 戦略一覧

| 戦略 | 仮説 | 取引頻度 | 手数料耐性 | 失敗する相場 |
|------|------|---------|-----------|-------------|
| Buy & Hold | 長期上昇トレンド | 1回 | ◎ | 下落/レンジ |
| MA Cross | 短期MA > 長期MAでトレンド転換検出 | 月数回〜十数回 | ○ | レンジ（ダマシ） |
| Momentum | N期間リターンの継続性 | 中〜高 | △ | 急反転 |
| Mean Reversion (Long-only) | BB下限を下回った後の反発を捕捉。**現物取引のためショートは不可。BB上限超過時の売りはポジション解消のみ** | 低〜中 | ○ | 強トレンド、下落一方通行相場 |
| Vol Filter MA Cross | ATRフィルターでダマシ回避 | 低 | ◎ | 低ボラのじわじわトレンド |

各戦略の詳細パラメータ:

**MA Cross**: `short_period`: 20, `long_period`: 50, `min_ma_diff_pct`: 0.1%
**Momentum**: `lookback`: 14, `threshold`: 2%
**Mean Reversion (Long-only)**: `bb_period`: 20, `bb_std`: 2.0, `min_bandwidth_pct`: 1.0%
- BUY シグナル: `close < BB_lower` かつ `min_bandwidth_pct` 以上の帯域幅
- SELL シグナル: `close > BB_upper`（既存ロングのクローズのみ、新規ショートは行わない）
- 理論的制約: 本来の Mean Reversion は双方向（上昇しすぎ→ショート、下落しすぎ→ロング）だが、
  現物取引ではロング方向のみ。シグナルの半分（BB上限超過時のショート利益）が取れないため、
  期待値は完全な双方向実装の約半分になる可能性がある。これを承知の上でバックテストする。
**Vol Filter MA Cross**: MA Cross パラメータ + `atr_period`: 14, `atr_multiplier`: 1.0（閾値 = ATR の過去20期間中央値 × atr_multiplier）

### ノートレードの扱い

- 全てのノートレード判断をログに記録（`reason`, `market_state`, `timestamp`）
- ノートレード率を評価指標に含める
- ノートレード率 >90%: 戦略の有効性を疑う
- ノートレード率 <10%: フィルター不足を疑う

## 6. バックテスト設計

### データ

| 項目 | 値 |
|------|-----|
| データソース | bitbank Public API Candlestick |
| 時間足 | 1hour（主）、1day（補助） |
| 取得期間 | 最低1年、目標2〜3年 |
| 保存形式 | Parquet（OHLCV）、SQLite（メタデータ） |

1hour を主時間足にする理由: 手数料 0.1% に対して十分な値幅が期待できる、データ量が現実的。

### データ分割

```
全データ期間
├── 学習期間 (60%)  ── パラメータ最適化
├── 検証期間 (20%)  ── パラメータ選択
└── テスト期間 (20%) ── 最終評価（1回のみ使用）
```

### コスト模倣モデル

| コスト項目 | 想定値 |
|-----------|--------|
| Taker 手数料 | 0.1% |
| Maker 手数料 | 0% |
| スリッページ | 0.05% |
| **合計（保守値）** | **0.15%** |

バックテストでは全注文を Taker + スリッページ = 0.15% で計算する。

### ウォークフォワード検証

```
Window 1: [学習 M1-M6]  → [検証 M7-M8]
Window 2: [学習 M3-M8]  → [検証 M9-M10]
Window 3: [学習 M5-M10] → [検証 M11-M12]
```

各ウィンドウで独立にパラメータ最適化し、検証期間で評価。
特定期間でのみ勝てる戦略を排除する。

### 評価指標

主指標:
- **手数料・スリッページ込み期待値** > 0（bootstrap CI 下限 > 0 が必須）
- **最大ドローダウン** < 15%（bootstrap 95パーセンタイル推定値で評価）

副指標: Profit Factor > 1.3, Sharpe > 0.5（bootstrap CI 下限 > 0 必須）, Sortino > 0.7,
Calmar > 0.5, CVaR(95%) < -3%, 取引回数 > 100, 実効サンプルサイズ > 50,
手数料合計 < 総利益の30%

ベンチマーク比較: Buy & Hold、ランダムエントリー（Monte Carlo 1000回）

全指標リスト: 総損益、年率換算リターン、勝率、平均利益/損失、期待値（bootstrap CI付）、
Profit Factor、Sharpe Ratio（bootstrap CI付）、Sortino Ratio、Calmar Ratio、
CVaR(95%)、最大ドローダウン（観測値＋bootstrap推定値）、最大連敗数、
取引回数、実効サンプルサイズ、売買回転率、手数料込み損益、月次損益（二項検定）、
ノートレード率、破産確率（Monte Carlo）、レジーム別条件付き期待値、Maker執行率感度分析

### ベンチマーク設計

Buy & Hold のみでは「シグナルに価値があるか」を測れない。以下のベンチマークと比較する。

| ベンチマーク | 目的 | 実装 |
|------------|------|------|
| Buy & Hold | 市場全体のリターンとの比較 | 初日買い・最終日売り |
| **ランダムエントリー** | シグナルの付加価値検証 | 同一ポジションサイズ・保有期間でランダム選択、Monte Carlo 1000回、95パーセンタイルとの比較 |

ランダムエントリーの Sharpe 95パーセンタイルを下回る戦略は、
「ランダムよりも悪い」または「ランダムと区別できない」ため棄却する。

### OHLCVバックテストの限界と対策

| 限界 | 対策 |
|------|------|
| 板情報がない | 保守的スリッページ想定 + paper trade で検証 |
| ティック内の動きが不明 | 1hour 足を使い影響を緩和 |
| 流動性変動が反映されない | Paper trade で板情報ベースの疑似約定と比較 |
| スプレッド変動が反映されない | 固定 0.05% スリッページで保守的に |

## 7. Paper Trade 設計

### 実行サイクル

1hour ごとに以下を実行:
1. データ取得（ticker, depth, candlestick）
2. 市場状態チェック（スプレッド、流動性、ボラティリティ）
3. リスクチェック（日次損失、ドローダウン、連敗）
4. シグナル生成
5. 疑似約定（板情報ベース）
6. ポートフォリオ状態保存

### 疑似約定シミュレータ

- **成行注文**: 板を食い進み、加重平均約定価格を算出
- **指値注文**: best bid/ask との比較で約定判定（保守的: 同値は未約定扱い）
- 手数料: Taker 0.1% / Maker 0%
- スリッページ: 板の走査結果から実測

### バックテストとの乖離トラッキング

| 比較項目 | 許容乖離 |
|---------|---------|
| シグナル一致率 | > 90% |
| 平均約定価格の差 | < 0.1% |
| 総損益の差 | < 20% |
| 取引回数の差 | < 10% |

### ログ・監査設計

**構造化ログ**: loguru → JSON Lines
**監査ログ**: SQLite `audit_log` テーブル（追記専用、UPDATE/DELETE 禁止）

監査ログカラム: id, timestamp, event_type, strategy, direction, signal_confidence,
signal_reason, fill_price, fill_size, fee, slippage_pct, portfolio_value,
drawdown_pct, raw_market_data (JSON), **prev_hash** (TEXT)

event_type: signal / fill / risk_stop / kill_switch / no_trade

### 監査ログの改ざん耐性設計（SC-I2）

`audit_log` テーブルに **ハッシュチェーン** を付与し、レコードの追加・改ざんを検知可能にする:

```python
import hashlib, json

def compute_audit_hash(prev_hash: str, record: dict) -> str:
    """前レコードのハッシュ + 現レコードの内容から SHA256 を計算"""
    content = prev_hash + json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()

# 新規レコード挿入時:
# 1. 最新レコードの prev_hash を取得（初回は "GENESIS"）
# 2. 挿入するレコードのハッシュを計算
# 3. INSERT のみ実行（UPDATE/DELETE は application レベルで禁止）
```

**設計ルール**:
- `audit_log` テーブルへの `UPDATE` / `DELETE` は実装しない
- 改ざん検証は `python -m crypt.tools.verify_audit_log` コマンドで実行可能にする
- チェーン検証が失敗した場合、ログに `AUDIT_INTEGRITY_FAILURE` を記録して処理を停止する

## 8. リスク管理

詳細は [risk_policy.md](risk_policy.md) を参照。

要約:

| 制限 | 閾値 |
|------|------|
| 1取引最大損失 | 2% (2,000 JPY) |
| 日次最大損失 | 3% (3,000 JPY) |
| 週次最大損失 | 5% (5,000 JPY) |
| 月次最大損失 | 10% (10,000 JPY) |
| 最大ドローダウン | 15% → Kill switch |
| 連敗停止 | 5連敗 |
| 最大ポジション | 総資金の30% |
| 最大同時ポジション | 1 |

Kill switch 発動後は自動再開しない。

## 9. マーケットマイクロストラクチャー

Paper trade で収集・分析:

| データ | 取得元 | 分析内容 |
|--------|--------|---------|
| Bid/Ask スプレッド | depth API | 時間帯別分布、異常拡大の頻度 |
| 板の厚さ | depth 上位5段 | Best 付近の流動性 |
| 約定履歴 | transactions API | 約定頻度、大口注文の影響 |
| 実効スプレッド | 約定価格 vs mid price | 成行注文の実コスト |

市場環境による停止条件:
- スプレッド > 0.5%
- 板厚 < 0.01 BTC
- 1時間変動率 > 5%
- API エラー 連続3回
- API レスポンス > 5秒

## 10. ML 設計（Phase 3）

MVP では実装しない。Phase 3 で導入する際の方針:

- モデル出力 → `confidence_score` (0.0-1.0) に正規化
- `confidence_score` < 0.6 → シグナルとして使わない
- 既存ルールベース戦略のフィルターとして使用
- モデル劣化検知: 直近N日の予測精度低下でアラート
- GPU/CPU 自動判定、CPU fallback 対応
- 実験管理は YAML ベース（MLflow は過剰）

Kronos 含むモデル調査要件は [model_comparison.md](model_comparison.md) を参照。

## 11. Live Trade 設計（Phase 4、設計のみ）

詳細は [live_trade_design.md](live_trade_design.md) を参照。

注文状態 State Machine:
```
CREATED → SUBMITTED → PARTIAL_FILLED → FILLED
                   → REJECTED
                   → CANCELLED
           ↓
    SUBMIT_FAILED (API エラー)
```

二重発注防止、APIキー管理、手動停止手順を含む。

## 12. フェーズ別実装順序

```
Phase 0: docs/ + config + README
Phase 1: data/ + exchanges/ + strategies/ + backtest/ + risk/ + reports/ + tests/
Phase 2: paper/ + execution/ + tests/
Phase 3: models/ + tests/
Phase 4: docs/live_trade_design.md
```

## 13. 依存関係

```toml
[project]
dependencies = [
    "pandas>=2.0",
    "numpy>=1.24",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "httpx>=0.25",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
    "loguru>=0.7",
    "pyarrow>=14.0",
    "matplotlib>=3.7",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-asyncio>=0.21", "pytest-cov>=4.0"]
ml = ["torch>=2.0", "scikit-learn>=1.3"]
```

## 14. Live 移行条件

以下を全て満たすまで live 取引しない:

- [ ] バックテストで手数料・スリッページ込み期待値がプラス
- [ ] Buy & Hold よりリスク調整後成績が良い
- [ ] ウォークフォワード検証で大きく崩れていない
- [ ] Paper trade を最低2週間以上実施
- [ ] Paper trade で想定外の注文・残高不整合がない
- [ ] Kill switch が動作する
- [ ] 日次損失上限が動作する
- [ ] 最大ポジション制限が動作する
- [ ] API エラー時に停止する
- [ ] 全判断ログが保存される
- [ ] API キーに出金権限がない
- [ ] 手動停止手順が README にある
- [ ] ユーザーが明示的に live 移行を許可する

## 15. 関連ドキュメント

- [exchange_comparison.md](exchange_comparison.md) — 取引所 API 比較
- [research_plan.md](research_plan.md) — 研究方針・評価指標
- [risk_policy.md](risk_policy.md) — リスク管理方針
- [model_comparison.md](model_comparison.md) — モデル比較（Phase 3）
- [live_trade_design.md](live_trade_design.md) — Live trade 設計（Phase 4）
