# 敵対的ソースレビュー引き継ぎメモ

作成日: 2026-04-14  
対象: `cryptbot` BTC/JPY 暗号資産自動売買システム  
目的: Claude Code に修正・追加検証を引き継ぐための、敵対的レビュー結果と優先順位

## レビュー前提

- 現状の実装は、README 上は paper trade MVP とされているが、実体は主に「データ取得、戦略、バックテスト、リスク部品、レポート」の Phase 1 相当。
- `python -m cryptbot.main` は実行不可。`src/cryptbot/main.py`、`src/cryptbot/paper/`、`src/cryptbot/execution/`、`src/cryptbot/tools/` は存在しない。
- Private API は `BitbankExchange` で `NotImplementedError` のまま。
- テスト結果:
  - `pytest`: 249 passed
  - `pytest --cov=src --cov-report=term-missing`: 249 passed, total coverage 90%
  - ただし、カバレッジ初回実行は `.coverage.*` の rename 権限で失敗し、権限外実行で成功。
- 重要: テストが通っていることは、実運用安全性やバックテスト妥当性を保証していない。

## 結論

現状を live trade に進めるのは不可。paper trade としても、README の実行経路が存在せず、バックテスト会計・約定タイミング・リスク発動・監査ログに致命的/高リスクの欠陥がある。

修正優先度は次の通り。

1. バックテスト会計と約定タイミングを直す。
2. README/設計で約束している実行入口、paper engine、audit verifier、live gate の実体をそろえる。
3. リスク制御を「設定値がある」状態から「発動する」状態にする。
4. 監査ログの改ざん検知を tail record まで検知できる方式にする。
5. live 移行条件をコード、テスト、運用手順、法務/コンプライアンスチェックリストに落とす。

## P0: 即時修正または live/paper 運用ブロック

### P0-01 README の実行コマンドが存在しない

職種視点: Pythonバックエンド、QA、SRE、法務/コンプライアンス

根拠:
- `README.md:24-27` は `python -m cryptbot.main` を案内している。
- `README.md:61-62` は `paper/` と `execution/` をディレクトリ構成に含めている。
- `README.md:67-69` は `python -m cryptbot.tools.verify_audit_log` を案内している。
- 実確認: `python -m cryptbot.main` は `No module named cryptbot.main` で失敗。
- 実確認: `src/cryptbot/main.py`、`src/cryptbot/paper`、`src/cryptbot/execution`、`src/cryptbot/tools` は存在しない。

リスク:
- ユーザーは paper trade が実行可能だと誤認する。
- live gate、kill switch override、監査ログ検証などの安全機構が「文書だけ」に存在する状態。
- 法務/コンプライアンス上、実態と説明の乖離が大きい。

修正方針:
- MVP の実行入口を実装するか、README/設計を Phase 1 実態に合わせて修正する。
- 少なくとも `cryptbot.main` は paper-only で起動し、live は明示的に未対応として fail closed する。
- `cryptbot.tools.verify_audit_log` を実装し、DB パスを受け取って `Storage.verify_audit_chain()` を実行する。

受け入れ条件:
- `python -m cryptbot.main --help` が成功する。
- `python -m cryptbot.main` が paper-only として起動、または明確な未実装エラーで安全に終了する。
- `python -m cryptbot.tools.verify_audit_log --db <path>` が監査ログ検証結果を返す。
- README の手順と実コードが一致する。

### P0-02 バックテストの残高会計が破綻している

職種視点: クオンツ、Pythonバックエンド、リスク管理、QA

根拠:
- `src/cryptbot/backtest/engine.py:214-222` でエントリー時に `entry_cost` 全額を `portfolio.balance` から差し引いている。
- `src/cryptbot/backtest/engine.py:188-190` でクローズ時に `record_trade_result(portfolio, trade.pnl, ...)` を呼ぶ。
- `src/cryptbot/backtest/engine.py:320-321` の `pnl` は `exit_proceeds - entry_cost`。
- `src/cryptbot/risk/manager.py:143-144` は `p.balance += pnl` だけを行う。

何が起きるか:
- エントリー時に購入原資を差し引いたあと、クローズ時に売却代金ではなく損益だけを足している。
- つまり `entry_cost` が二重に資産から消える。
- 例: 100,000 JPY から約 30,000 JPY 分を買い、同値で売るだけでも、残高は本来「往復コスト分だけ減少」すべきところ、約 30,000 JPY 近く過大に減る。

リスク:
- `final_balance`、`total_pnl`、drawdown、loss limit、metrics がすべて信用不能。
- これを元に戦略評価や live 移行判断をすると、意思決定が壊れる。

修正方針:
- 現金会計を明確化する。
  - エントリー時: cash から `entry_cost` を差し引き、BTC position を増やす。
  - クローズ時: cash に `exit_proceeds` を足し、pnl は loss counter / trade record 用に別途記録する。
- `record_trade_result()` は「残高更新」と「損失カウンタ更新」を分離するか、引数を `cash_delta` と `realized_pnl` に分ける。

受け入れ条件:
- 同値往復トレードの final balance が `initial_balance - entry_fee - entry_slippage - exit_fee - exit_slippage` になる。
- 十分な値上がりの往復トレードで final balance が initial balance を上回る。
- PnL、fee、slippage、equity_curve、PortfolioState の整合性テストを追加する。

### P0-03 バックテストに look-ahead bias がある

職種視点: クオンツ、データエンジニア、QA

根拠:
- `src/cryptbot/backtest/engine.py:168-171` で現在バーを含む `window = data.iloc[: i + 1]` を戦略に渡す。
- `src/cryptbot/backtest/engine.py:160-162` の `current_price = current_bar["close"]` を、その同じバーでのエントリー/クローズ価格として使う。

何が起きるか:
- そのバーの終値を見てシグナルを作り、その同じ終値で約定している。
- candle close 後にしか分からない情報で、同じ candle close に約定した扱いになっている。

リスク:
- バックテスト成績が過大評価または歪む。
- 特に MA cross、momentum、Bollinger band のような close 依存の指標で致命的。

修正方針:
- シグナル生成はバー `i` の close まででよいが、約定はバー `i+1` の open または保守的な next close にずらす。
- 末尾バーのシグナルは約定不可として捨てる。
- slippage は next execution price に対して適用する。

受け入れ条件:
- 「バー i のシグナルがバー i+1 で約定する」ことを検証するテストを追加。
- 最終バーで BUY/SELL が出ても新規約定しないテストを追加。
- レポートに execution timing を明記する。

### P0-04 live/paper gate が実体化していない

職種視点: セキュリティ、SRE、リスク管理、法務/コンプライアンス

根拠:
- `src/cryptbot/config/settings.py:91-108` には `mode: Literal["paper", "live"]` がある。
- `.env.example` は `CRYPT_MODE=paper` を使う。
- README は `TRADING_MODE=live` と `--confirm-live` を案内している。
- しかし `cryptbot.main` が存在せず、CLI gate も存在しない。
- `src/cryptbot/exchanges/bitbank.py:148-165` の private API は未実装。

リスク:
- live 不可なのは安全側とも言えるが、説明とコードの乖離が大きい。
- 将来 `main` を雑に追加すると、`CRYPT_MODE=live` だけで live 化する危険がある。

修正方針:
- live は fail closed をデフォルトにする。
- 環境変数名を統一する。推奨は `CRYPT_MODE` に寄せ、README から `TRADING_MODE` を除去するか、明示的に両方必要にする。
- `--confirm-live`、対話確認、APIキー権限確認、残高確認、kill switch state 確認を live 起動前に必須化する。

受け入れ条件:
- `CRYPT_MODE=live` だけでは live 実行されない。
- `--confirm-live` だけでも live 実行されない。
- live executor 未実装の間は、常に明示的な `LiveTradingNotImplementedError` で終了する。

## P1: 高優先度

### P1-01 リスク設定が実際の売買制御に接続されていない

職種視点: リスク管理、クオンツ、QA

根拠:
- `src/cryptbot/config/settings.py:19` の `max_trade_loss_pct` はソース全体で設定以外に使われていない。
- `src/cryptbot/risk/manager.py:161-179` の `update_drawdown_stop()` は呼ばれていない。
- `src/cryptbot/risk/manager.py:182-218` の `get_cvar_action()` は呼ばれていない。
- `src/cryptbot/backtest/engine.py` はポジション保有中の stop loss / max trade loss / CVaR action / drawdown stop を毎バー評価していない。

リスク:
- 1トレード最大損失、CVaR、最大DD kill switch が「存在するだけ」で、バックテストでも paper でも発動しない。
- live 移行条件にある risk controls の確認ができない。

修正方針:
- Backtest/Paper の各バーで以下を評価する。
  - unrealized PnL による max trade loss
  - equity mark-to-market による drawdown stop
  - realized return series による CVaR action
  - daily/weekly/monthly loss stop
- risk breach は `risk_stop` / `kill_switch` として audit log に残す。

受け入れ条件:
- 価格急落時に `max_trade_loss_pct` で強制クローズするテスト。
- 最大DD到達時に kill switch が active になり、それ以降新規エントリー不可になるテスト。
- CVaR stop が新規エントリーを止めるテスト。

### P1-02 Kill switch が永続化されない

職種視点: リスク管理、SRE、セキュリティ、QA

根拠:
- `src/cryptbot/risk/kill_switch.py:45-54` で activate はメモリ状態と audit log を更新する。
- `src/cryptbot/risk/kill_switch.py:67-72` の `load_state()` は常に inactive に戻す。
- `src/cryptbot/config/settings.py:43-44` の `KillSwitchSettings.active` は `KillSwitch` 初期状態に接続されていない。
- `src/cryptbot/risk/kill_switch.py:56-65` の `deactivate()` は二段階承認や override 確認なしで解除できる。

リスク:
- プロセス再起動で kill switch が解除される。
- 設計上の「自動再開しない」に反する。

修正方針:
- DB に `system_state` または `kill_switch_state` を追加し、active/reason/activated_at を永続化する。
- `load_state()` は DB の最新状態を復元する。
- `deactivate()` は production path から直接呼ばせず、CLI override + 対話確認 + audit log を必須化する。

受け入れ条件:
- kill switch 発動後に別インスタンスで `load_state()` しても active が維持される。
- override なしで deactivate できない。
- deactivate audit log に解除理由と operator action が残る。

### P1-03 監査ログの改ざん検知が tail record を検知できない

職種視点: セキュリティ、法務/コンプライアンス、データエンジニア

根拠:
- `src/cryptbot/data/storage.py:76-92` の `audit_log` は `prev_hash` だけを保持する。
- `src/cryptbot/data/storage.py:292-337` は新規レコードの `prev_hash` に前レコードのハッシュを格納する。
- `src/cryptbot/data/storage.py:352-363` は各行の `prev_hash` が前行由来かだけ確認する。

リスク:
- 最終行の内容を書き換えても、次行がないため検知できない。
- 末尾行の削除も検知しにくい。
- SQLite に UPDATE/DELETE を禁止する trigger がない。

修正方針:
- `audit_log` に `record_hash` を追加し、各行自身の hash を保存する。
- verify は `prev_hash == previous.record_hash` かつ `record_hash == hash(prev_hash + canonical_record)` を検証する。
- `BEFORE UPDATE` / `BEFORE DELETE` trigger で audit_log の変更を拒否する。
- 可能なら run ごとの final chain hash を別テーブル/ファイルに保存する。

受け入れ条件:
- 最終行の `signal_reason` を手動 UPDATE したら verify が失敗する。
- audit_log の UPDATE/DELETE が SQLite trigger で失敗する。
- 末尾削除を検知する仕組み、または少なくとも run final hash の照合がある。

### P1-04 Buy & Hold と walk-forward が戦略インスタンス状態を汚染する

職種視点: クオンツ、QA、Pythonバックエンド

根拠:
- `src/cryptbot/strategies/buy_and_hold.py:15-30` は `_has_position` を戦略インスタンス内部に持つ。
- `src/cryptbot/backtest/engine.py:386-389` は walk-forward で同じ engine/strategy に対して training run 後に validation run を行う。

リスク:
- training run が `_has_position=True` にすると、validation window で初回 BUY が出ない。
- 同じ strategy instance を使った複数回バックテストが独立でなくなる。
- ベンチマーク比較が壊れる。

修正方針:
- 戦略は原則 stateless にする。
- ポジション有無は engine/portfolio が判断する。
- やむを得ず stateful にするなら `reset()` を BaseStrategy に追加し、run/walk-forward window ごとに呼ぶ。

受け入れ条件:
- 同じ `BuyAndHoldStrategy` インスタンスで `run()` を2回実行しても、各 run で初回 BUY が出る。
- walk-forward の validation window が training の戦略 state に影響されない。

### P1-05 RegimeDetector が売買・リスク制御に統合されていない

職種視点: クオンツ、リスク管理

根拠:
- `src/cryptbot/strategies/regime.py` に `RegimeDetector` はある。
- しかし production/backtest path で呼ばれていない。
- 設計上は TREND_DOWN / HIGH_VOL で long position close や戦略停止が必要。

リスク:
- long-only なのに下落トレンド/高ボラ時の強制クローズが発動しない。
- 設計ドキュメントとバックテスト結果が一致しない。

修正方針:
- BacktestEngine/PaperEngine に regime step を入れる。
- `TREND_DOWN` / `HIGH_VOL` の close signal を strategy signal より優先する。
- regime 別の trade/no-trade 統計を Metrics/Report に出す。

受け入れ条件:
- HIGH_VOL で保有ポジションがクローズされ、新規エントリーしないテスト。
- TREND_DOWN で long position がクローズされるテスト。

### P1-06 注文DBに重複発注防止・状態遷移制約がない

職種視点: Pythonバックエンド、SRE、リスク管理、QA

根拠:
- `src/cryptbot/data/storage.py:94-109` の `orders` テーブルに active order の unique 制約がない。
- `src/cryptbot/data/storage.py:415-448` の `update_order_status()` は任意の status 文字列を受ける。
- `order_events` テーブルが存在しない。

リスク:
- live 実装時に二重発注・不正な状態遷移・partial fill の履歴欠落が起きる。
- 障害復旧時に取引所状態との reconcile ができない。

修正方針:
- `orders.status` を enum 的に制限する。
- `order_events` を追加し、状態遷移を append-only で記録する。
- active order 重複を DB transaction + partial unique index で防ぐ。

受け入れ条件:
- 同一 pair/side の active order を二重 insert できない。
- `FILLED -> CREATED` のような不正遷移が拒否される。
- partial fill と cancel が order_events に残る。

## P2: 中優先度

### P2-01 データ保存・読込の完全性チェックが弱い

職種視点: データエンジニア、QA、クオンツ

根拠:
- `src/cryptbot/data/storage.py:202-216` の `has_ohlcv()` は metadata だけを見ており、実 Parquet ファイルの存在や hash を確認しない。
- `src/cryptbot/data/storage.py:239-270` の `load_ohlcv()` はディレクトリ配下の全 Parquet を読み、metadata と照合しない。
- `src/cryptbot/data/storage.py:130-200` の `save_ohlcv()` は重複 timestamp、単調増加、OHLC 整合性、year 範囲を検証しない。

リスク:
- 欠損、重複、壊れた Parquet、誤配置ファイルがバックテストに混入する。
- 再現可能性が低い。

修正方針:
- 保存時に data hash、row count、timestamp min/max、source exchange、fetch timestamp を metadata に保存する。
- 読込時に metadata と実ファイルを照合する。
- OHLCV integrity validation を実装する。

受け入れ条件:
- metadata はあるがファイルがない場合、`has_ohlcv()` は false または integrity error。
- 重複 timestamp を保存しようとすると失敗。
- high < max(open, close) などの壊れた OHLC を拒否。

### P2-02 Exchange fetch に retry/backoff/rate-limit がない

職種視点: Pythonバックエンド、SRE、データエンジニア

根拠:
- `src/cryptbot/exchanges/bitbank.py:171-199` は単発 GET。
- timeout と HTTP status は例外化するが、retry/backoff/rate-limit/circuit breaker はない。
- `raw_response` に response text をそのまま保持する。

リスク:
- 一時的な API 障害でデータ取得が失敗する。
- private API 実装時に error body や headers 由来の機密情報がログに出る危険がある。

修正方針:
- Public API にも限定的 retry/backoff を追加する。
- error sanitize を `ExchangeError` 生成前または logging 前に通す。
- private API 実装時は raw response/body/header をログ保存しない方針にする。

受け入れ条件:
- 429/5xx/timeout の retry テスト。
- retry exceeded 時の sanitized error テスト。

### P2-03 メトリクスの評価基準が live 移行条件に不足

職種視点: クオンツ、リスク管理、QA

根拠:
- `src/cryptbot/reports/generator.py` の criteria は total pnl、max DD、PF、Sharpe、trade count 程度。
- README/設計にある bootstrap CI、Buy & Hold 比較、random entry benchmark、walk-forward robustness は未実装。
- `src/cryptbot/backtest/metrics.py:193-205` の CVaR は trade count < 20 で 0.0 を返すため、リスクなしと誤読されうる。

リスク:
- 成績の統計的有意性がないまま live 移行判断される。
- 取引数不足でも良い成績に見える。

修正方針:
- Bootstrap CI、Buy & Hold、random entry Monte Carlo、minimum effective sample size を実装する。
- CVaR は `None` / `insufficient_sample` を返せる型にする。
- Report に live readiness を明示する。

受け入れ条件:
- trade count 不足時は live readiness が fail。
- Buy & Hold と random entry benchmark を下回る場合は fail。
- Bootstrap CI 下限が 0 以下なら fail。

### P2-04 ログ/監査ログ sink が未完成

職種視点: セキュリティ、SRE、QA

根拠:
- `src/cryptbot/utils/logger.py:76-92` の `_AuditLogSink.__call__()` は storage に書かない。
- `src/cryptbot/utils/logger.py:97-104` の `setup_audit_logger()` は sink を追加するだけ。

リスク:
- `logger.bind(audit=True)` でログしても SQLite audit_log に残らない。
- 「全判断ログが保存される」という設計要求を満たさない。

修正方針:
- audit 用イベントは logger 経由ではなく、明示的な domain event API で Storage に書くほうが安全。
- logger sink を使うなら record schema mapping と failure handling を実装する。

受け入れ条件:
- signal/fill/risk_stop/no_trade/kill_switch が audit_log に残る。
- audit write 失敗時に処理を続行するか停止するか、方針がテストされている。

## 職種別レビュー観点

### Pythonバックエンドエンジニア

- 実行入口、paper engine、execution layer が存在しない。
- stateful strategy と engine の責務分離が不十分。
- order lifecycle/state machine が DB に落ちていない。
- pydantic settings と README の env 名が一致していない。
- private API 未実装の状態を runtime で安全に表現できていない。

### クオンツ / アルゴリズム取引エンジニア

- バックテスト会計が破綻。
- 同一バー close で signal 生成・約定しており look-ahead bias。
- walk-forward が training 結果を捨てるだけで、parameter optimization も strategy reset もない。
- regime logic が実運用 path に未統合。
- Buy & Hold / random entry / bootstrap CI が未実装。

### リスク管理・金融ドメイン担当

- max trade loss、CVaR、drawdown stop が実取引判断に接続されていない。
- kill switch が再起動で解除される。
- monthly loss breach が kill switch reason として列挙されているが、実際に activate されない。
- position close failure、manual intervention、partial fill の状態が実装されていない。

### セキュリティエンジニア

- audit log は tail 改ざんを検知できない。
- audit_log に UPDATE/DELETE trigger がない。
- error sanitization は限定的で、ExchangeError.raw_response に未加工 body を保持する。
- API key 権限確認、withdraw 権限なし確認、secret scanning/pre-commit がない。
- live gate がないため、将来実装時の fail-open リスクがある。

### QA / テストエンジニア

- 249 tests pass だが、致命的な会計不整合と look-ahead bias を検出できていない。
- README のコマンド実行テストがない。
- run/walk-forward の独立性テストがない。
- kill switch 永続化、audit tail tamper、risk control 発動のテストがない。
- coverage 90% は高く見えるが、安全要求ベースのテストが不足。

### データエンジニア

- Parquet と metadata の整合性検証が弱い。
- OHLCV の欠損/重複/異常値検出がない。
- API fetch の retry/backoff/rate-limit がない。
- データハッシュや source version が不足しており再現性が弱い。

### 法務 / コンプライアンス担当

- README/設計と実装の乖離が大きく、ユーザー説明として危険。
- live trade 未実装なら、README で明確に「実資金運用不可」と表示すべき。
- API key は withdraw 権限なしが必須だが、起動時検証がない。
- 顧客資産を扱わないこと、自己売買限定であること、税務/記録保存、取引所利用規約、暗号資産関連規制の確認が必要。
- audit log の改ざん耐性が不十分なため、事後説明責任に耐えない。

## Claude Code への推奨作業順

1. `BacktestEngine` の cash/position accounting を修正し、同値往復・利益往復・損失往復のテストを追加。
2. 約定タイミングを next bar execution に変更し、look-ahead bias を防ぐテストを追加。
3. `cryptbot.main` を paper-only/fail-closed で実装し、README と env 名を統一。
4. `KillSwitch` を DB 永続化し、`load_state()` と override flow をテスト。
5. `Storage.audit_log` に `record_hash` と UPDATE/DELETE trigger を追加し、tail 改ざんテストを追加。
6. risk controls を Backtest/Paper loop に統合。
7. order state machine と `order_events` を追加。
8. data integrity validation と metadata hash を追加。
9. benchmark/CI/Monte Carlo/report live readiness を追加。

## 確認コマンド

```bash
pytest
pytest --cov=src --cov-report=term-missing
python -m cryptbot.main --help
python -m cryptbot.tools.verify_audit_log --db <path-to-sqlite>
```

現時点では `python -m cryptbot.main` と `python -m cryptbot.tools.verify_audit_log` は失敗するため、修正後の受け入れ確認に使う。
