# Walk-Forward KillSwitch 分離修正 設計書

**日付**: 2026-04-18  
**対象ブランチ**: `claude/nice-ishizaka-dedba6`  
**背景**: クオンツ・リスクマネージャー・統計家による職種パネルが選定した最優先修正

---

## 問題

`BacktestEngine.run_walk_forward()` は全ウィンドウで同一の `BacktestEngine` インスタンス（および同一の `KillSwitch`）を再利用する。

`KillSwitch.activate()` は SQLite の `audit_log` テーブルに書き込む。
`run()` 冒頭の `self._risk_manager._kill_switch.active` チェックが `True` を返すと、そのウィンドウは空結果（`datetime.now()` タイムスタンプ）でスキップされる。

結果として、学習窓でドローダウン上限に達して KillSwitch が発動すると、**後続の全ウィンドウがスキップ**される。

```
観測された症状:
  W01: 有効な BacktestResult
  W02〜W16: タイムスタンプが全て 2026-04-xx（実行時刻）の空結果
```

---

## 選択したアプローチ: KillSwitch.reset()

3 候補（reset メソッド / Engine Factory / ephemeral モード）から、変更量最小・既存 API 不変の `reset()` メソッド追加を選択。

**選択理由**:
- 変更ファイル 2 件のみ
- `engine.py` 内の `_risk_manager._kill_switch` 直接アクセスは既存慣例（349, 480, 568 行）に沿う
- WF は一時 DB を使うため DB に書かない reset で十分

---

## 変更仕様

### 1. `src/cryptbot/risk/kill_switch.py` — `reset()` 追加

```python
def reset(self) -> None:
    """Walk-forward ウィンドウ切り替え用リセット。

    インメモリ状態のみクリアする。audit_log への書き込みは行わない。
    Live/Paper 運用では使用しないこと（自動リセット禁止の原則）。
    """
    self._active = False
    self._reason = None
    self._activated_at = None
```

**制約**:
- `deactivate()` とは別。`deactivate()` は Live/Paper 用で DB に KILL_SWITCH_DEACTIVATED を記録する。
- `reset()` は audit_log を汚染しない。バックテスト一時 DB 専用の操作。

### 2. `src/cryptbot/backtest/engine.py` — `run_walk_forward()` 変更

各ウィンドウループ先頭に KillSwitch リセットを追加。

```python
while True:
    ...
    # ウィンドウ開始前に KillSwitch とリスク状態をリセット
    self._risk_manager._kill_switch.reset()

    # 学習期間
    if len(train_data) > warmup_bars:
        self._strategy.reset()
        self.run(train_data, warmup_bars=warmup_bars)

    # 検証期間
    self._strategy.reset()
    result = self.run(val_data, warmup_bars=min(warmup_bars, len(val_data) // 2))
    results.append(result)
    ...
```

**配置**: `strategy.reset()` の直前。PortfolioState は `run()` が毎回 `initial_balance` から再初期化するため別途リセット不要。

---

## テスト仕様

### `tests/test_risk/test_kill_switch.py`

**追加テスト 1**: `test_reset_clears_active_state`
- activate() 後に reset() を呼ぶと `active == False` になること
- `reason == None`、`activated_at == None` になること

**追加テスト 2**: `test_reset_does_not_write_to_db`
- reset() 後に `load_state()` を呼ぶと、DB の ACTIVATED イベントが残っているため `active == True` に戻ること
- （reset はインメモリのみ操作であることの確認）

### `tests/test_backtest/test_engine.py`

**追加テスト 3**: `test_walk_forward_killswitch_not_shared_between_windows`
- 学習窓でドローダウン上限を超えて KillSwitch が発動する合成データを作成
- `run_walk_forward()` が少なくとも 2 つの有効な BacktestResult を返すこと
- 2 窓目以降の `first_bar_time` が学習窓終了後のタイムスタンプであること

---

## 変更ファイル一覧

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `src/cryptbot/risk/kill_switch.py` | 追加 | `reset()` メソッド |
| `src/cryptbot/backtest/engine.py` | 修正 | `run_walk_forward()` 内に `reset()` 呼び出し追加 |
| `tests/test_risk/test_kill_switch.py` | 追加 | `reset()` テスト 2 件 |
| `tests/test_backtest/test_engine.py` | 追加 | WF 窓間 KillSwitch 分離テスト 1 件 |

合計: 663 → **666 tests** 見込み（+3 件）

---

## 完了条件

- [ ] `python -m pytest -q` が全通過
- [ ] `run_walk_forward(threshold=3.0)` で W02 以降に空でない BacktestResult が返る
- [ ] Live/Paper の KillSwitch 動作に影響なし（`reset()` は WF 内部のみ呼び出し）
