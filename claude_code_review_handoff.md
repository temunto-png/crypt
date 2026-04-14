# Claude Code 引き継ぎメモ: 敵対的レビュー結果

作成日: 2026-04-14  
対象: `cryptbot` BTC/JPY 暗号資産自動売買システム  
目的: 敵対的レビューで見つかった問題を Claude Code に修正・追加検証として引き継ぐ。

## レビュー前提

- 対象リポジトリ: `https://github.com/temunto-png/crypt.git`
- ローカル作業ディレクトリ: `C:\tool\claude\crypt`
- 現在ブランチ: `master`
- 最新確認コミット: `b3adb21 Initial commit: Phase 0/1/P2 implementation (pytest 346 passed)`
- 通常テスト: `python -m pytest -q` → `346 passed`
- カバレッジ付きテスト: `pytest --cov=src --cov-report=term-missing -q` → `346 passed`, total coverage `91%`
- ただし、テスト成功は paper/live 運用安全性を保証しない。

## 結論

現状は **live 取引不可**。さらに README が示す **paper trade MVP としても未完成**。

バックテスト会計、next-bar open 約定、監査ログハッシュチェーンなどは改善されているが、以下の理由で実運用・paper運用に進めてはいけない。

1. YAML 設定が実際には読み込まれない。
2. `python -m cryptbot.main` は paper trade を実行しない。
3. CVaR のポジション半減制御が効かない。
4. Kill Switch 永続状態の復元が本体経路に組み込まれていない。
5. 監査ログ書き込み失敗時に処理が継続する。
6. Live readiness が CVaR の危険値を PASS してしまう。
7. 注文制約が Long-only / 最大同時ポジション1 の前提と弱く噛み合っている。

## 修正優先順位

1. **P0-01 YAML設定読み込みを修正する**
2. **P0-02 README と実行入口の乖離を解消する**
3. **P1-01 CVaR half 制御を実際のサイズ計算に接続する**
4. **P1-02 Kill Switch 復元を起動経路に組み込む**
5. **P1-03 監査ログ失敗時の fail-open をやめる**
6. **P1-04 Live readiness の CVaR 判定を閾値ベースにする**
7. **P2-01 active 注文制約を Long-only 前提に合わせて見直す**

---

## P0-01 YAML設定が読み込まれていない

対象:

- `src/cryptbot/config/settings.py:120-135`
- `tests/test_config/test_settings.py`

職種視点:

- Python バックエンド
- リスク管理
- QA
- SRE

### 問題

`pydantic-settings` v2 では現在の `customise_sources` が呼ばれておらず、`load_settings(yaml_path=...)` が YAML を反映しない。

実確認:

```powershell
@'
from pathlib import Path
from tempfile import TemporaryDirectory
from cryptbot.config.settings import load_settings

with TemporaryDirectory() as d:
    p = Path(d) / "config.yaml"
    p.write_text("mode: live\ncircuit_breaker:\n  daily_loss_pct: 0.07\n", encoding="utf-8")
    s = load_settings(yaml_path=p)
    print("mode=", s.mode)
    print("daily=", s.circuit_breaker.daily_loss_pct)
'@ | python -
```

実際の出力:

```text
mode= paper
daily= 0.03
```

期待:

```text
mode= live
daily= 0.07
```

### リスク

- `config.yaml` の `circuit_breaker`、`cvar`、`kill_switch` が無視される。
- ユーザーがリスク閾値を保守的に変更したつもりでも、デフォルト値で実行される。
- Live gate や kill switch 設定の運用前提が崩れる。
- テストがデフォルト値と同じ `mode: paper` しか見ておらず、バグを検出できていない。

### 修正方針

- `pydantic-settings` v2 の正しい hook 名に直す。
  - 通常は `settings_customise_sources` を使う。
- `YamlConfigSource` がネスト設定を正しく返すことを確認する。
- `load_settings(yaml_path=...)` のテストで、デフォルトと異なる値を使う。

### 受け入れ条件

- `load_settings(yaml_path=...)` で `mode: live` が `s.mode == "live"` になる。
- `circuit_breaker.daily_loss_pct: 0.07` が `s.circuit_breaker.daily_loss_pct == 0.07` になる。
- `cvar.half_pct` などネスト設定も YAML から反映される。
- 環境変数が YAML より優先されるテストを追加する。
- 既存 `346 passed` を維持する。

---

## P0-02 Paper trade MVPが実行されない

対象:

- `README.md:28-32`
- `README.md:75-88`
- `src/cryptbot/main.py:72-76`

職種視点:

- Python バックエンド
- QA
- SRE
- 法務/コンプライアンス

### 問題

README は以下を Paper Trade 実行手順として案内している。

```bash
python -m cryptbot.main
```

しかし実体は paper engine 未実装メッセージを表示して終了するだけ。

`README.md` には `paper/` と `execution/` ディレクトリも記載されているが、現状の実装には存在しない。

また README の監査ログ検証コマンドは `--db` が抜けている。

```bash
python -m cryptbot.tools.verify_audit_log
```

実際には `--db PATH` が必須。

### リスク

- ユーザーが paper trade が実行可能だと誤認する。
- Live 移行チェックリストの「Paper trade を最低2週間以上実施」が満たせない。
- 実装実態と公開説明が乖離し、コンプライアンス上も危険。

### 修正方針

以下のどちらかを選ぶ。

選択肢 A: README を実態に合わせる。

- 現状を「バックテスト/設計/基盤フェーズ」と明記する。
- Paper trade は未実装と明記する。
- `paper/` と `execution/` を未実装または予定として記載する。
- 監査ログ検証コマンドに `--db` を追加する。

選択肢 B: 最小 paper engine を実装する。

- `src/cryptbot/paper/` を追加する。
- `python -m cryptbot.main` が paper-only で起動する。
- live は fail-closed のまま維持する。
- 全判断ログを audit_log に保存する。

### 受け入れ条件

- README の実行手順と実コードの挙動が一致する。
- `python -m cryptbot.main --help` が成功する。
- `python -m cryptbot.main` の挙動が README に明記されている。
- `python -m cryptbot.tools.verify_audit_log --db <path>` の形で README が案内する。
- live は引き続き実資金を使わない fail-closed である。

---

## P1-01 CVaRの半減制御が無効

対象:

- `src/cryptbot/backtest/engine.py:284-293`
- `src/cryptbot/backtest/engine.py:401-402`
- `tests/test_backtest/test_engine.py`

職種視点:

- クオンツ
- リスク管理
- QA

### 問題

ループ側では `cvar_action` を計算しているが、`stop` しか処理していない。

```python
if cvar_action == "stop" or self._risk_manager._kill_switch.active:
    pending_signal = Signal(...)
```

実際のエントリー時には以下のように空リストで再計算している。

```python
cvar_action = self._risk_manager.get_cvar_action([])  # always normal
size_multiplier = 0.5 if cvar_action == "half" else 1.0
```

`get_cvar_action([])` は常に `"normal"` なので、設計上の `half` が発動しない。

### リスク

- CVaR が悪化してもポジションサイズ半減が効かない。
- risk policy の段階対応とバックテスト結果が一致しない。
- Live readiness の判断材料が過大評価される。

### 修正方針

- ループで算出した `cvar_action` を `_execute_pending()` に渡す。
- `_execute_pending()` では渡された `cvar_action` に基づいて size multiplier を決定する。
- `warning` はログまたは結果メタデータに残す。
- `half` の場合は `calculate_position_size()` 後に 50% を適用し、最小注文未満ならエントリーしない。

### 受け入れ条件

- 直近トレードリターンが `half` 条件の場合、次回 BUY のサイズが通常の 50% になる。
- `stop` 条件の場合、BUY が HOLD に変換される。
- `normal` 条件の場合、通常サイズでエントリーする。
- 上記3ケースの backtest engine テストを追加する。

---

## P1-02 Kill Switch復元が呼ばれない設計

対象:

- `src/cryptbot/risk/kill_switch.py:24-28`
- `src/cryptbot/risk/kill_switch.py:71-104`
- `src/cryptbot/main.py`

職種視点:

- リスク管理
- SRE
- セキュリティ
- QA

### 問題

`KillSwitch` は生成時に常に inactive から始まる。

```python
self._active: bool = False
```

永続化状態を復元するには `load_state()` を明示的に呼ぶ必要があるが、現状の本体起動経路には組み込まれていない。

### リスク

- 将来 paper/live 実行経路を実装したとき、DB 上で kill switch active でも、プロセス起動直後は inactive として扱われる可能性がある。
- 最大ドローダウンや月次損失停止後の「自動再開しない」という設計が破られる。

### 修正方針

- `KillSwitch.__init__` で `load_state()` するか、明示的な factory を用意する。
- `main` / paper engine / live engine の起動時に必ず `load_state()` する。
- DB が未初期化または audit_log 未作成の場合の安全な挙動を決める。
- live/paper 起動時に kill switch active なら fail-closed する。

### 受け入れ条件

- `KILL_SWITCH_ACTIVATED` が audit_log にある DB で起動すると、entry が拒否される。
- `KILL_SWITCH_DEACTIVATED` が最新イベントなら inactive になる。
- paper/live 起動経路で `load_state()` がテストされる。

---

## P1-03 監査ログ失敗時に取引継続する

対象:

- `src/cryptbot/utils/logger.py:98-100`
- `src/cryptbot/utils/logger.py:142-146`

職種視点:

- セキュリティ
- SRE
- リスク管理
- 法務/コンプライアンス

### 問題

監査ログ sink は書き込み失敗時に stderr へ出力するだけで、例外を伝播しない。

```python
except Exception as exc:
    sys.stderr.write(...)
```

一方、README の live 移行条件には「全判断ログが保存されること」とある。

### リスク

- DB 障害、権限エラー、ディスク不足でも売買判断だけ進む。
- 「なぜ注文したか」を後から追えない。
- 事故時の説明責任を満たせない。

### 修正方針

- paper/live の売買判断に関わる audit write は fail-closed にする。
- 補助的な一般ログと監査ログを分離する。
- `storage.insert_audit_log()` を直接呼ぶ重要経路では例外を握りつぶさない。
- logger sink は補助用途に限定し、売買判断には使わない方針を明文化する。

### 受け入れ条件

- paper/live 相当の意思決定経路で audit write が失敗した場合、注文または疑似注文に進まない。
- テストで `insert_audit_log()` を失敗させ、処理が停止することを確認する。
- README または設計書に audit failure policy を明記する。

---

## P1-04 Live readinessがCVaR閾値を見ていない

対象:

- `src/cryptbot/backtest/benchmark.py:321-324`
- `src/cryptbot/backtest/metrics.py:113-115`
- `docs/risk_policy.md`

職種視点:

- クオンツ
- リスク管理
- QA

### 問題

Live readiness は CVaR が計算可能かだけを見る。

```python
metrics.cvar_95 is not None
```

つまり `cvar_95 = -10%` のような危険な値でも、この項目は PASS になる。

### リスク

- Live移行判定が危険なテールリスクを見逃す。
- risk policy の `warn_pct`, `half_pct`, `stop_pct` と live readiness が接続されていない。

### 修正方針

- `check_live_readiness()` に `cvar_settings` または明示的な `max_cvar_loss_pct` を渡す。
- 少なくとも `metrics.cvar_95 > cvar_settings.warn_pct * 100` など、単位を揃えた閾値判定を行う。
- 現状 `metrics.cvar_95` は `TradeRecord.pnl_pct` 由来で percent 単位なので、`CvarSettings` の fraction 単位と混同しない。

### 受け入れ条件

- `cvar_95 is None` は FAIL。
- `cvar_95 <= -3.0` など policy 閾値超過は FAIL。
- `cvar_95 = -10.0` が Live readiness PASS にならない。
- 単位変換のテストを追加する。

---

## P2-01 反対売買の同時active注文を許す

対象:

- `src/cryptbot/data/storage.py:202-205`
- `docs/live_trade_design.md`

職種視点:

- Python バックエンド
- トレーディングシステム
- リスク管理
- QA

### 問題

active 注文の一意制約が `(pair, side)` 単位。

```sql
CREATE UNIQUE INDEX IF NOT EXISTS orders_active_unique
  ON orders(pair, side)
  WHERE status IN ('CREATED', 'SUBMITTED', 'PARTIAL');
```

同じ pair で active BUY と active SELL が同時に存在できる。

Long-only、最大同時ポジション1、二重発注防止という設計なら、同一ペアに active 注文は1件まで、または状態機械でより厳密に制御した方が安全。

### リスク

- 将来 live executor 実装時に、BUY 未完了のまま SELL が作られるなど注文状態が複雑化する。
- 部分約定時のポジション復元やキャンセル処理が破綻しやすい。
- Long-only MVP の安全制約が DB レベルで保証されない。

### 修正方針

- MVP では active 注文を `pair` 単位で1件に制限する案を検討する。
- もし反対注文を exit 用に許可するなら、ポジション状態テーブルと注文目的 (`ENTRY` / `EXIT`) を導入し、状態遷移で保証する。
- live 実装前に `orders`, `positions`, `order_events` の責務を再整理する。

### 受け入れ条件

- Long-only MVP で同一 pair に複数 active 注文を作れない。
- 部分約定中の追加注文ルールがテストで明確になる。
- DB 制約とアプリ側チェックが同じルールを表現する。

---

## 追加で推奨するテスト

### 設定

- YAML で `mode: live` が反映される。
- YAML で `circuit_breaker.daily_loss_pct: 0.07` が反映される。
- env `CRYPT_MODE=paper` が YAML `mode: live` を上書きする。
- YAML の不正値が validation error になる。

### バックテスト / リスク

- CVaR `half` で次回サイズが半減する。
- CVaR `stop` でエントリーしない。
- Kill switch active DB から起動するとエントリーしない。
- 月次損失や最大DDで kill switch 発動後、次バー以降の BUY が拒否される。

### README / CLI

- README に記載されたコマンドを smoke test する。
- `python -m cryptbot.main` の実挙動と README の説明が一致する。
- `python -m cryptbot.tools.verify_audit_log --db <path>` が成功する。

### 監査

- `insert_audit_log()` 失敗時に paper/live 意思決定が停止する。
- audit_log 末尾行の改ざんを `verify_audit_chain()` が検出する。
- audit_log が空の DB で検証ツールが正常終了する。

## 修正後に実行するコマンド

```bash
python -m pytest -q
pytest --cov=src --cov-report=term-missing -q
python -m cryptbot.main --help
python -m cryptbot.tools.verify_audit_log --db <test-db-path>
```

注意:

- Windows 環境では `.coverage` の削除権限でカバレッジ実行が失敗することがある。
- その場合は権限を調整するか、既存 `.coverage` を安全に扱ってから再実行する。

## Claude Code への作業指示案

以下の順で直すこと。

1. `settings.py` の YAML 読み込みを修正し、テストを追加する。
2. README と `main.py` の実行実態を一致させる。paper engine を実装しないなら未実装を明記する。
3. BacktestEngine の CVaR action を `_execute_pending()` に渡し、`half` を実装する。
4. KillSwitch の `load_state()` を起動経路に組み込む。
5. 監査ログ失敗時の fail policy を明確化し、paper/live 重要経路は fail-closed にする。
6. Live readiness の CVaR 判定を risk policy の閾値に接続する。
7. 注文 DB 制約を Long-only MVP の注文状態に合わせて再設計する。

完了条件:

- 上記 P0/P1 がすべて修正される。
- テストが追加される。
- `python -m pytest -q` と `pytest --cov=src --cov-report=term-missing -q` が通る。
- README が現状の実装に対して誤解を生まない。
