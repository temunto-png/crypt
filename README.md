# 暗号資産自動売買システム

BTC/JPY 現物 Long-only の自動売買システム。

**現在のフェーズ**: バックテスト・Paper trade・Live trade 実装完了。

## 前提

- Python >= 3.11
- bitbank アカウント（出金権限なし API キー）

## セットアップ

```bash
# 1. 依存関係インストール
pip install -e ".[dev]"

# 2. 設定ファイルを作成
cp .env.example .env
cp config.example.yaml config.yaml

# 3. .env を編集して API キーを設定
#    CRYPT_BITBANK_API_KEY=<your_key>
#    CRYPT_BITBANK_API_SECRET=<your_secret>

# 4. テスト実行
pytest
```

## 実行

```bash
# ヘルプ表示
python -m cryptbot.main --help

# paper モード（シミュレーション売買）
python -m cryptbot.main --config config.yaml --mode paper
```

> **注意**: 本番取引（live mode）実行時は、少なくとも 2 週間の paper trade 実績と 起動前チェックリスト を確認してください。

## Live 移行条件チェックリスト

以下を**全て**満たすまで live 取引を開始しない:

- [ ] バックテストで手数料・スリッページ込み期待値がプラス（bootstrap CI 下限 > 0）
- [ ] Buy & Hold よりリスク調整後成績が良い
- [ ] ウォークフォワード検証（学習6ヶ月・検証4ヶ月・ステップ4ヶ月）で崩れていない
- [ ] Paper trade を最低2週間以上実施した
- [ ] Paper trade で想定外の注文・残高不整合がない
- [ ] Kill switch が動作することを確認した
- [ ] 日次損失上限が動作することを確認した
- [ ] 最大ポジション制限が動作することを確認した
- [ ] API エラー時に停止することを確認した
- [ ] 全判断ログが保存されることを確認した
- [ ] API キーに出金権限がないことを確認した
- [ ] ユーザーが明示的に live 移行を許可した

## Live 運用

### 起動前チェックリスト

1. `config.yaml` の `mode: live` と `pair: btc_jpy` を確認する
2. `.env` に `BITBANK_API_KEY` / `BITBANK_API_SECRET` が設定されていることを確認する
3. bitbank の API キーに **出金権限がない** ことを確認する
4. paper モードで少なくとも 2 週間動作実績があることを確認する

### 起動コマンド

```bash
python -m cryptbot.main --config config.yaml --mode live
```

### 起動リカバリー動作

再起動時、エンジンは以下の順序で自動リカバリーを行う:

1. `CREATED`（exchange_order_id なし）→ ローカルキャンセル + order_events 記録
2. `SUBMITTED` / `PARTIAL` → 取引所照合 → DB と LiveState を最新化
3. 残高照合 → 乖離が許容差を超える場合は fail-close（手動確認を要求）

### 手動復旧が必要なケース

- LiveState が存在しない状態で BTC 残高がある（初回起動時の異常）
- `exchange_order_id` が空の `SUBMITTED` 注文が残っている
- 残高乖離が `balance_sync_tolerance_pct` を超えている

上記の場合、ログに詳細が出力されるので、`LiveState` ファイルを手動で編集後に再起動する。

### Kill Switch

プロセスに `SIGINT`（Ctrl+C）または `SIGTERM` を送ると graceful shutdown する。
既存の open orders はキャンセルされない。手動で bitbank の管理画面から確認すること。

## ディレクトリ構成

```
crypt/
  src/cryptbot/       # メインパッケージ
    config/           # pydantic 設定
    data/             # データ取得・保存
    strategies/       # 取引戦略
    risk/             # リスク管理・Kill Switch
    backtest/         # バックテストエンジン
    paper/            # Paper trade エンジン
    live/             # Live trade エンジン
    reports/          # レポート生成
    utils/            # ユーティリティ
  tests/              # テスト
  docs/               # 設計ドキュメント
  logs/               # ログ出力先（.gitignore）
  reports/            # レポート出力先（.gitignore）
```

## 監査ログ整合性チェック

```bash
python -m cryptbot.tools.verify_audit_log --db <DB_PATH>
```

例:

```bash
python -m cryptbot.tools.verify_audit_log --db data/cryptbot.db
```
