# Findings — 暗号資産自動売買システム

## 設計ドキュメント状態（2026-04-14 時点）

3ラウンドのレビューを経て、設計ドキュメント6ファイルが確定済み。

### 確定した設計判断

#### アーキテクチャ
- Python + asyncio（シングルイベントループ）
- SQLite（状態管理・監査ログ）+ Parquet（OHLCV・分析）の厳密な責務分離
- httpx（async HTTP）、loguru（JSON Lines）、pydantic v2（設定）

#### 戦略
- 5戦略: Buy & Hold, MA Cross, Momentum, Mean Reversion (Long-only), Vol Filter MA Cross
- RegimeDetector (ADX+ATR) で排他的戦略選択（TREND_UP/DOWN: MA系、RANGE: 平均回帰、HIGH_VOL: 全停止）
- HIGH_VOL / TREND_DOWN 検出時は既存ポジションを即時成行クローズ

#### バックテスト
- WF: 学習6ヶ月 / 検証4ヶ月 / ステップ4ヶ月（重複率33%）
- コスト: Taker 0.1% + スリッページ 0.05% = 0.15%
- 統計: block bootstrap (Politis-Romano 自動推定) + FDR 補正 (BH法, block bootstrap p-value)
- テスト期間保護: `test_set_evaluated` フラグ + データ SHA256 ハッシュ

#### リスク管理
- Circuit Breaker: 全て `current_balance * pct` で動的計算（固定額なし）
- CVaR: 直近50トレード、更新頻度毎日 + エグジット時、段階対応（-3%/-4%/-5%）
- Kill Switch: 環境変数 `KILL_SWITCH_OVERRIDE=1` + CLI + 対話確認で解除
- 失敗後の `MANUAL_INTERVENTION_REQUIRED` 状態と緊急アラート

#### セキュリティ
- APIキー: pydantic `SecretStr` で管理
- エラー記録: `sanitize_error(e)` でAPIキーパターンをマスク
- `.env` パーミッション: `600` (Unix) / ACL (Windows)
- 監査ログ: append-only + SHA256 ハッシュチェーン

#### Live 実行制御
- 二段階ゲート: `TRADING_MODE=live` env + `--confirm-live` CLI（config.yaml 単独では不可）
- 二重発注防止: `BEGIN EXCLUSIVE` トランザクション + UNIQUE 制約

## bitbank API メモ
- Maker 手数料: 0% / Taker: 0.1%
- レート制限: GET 10回/秒、POST 6回/秒
- 最小注文: 0.0001 BTC
- Candlestick API: 1min〜1month、日付指定可

## 主要依存ライブラリ
```
pandas>=2.0, numpy>=1.24, pydantic>=2.0, pydantic-settings>=2.0
httpx>=0.25, python-dotenv>=1.0, pyyaml>=6.0
loguru>=0.7, pyarrow>=14.0, matplotlib>=3.7
pytest>=7.0, pytest-asyncio>=0.21, pytest-cov>=4.0
arch  # optimal_block_length のため
statsmodels  # multipletests (BH法) のため
```
