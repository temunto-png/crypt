# 研究計画

## 研究方針

BTC/JPY 1hour 足の現物取引において、手数料・スリッページ込みで正の期待値を持つ
ルールベース戦略が存在するかを検証する。

### alpha 残存仮説（なぜ 2026 年時点でこの edge が残存しうるか）

テクニカル指標は伝統的市場では大半が裁定済みだが、BTC/JPY では以下の理由で
残存 alpha が存在しうると仮定する。これらは **反証可能な仮定** であり、
バックテスト結果が仮定を支持しない場合は戦略を棄却する。

**前提認識**: BTC/JPY の価格は BTC/USD にほぼ完全に連動する（相関 > 0.99）。
JPY 建て市場の参加者構造は BTC の price dynamics に対して支配的影響力を持たない。
したがって alpha 仮説は「BTC/JPY 固有」ではなく「BTC という資産クラス固有」で考える。

| 仮定 | 根拠 | 反証条件 |
|------|------|---------|
| BTC の価格形成は依然として感情駆動（FOMO/FUD）の成分が大きく、1時間足レベルではモメンタムが持続しやすい | 個人投資家の大量参加・SNS 起点のハーディングは BTC/USD でも観測されており、1時間足の自己相関がゼロでないことで確認できる | MA Cross / Momentum の期待値 ≤ 0、かつリターンの1時間ラグ自己相関が統計的に有意でない |
| 暗号資産特有の急騰・急落後に平均回帰が生じる（ただし現物 Long-only では下落からの反発のみ捕捉可能） | BTC の尖度が高く、Bollinger Band 逸脱後の反転は観測されている。**ただしショートができないため、上昇しすぎた場合の戻りは捕捉できない** | Mean Reversion（Long-only 版）の期待値 ≤ 0 |
| Maker 手数料 0% により、指値ベースの戦略では実質コストを大幅に削減できる | bitbank の Maker 手数料は 0%（Taker 0.1%） | Paper Trade での実測 Maker 執行率 < 50% |

**注意**: これらの仮定が成立しなければ、ルールベース戦略に優位性はない。
仮定の検証自体を研究の一環として位置づける。

### 研究仮説

| # | 仮説 | 検証方法 |
|---|------|---------|
| H1 | BTC/JPY 1hour 足にはトレンド継続性がある | MA Cross / Momentum の期待値 > 0（FDR 補正後） |
| H2 | 下落しすぎた価格は反発する（上昇方向のみ）。現物取引のためショート不可 | Mean Reversion (Long-only) の期待値 > 0（FDR 補正後）。BB上限超過のショートは不可 |
| H3 | ボラティリティフィルターでダマシを除去できる | Vol Filter 付き MA Cross が素の MA Cross を有意に上回る |
| H4 | 手数料 0.15%（Taker + スリッページ）を超える優位性がある | 全戦略で手数料込み期待値 > 0 |
| H5 | トレンド戦略と平均回帰戦略は異なる市場レジームで機能する | レジーム分類後の条件付き期待値が各レジームで正 |

### 前提

- 取引所: bitbank
- 銘柄: BTC/JPY 現物
- 時間足: 1hour（主）、1day（補助）
- 初期資金: 100,000 JPY
- コスト: Taker 0.1% + スリッページ 0.05% = 0.15%（保守値）

## 評価対象戦略

### MVP 戦略

1. **Buy & Hold** — ベンチマーク
2. **移動平均クロス (MA Cross)** — `short_period`: 20, `long_period`: 50
3. **モメンタム (Momentum)** — `lookback`: 14, `threshold`: 2%
4. **平均回帰 (Mean Reversion, Long-only)** — `bb_period`: 20, `bb_std`: 2.0
   - BUY: BB 下限を下回った後の反発を捕捉
   - SELL: BB 上限超過時はポジション解消のみ（新規ショートなし）
   - 理論的上限: 双方向実装の期待値の最大約50%
5. **ボラティリティフィルター付き MA Cross** — MA Cross + `atr_period`: 14

> **全戦略共通制約**: 現物 Long-only。下落一方通行相場では全戦略が Buy & Hold と近似する。

### Phase 3 追加候補

- Kronos 予測 + トレンドフィルター
- Kronos 予測 + ボラティリティフィルター
- LightGBM / XGBoost
- 複数モデル一致時のみ売買

## 評価期間と分割

### データ期間

bitbank Public API から取得可能な最長期間（目標2〜3年）の BTC/JPY 1hour OHLCV。

### データ分割

```
全データ期間
├── 学習期間 (60%)  ── パラメータ最適化に使用
├── 検証期間 (20%)  ── パラメータ選択に使用
└── テスト期間 (20%) ── 最終評価（1回のみ使用、パラメータ調整禁止）
```

### ウォークフォワード検証

```
Window 1: [学習 M1-M6]  → [検証 M7-M10]
Window 2: [学習 M5-M10] → [検証 M11-M14]
Window 3: [学習 M9-M14] → [検証 M15-M18]
...
```

- 学習窓: 6ヶ月
- 検証窓: **4ヶ月**（2ヶ月から変更）
- ステップ: **4ヶ月**（2ヶ月から変更）

**変更理由**: ステップ2ヶ月では隣接ウィンドウの学習データが75%重複し、
独立した検証が実質3〜4回しか得られない。ステップを4ヶ月に広げることで:
- 隣接ウィンドウ間の学習データ重複率: 75% → 33%
- 検証期間の重複: なし（各ウィンドウの検証期間が非重複）
- 独立した検証窓の数: 2年データで約3〜4窓 → 実質的な独立性が向上

**ウィンドウ数と独立性のトレードオフ**: 窓数が減るが、重複した擬似独立の
ウィンドウを多く持つより、真に独立した少数のウィンドウの方が統計的価値が高い。

各ウィンドウで独立にパラメータ最適化し、検証期間で評価。
採用基準: **全ての独立検証ウィンドウで期待値がプラス**。

## データリーク防止

- [ ] 未来データの参照がない（`data[:i+1]` のみ使用）
- [ ] テスト期間のデータでパラメータ調整をしていない
- [ ] 検証期間の結果を見てから学習期間のロジックを変えていない
- [ ] ウォークフォワードの各ウィンドウが時間的に前方のみ参照
- [ ] 全ての特徴量が当該時点で利用可能な情報のみから計算

### テスト期間の保護機構（S-C3）

テスト期間のデータに誤ってアクセスしないよう、以下の手順を取る:

1. 全データ取得後、テスト期間の開始インデックスを `config.yaml` に記録してから git commit
2. バックテストエンジンは `test_start_index` 以降のデータをデフォルトで除外する
3. 最終評価時のみ `--enable-test-set` フラグを明示的に渡して解除
4. `--enable-test-set` の実行は **1回のみ許容**。2回目以降は警告ログ + 実行禁止

```yaml
# config.yaml
backtest:
  test_start_index: null       # データ取得後に設定、変更禁止
  test_set_evaluated: false    # 最終評価後に true に変更、以降 --enable-test-set 不可
  test_set_data_hash: null     # テストデータ期間の SHA256（改ざん検知用）
```

#### 「1回のみ」の保証メカニズム

設定ファイルの書き換えだけでは再実行を防げないため、以下の多重チェックを実装する:

```python
def enable_test_set(config: BacktestConfig, data: pd.DataFrame) -> None:
    """テストセットを1回限りで有効化する。2回目以降は例外を発生させる"""

    # 1. test_set_evaluated フラグのチェック
    if config.test_set_evaluated:
        raise TestSetAlreadyEvaluatedError(
            "テストセットは既に評価済みです。再評価はバックテスト結果を無効化します。"
        )

    # 2. データハッシュの確認（テストデータが変わっていないことを確認）
    current_hash = compute_data_hash(data[data.index >= config.test_start_index])
    if config.test_set_data_hash is None:
        # 初回: ハッシュを記録
        config.test_set_data_hash = current_hash
    elif config.test_set_data_hash != current_hash:
        raise TestSetDataChangedError(
            f"テストデータが変更されています。期待: {config.test_set_data_hash}, "
            f"現在: {current_hash}"
        )

    # 3. config.yaml に test_set_evaluated = true を書き込み
    config.test_set_evaluated = True
    save_config(config)  # YAML に永続化

    # 4. git commit を促すメッセージを表示
    print(
        "[IMPORTANT] config.yaml の test_set_evaluated が true になりました。\n"
        "以下のコマンドで変更を commit してください:\n"
        "  git add config.yaml && git commit -m 'chore: mark test set as evaluated'"
    )

def compute_data_hash(df: pd.DataFrame) -> str:
    """データフレームの内容から SHA256 を計算（改ざん・データ変更検知用）"""
    import hashlib
    content = df.to_csv(index=True).encode()
    return hashlib.sha256(content).hexdigest()
```

**保証の多重層**:
- Layer 1: `test_set_evaluated` フラグ（config.yaml に永続化）
- Layer 2: テストデータの SHA256 ハッシュ（データ差し替えを検知）
- Layer 3: git commit の促し（変更履歴を残す）

**ウォークフォワードと holdout の関係**:
ウォークフォワードは学習・検証期間（全データの 80%）内でのみ実行する。
テスト期間（残り 20%）にはウォークフォワードのウィンドウを侵入させない。

## 特徴量設計

全て `close`, `high`, `low`, `volume` から算出。外部データは使わない。

| 特徴量 | 計算 | 使用戦略 |
|--------|------|---------|
| SMA_short | 単純移動平均 (20) | MA Cross, Vol Filter |
| SMA_long | 単純移動平均 (50) | MA Cross, Vol Filter |
| RSI | 14期間 RSI | 補助指標 |
| Bollinger Bands | 20期間, 2σ | Mean Reversion |
| ATR | 14期間 Average True Range | Vol Filter, リスク管理 |
| Momentum | N期間リターン | Momentum |
| Volume MA ratio | 現在出来高 / 出来高MA | フィルター |

### ラベル設計（Phase 3 ML 導入向け、MVP では未使用）

- バイナリ: N期間後リターン > 手数料閾値(0.15%) → 1, else 0
- 三値: UP(>0.15%) / DOWN(<-0.15%) / NEUTRAL

## レジーム別分析

トレンド戦略（MA Cross、Momentum）と平均回帰戦略（Mean Reversion）は
**矛盾する市場仮説** に基づく。レジーム検出なしに両戦略を同時運用すると
互いに打ち消し合い、手数料分だけ確実に負ける可能性がある。

### レジーム分類方法

各バーを以下のいずれかに分類する:

| レジーム | 定義 | 判定方法 |
|---------|------|---------|
| **Trend UP** | 強い上昇トレンド | ADX > 25 かつ SMA_short > SMA_long |
| **Trend DOWN** | 強い下落トレンド | ADX > 25 かつ SMA_short < SMA_long |
| **Range** | レンジ（トレンドなし） | ADX ≤ 25 |
| **High Vol** | 急変相場 | ATR / ATR_median > 2.0 |

ADX（Average Directional Index）: 14期間。`high`, `low`, `close` から算出。

### レジーム別の戦略適性

| 戦略 | Trend UP | Trend DOWN | Range | High Vol |
|------|---------|------------|-------|---------|
| MA Cross | ◎ | ◎ | × | △ |
| Momentum | ◎ | ◎ | × | × |
| Mean Reversion | × | × | ◎ | × |
| Vol Filter MA Cross | ◎ | ◎ | △ | × |
| Buy & Hold | ◎ | × | △ | × |

### 分析要件

- 全バックテスト期間のレジーム分布（何%が Trend/Range/High Vol か）を算出
- 戦略ごとにレジーム条件付き期待値を算出
- 戦略がそのレジームで動作した期間の割合を算出
- Live 移行前に Paper Trade でのレジーム分布が Backtest と大きく乖離していないか確認

### レジーム-戦略マッチングの過学習リスクと対策

「Trend 時は MA Cross が有効」というレジーム-戦略マッチング自体が、
学習・検証期間のデータから導出されている。これはメタレベルの過学習リスクを持つ。

**対策: マッチングルールをテスト期間で事前定義する**

1. ウォークフォワード（学習・検証期間）でレジーム別の条件付き期待値を算出
2. 「どのレジームでどの戦略を使うか」のルールを確定する
3. **このルールを git commit して変更禁止にしてからテスト期間を評価する**
4. テスト期間でマッチングルールをチューニングすることは禁止

マッチングルールがテスト期間で崩れた場合、「ウォークフォワードで発見したマッチングは
偽陽性だった」と判断し、レジーム検出なしのシンプルな運用に戻す。

## 評価指標

### 主指標（採用判断の根拠）

| 指標 | 採用基準 | 備考 |
|------|---------|------|
| **手数料・スリッページ込み期待値** | > 0（必須） | bootstrap 95% CI の下限 > 0 を要件とする |
| **最大ドローダウン** | < 15%（必須） | bootstrap 95パーセンタイル推定値で評価 |

### 副指標

| 指標 | 計算 | 目安 | 注意事項 |
|------|------|------|---------|
| 総損益 | 最終資金 - 初期資金 | プラス | — |
| 年率換算リターン | (1 + 総リターン)^(365/日数) - 1 | — | — |
| 勝率 | 利益トレード / 全トレード | 参考値のみ | 勝率単独では意味なし |
| 平均利益 / 平均損失 | — | 比率 > 1.5 | — |
| Profit Factor | 総利益 / 総損失 | > 1.3 | bootstrap CI を算出すること |
| **Sharpe Ratio** | (年率リターン - Rf) / 年率σ | > 0.5 | **bootstrap 95% CI を必ず算出。CI 下限 > 0 を要件とする。Newey-West 標準誤差を使用** |
| Sortino Ratio | (年率リターン - Rf) / 下方偏差 | > 0.7 | — |
| Calmar Ratio | 年率リターン / 最大DD | > 0.5 | — |
| **CVaR (95%)** | 下位5%のトレードリターンの平均損失 | < -3% / トレード | Sharpe が捕捉できない裾リスクを補完 |
| **最大ドローダウン（bootstrap）** | block bootstrap (block=20) で 1000回推定した 95パーセンタイル | < 15% | 観測値でなく bootstrap 推定値を主指標とする |
| 最大連敗数 | — | < 10 | — |
| **取引回数** | — | > 100（統計的有意性） | **N > 30 は i.i.d. 仮定下の理論値。自己相関・ボラティリティクラスタリングを考慮し、実効サンプルサイズを別途算出。最低 100 回を目安とする** |
| 売買回転率 | — | — | — |
| 手数料合計 | — | < 総利益の30% | — |
| **月次損益（二項検定）** | 月ごとの損益と検定 | 24ヶ月中 17ヶ月以上プラス（p < 0.1, 二項検定） | **「大半がプラス」ではなく二項検定で評価** |
| Buy & Hold との差 | Sharpe/Sortino 比較 | 上回る | **ランダムエントリーベンチマークとも比較する（下記参照）** |
| ノートレード率 | HOLD / 全判定 | 10%〜90% | — |
| **破産確率（Montecarlo）** | リターン分布から Monte Carlo 1000回シミュレーション | < 1% | **Gambler's Ruin 公式は i.i.d. 前提で不正確。重い裾を持つ実測分布からシミュレーションで推定する** |
| **リターンの自己相関** | Ljung-Box 検定（lag=10） | 採用/不採用の根拠には使わない。p < 0.05 の場合は実効サンプルサイズを補正するためのトリガーとして使用 | 自己相関があっても戦略として有効な場合がある（Momentum は定義上自己相関しやすい）。「自己相関あり → 不採用」ではなく「自己相関あり → block bootstrap の block_size を増やして CI を再計算」 |

### ランダムエントリーベンチマーク

Buy & Hold に加え、以下のベンチマークとも比較する:

- **Random Entry**: 同一ポジションサイズ・同一保有期間で、エントリー時刻をランダムに選択した戦略
  - 1000回 Monte Carlo シミュレーション
  - 各戦略の Sharpe が Random Entry の 95パーセンタイルを超えることを確認
  - 超えない場合、「シグナルに価値がなく、ただポジションを持っていただけ」と判断

### Sharpe Ratio の信頼区間計算方法

```python
def optimal_block_size(returns: np.ndarray) -> int:
    """
    Politis-Romano (1994) に基づく最適 block size 自動推定。
    固定 block_size=20 は BTC のボラティリティクラスタリング持続期間（数日〜数週間）
    に対して過小の可能性があるため、データから推定する。
    """
    try:
        from arch.bootstrap import optimal_block_length
        result = optimal_block_length(returns)
        return max(int(result["circular"].values[0]), 10)  # 最低 10 時間
    except ImportError:
        # arch ライブラリがない場合のフォールバック: 経験則
        # BTC 1時間足でのボラ持続期間の保守的推定 = 5日 = 120時間
        return 120

def sharpe_confidence_interval(returns: pd.Series, n_bootstrap: int = 2000) -> tuple[float, float]:
    """Block bootstrap で Sharpe の 95% CI を推定（block_size は自動推定）"""
    arr = returns.values
    block_size = optimal_block_size(arr)

    # ポイント推定
    annualize = np.sqrt(252 * 24)  # 1hour → 年率
    sharpe = arr.mean() / arr.std() * annualize

    # Circular block bootstrap
    n = len(arr)
    boot_sharpes = []
    for _ in range(n_bootstrap):
        starts = np.random.randint(0, n, size=n // block_size + 1)
        blocks = [np.take(arr, np.arange(s, s + block_size) % n) for s in starts]
        sample = np.concatenate(blocks)[:n]
        boot_sharpes.append(sample.mean() / sample.std() * annualize)

    return float(np.percentile(boot_sharpes, 2.5)), float(np.percentile(boot_sharpes, 97.5))
```

**全 bootstrap 処理で同一の `optimal_block_size()` を使う**（Sharpe CI、MDD bootstrap、FDR p-value で統一）。

## 採用基準

以下を**全て**満たす戦略を採用候補とする:

1. 手数料・スリッページ込み期待値がプラス（bootstrap CI 下限 > 0）
2. 最大ドローダウン bootstrap 95パーセンタイル推定値 < 15%
3. Buy & Hold **および** ランダムエントリーベンチマークよりリスク調整後成績（Sharpe / Sortino）が良い
4. ウォークフォワード検証で全ウィンドウの期待値がプラス
5. 取引回数 > 100、かつ実効サンプルサイズ（自己相関補正後）> 50
6. 手数料合計が総利益の 30% 以下
7. **Sharpe の bootstrap 95% CI 下限 > 0**（ゼロと有意に異なること）
8. **FDR 補正後 p-value < 0.1**（全テスト対象のうち偽陽性率を制御）
9. パラメータ安定性：最適パラメータの ±20% 近傍でも期待値がプラスを維持（下記参照）
10. 月次損益：二項検定 p < 0.1（24ヶ月中 ≥ 17 ヶ月プラス）

### 多重検定補正（FDR 制御）

5戦略 × 各3〜5パラメータセット = 多数の仮説を同時検定するため、
Benjamini-Hochberg 法で False Discovery Rate < 10% を保証する。

**重要**: BTC の1時間足リターンは非正規・自己相関ありのため、t-test の p-value は
信頼できない。FDR 補正に渡す p-value は **block bootstrap t-test** で算出する。

```python
from statsmodels.stats.multitest import multipletests
import numpy as np

def block_bootstrap_pvalue(returns: np.ndarray, block_size: int, n_boot: int = 2000) -> float:
    """
    帰無仮説: 期待値 = 0 の下での block bootstrap t-test p-value
    BTC リターンの非正規性・自己相関に対応
    """
    observed_mean = returns.mean()
    # 帰無仮説中心化: 平均をゼロにシフト
    centered = returns - observed_mean

    boot_means = []
    n = len(centered)
    for _ in range(n_boot):
        # circular block bootstrap
        start = np.random.randint(0, n, size=n // block_size)
        blocks = [centered[i:i + block_size] for i in start]
        sample = np.concatenate(blocks)[:n]
        boot_means.append(sample.mean())

    # 両側 p-value
    p_value = np.mean(np.abs(boot_means) >= np.abs(observed_mean))
    return p_value

# 全戦略 × パラメータセットの p-value を block bootstrap で収集
p_values = [block_bootstrap_pvalue(strategy_returns, block_size=opt_block_size)
            for strategy_returns in all_strategy_returns]
rejected, p_corrected, _, _ = multipletests(p_values, alpha=0.1, method="fdr_bh")
# rejected[i] = True の戦略のみ採用候補
```

## 不採用基準

以下のいずれかに該当する戦略は不採用:

1. バックテストでしか勝てない（ウォークフォワードで崩れる）
2. 売買回数が多すぎて手数料負けする
3. スプレッドに弱い（期待値の大部分がスプレッド以下）
4. 最大ドローダウン bootstrap 推定値が 15% を超える
5. 特定期間でのみ有効（レジーム依存が強すぎる）
6. ノートレード率が 90% を超える（実質的に機能していない）
7. ノートレード率が 10% 未満（フィルターが機能していない）
8. 破産確率 Monte Carlo 推定値が 1% を超える
9. Sharpe bootstrap CI 下限 ≤ 0（ゼロと有意に異ならない）
10. FDR 補正後に棄却されない（偽陽性の可能性）

## パラメータ安定性分析

最適パラメータが局所的な過学習でないことを確認する。

### 方法

最適パラメータの周辺で期待値をグリッド評価し、「台地（plateau）」の存在を確認する。
急峻なピークは curve-fitting の証拠。

| 戦略 | 主要パラメータ | 評価範囲 |
|------|-------------|---------|
| MA Cross | short_period, long_period | ±20% 刻み5段階 |
| Momentum | lookback, threshold | ±20% 刻み5段階 |
| Mean Reversion | bb_period, bb_std | ±20% 刻み5段階 |
| Vol Filter | atr_period, atr_multiplier | ±20% 刻み5段階 |

### 安定性の判定基準

- 最適パラメータの ±20% 範囲内で期待値がプラスを維持していること
- 期待値の変化幅が ±50% 以内であること（急変しない）
- 範囲内の80%以上のパラメータ組み合わせで期待値 > 0

## 戦略間相関分析

5戦略のシグナルが高度に相関する場合、実質的な多様化効果がなく
多重検定の問題がさらに深刻になる。

### 分析内容

シグナル値（BUY=1, SELL=-1, HOLD=0）の相関は「同時にシグナルを出すか」を測るに過ぎない。
より重要な指標は **「同じ局面で損益が相関するか」**（ホールディング期間リターンの相関）。

**測定すべき2種類の相関**:

1. **シグナル相関**: 各バーでの Signal.direction のペア相関係数（補助情報として算出）
2. **リターン相関（主）**: 各トレードのホールディング期間リターンのペア相関係数
   - 各戦略のトレード期間を `(entry_bar, exit_bar, return_pct)` として記録
   - 同一期間にポジションを持っていたトレード間のリターンを比較
   - 相関 > 0.7 のペアは「同じリスクを取っている」と判断

```python
# ホールディング期間リターン相関の算出例
# 全期間を1時間バーのリターン時系列として表現
strategy_bar_returns = {}
for strategy in strategies:
    # ポジションを持っている時間帯は実際のリターン、それ以外は 0
    bar_returns = np.where(position_held, bar_return, 0.0)
    strategy_bar_returns[strategy.name] = bar_returns

# 全戦略のリターン時系列の相関行列
corr_matrix = pd.DataFrame(strategy_bar_returns).corr()
# ヒートマップをレポートに含める
```

### 共存ルール

- **ホールディング期間リターン相関 > 0.7 のペアは同時運用しない**
- Trend レジーム: MA Cross または Vol Filter のどちらか一方のみ
- Range レジーム: Mean Reversion のみ
- 無条件で「5戦略を同時に走らせる」設計は禁止

## 執行 alpha（Maker/Taker の最適化）

Maker 手数料 0% vs Taker 手数料 0.1% の差は、往復 0.2% の執行コスト差を生む。
戦略の gross alpha が 0.3% 程度と仮定すると、Maker 執行の可否で期待値の正負が反転しうる。

### Maker 執行戦略の検討

| 注文タイプ | 手数料 | 執行リスク |
|-----------|--------|-----------|
| 成行（Taker） | 0.1% | 確実に約定 |
| 指値（Maker） | 0% | 未約定リスクあり |
| 指値（Post Only） | 0% | Taker になる場合はキャンセル |

### バックテストでの評価方法

1. **Taker 前提**（現行）: 全注文 0.1% で計算
2. **Maker 前提**: 全注文 0% で計算（楽観的上限）
3. **現実的混合**: Maker 執行率を 50%/70%/90% と仮定した感度分析

```
執行 alpha の感度分析テーブルをレポートに含める
コスト仮定: Taker のみ 0.15% / Maker 50% 混合 0.075% / Maker 70% 混合 0.045%
```

Paper Trade で実際の Maker 執行率を測定し、バックテストの仮定を検証する。

## 報告

### バックテストレポート (`reports/backtest_report.md`)

- 全戦略の評価指標比較テーブル（bootstrap CI 付き）
- 戦略ごとの月次損益（二項検定 p-value 付き）
- 最大ドローダウン期間（観測値 + bootstrap 95%ile）
- Buy & Hold および ランダムエントリーベンチマーク比較
- ウォークフォワード検証結果（独立ウィンドウごとの期待値）
- レジーム別条件付き期待値テーブル
- **ADX 遅延コスト分析**（転換後 0〜14h の損益寄与率）
- 戦略間ホールディング期間リターン相関行列（ヒートマップ）
- Maker/Taker 執行 alpha 感度分析テーブル
- FDR 補正後の p-value テーブル（採用/不採用の根拠）
- パラメータ安定性プロット（±20% 近傍の期待値面）
- 採用候補と不採用理由

### Paper Trade レポート (`reports/paper_trade_report.md`)

- 総損益、勝率、期待値
- バックテストとの乖離分析
- 停止条件の発動履歴
- ノートレード率と理由分布
- 板情報ベースの実効スプレッド統計
- 改善点
