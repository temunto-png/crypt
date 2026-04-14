# 暗号資産自動売買システム

BTC/JPY 現物 Long-only の自動売買システム（paper trade MVP）。

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

## 実行（Paper Trade）

```bash
python -m cryptbot.main
```

## 手動停止手順

1. `Ctrl+C` でプロセスを停止する
2. 保有ポジションが残っている場合は bitbank の Web UI から手動でクローズする
3. `logs/crypt.jsonl` でポジション状態を確認する

**注意**: Kill Switch が発動した場合（最大ドローダウン 15% 超）、システムは自動再開しない。
再開には以下の手順が必要:
```bash
# Kill Switch 解除（慎重に判断すること）
KILL_SWITCH_OVERRIDE=1 python -m cryptbot.main --override-kill-switch
```

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

## Live 移行手順

```bash
# 両方の条件が揃った場合のみ live 起動可能
export TRADING_MODE=live
python -m cryptbot.main --confirm-live
# > Live 取引モードで起動しようとしています
# > 取引所: bitbank / 銘柄: BTC/JPY
# > 続行しますか？ [yes/NO]: yes
```

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
    execution/        # 注文執行
    reports/          # レポート生成
    utils/            # ユーティリティ
  tests/              # テスト
  docs/               # 設計ドキュメント
  logs/               # ログ出力先（.gitignore）
  reports/            # レポート出力先（.gitignore）
```

## 監査ログ整合性チェック

```bash
python -m cryptbot.tools.verify_audit_log
```
