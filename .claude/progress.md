# 暗号資産自動売買システム — 進捗

## 現在のフェーズ

**Plan A（Paper Trade 前必須修正）完了（2026-04-19）**

- テスト数: **678 passed**（元 666 + 新規 12）
- worktree branch: `feat/plan-a-paper-trade-critical-fixes`（HEAD: `848ade2`、tag: `plan-a-complete`）
- master HEAD: `86416ea`（Plan A は未マージ — master へのマージを次アクションとして実施予定）

---

## 最新作業（2026-04-18）

### 0. 2D グリッドサーチ（threshold × momentum_window）実行完了

**KillSwitch isolation 修正済み**（`make_risk_manager()` L75 に `ks.reset()` 追加）  
**2D グリッドサーチ実装済み**（`MOMENTUM_WINDOW_CANDIDATES = [3, 5, 10, 20]` × `THRESHOLD_CANDIDATES`）

グリッドサーチ結果（32組み合わせ、正 score 抜粋）:

| window | threshold | trades | pnl% | win% | Sharpe | score |
|--------|-----------|--------|------|------|--------|-------|
| **20** | **3.0** | **15** | **+1.51%** | **40.0%** | **0.08** | **0.038** ← 最良 |
| 5 | 2.5 | 17 | +1.20% | 35.3% | 0.05 | 0.031 |
| 3 | 3.0 | 6 | +2.05% | 16.7% | 0.09 | 0.018 |

**最良パラメータ: threshold=3.0, momentum_window=20**

WF 結果（threshold=3.0, momentum_window=20, 12窓）:

| W | 検証期間 | 取引 | 損益% | 勝率 | Sharpe |
|---|---------|------|-------|------|--------|
| W01 | 2022-07〜11 | 16 | +3.31% | 43.8% | 0.48 |
| W02 | 2022-11〜2023-03 | 9 | +3.12% | 44.4% | 0.53 |
| W03 | 2023-03〜07 | 12 | +5.18% | 41.7% | 0.84 |
| W04 | 2023-07〜11 | 4 | +0.40% | 25.0% | 0.14 |
| W05 | 2023-11〜2024-03 | 11 | +11.18% | 27.3% | 1.64 |
| W06 | 2024-03〜07 | 12 | -3.69% | 16.7% | -0.77 |
| W07 | 2024-07〜11 | 10 | -2.74% | 30.0% | -0.55 |
| W08 | 2024-11〜2025-03 | 11 | +2.29% | 36.4% | 0.50 |
| W09 | 2025-03〜07 | 9 | +7.42% | 66.7% | 1.06 |
| W10 | 2025-07〜11 | 3 | -2.18% | 0.0% | -2.47 |
| W11 | 2025-11〜2026-03 | 10 | -6.17% | 10.0% | -1.63 |
| W12 | 2026-03〜04 | 4 | +0.61% | 25.0% | 0.32 |

**合計取引数: 111**（基準 30+ クリア）  
**勝ち窓: 8/12**（前回 window=5 比: 82 trades → 111 trades、6/12 → 8/12）

### 1. Walk-Forward KillSwitch 修正 → master マージ完了

| ファイル | 変更 |
|---------|------|
| `src/cryptbot/risk/kill_switch.py` | `reset()` メソッド追加（インメモリのみ・audit_log 書き込みなし） |
| `src/cryptbot/backtest/engine.py` | `run_walk_forward()` の学習窓前・検証窓前に `reset()` を追加 |
| `tests/test_risk/test_risk.py` | `reset()` テスト 2 件追加 |
| `tests/test_backtest/test_engine.py` | WF 窓間分離テスト 1 件追加 |

**修正前の症状**: W02 以降が空結果（KillSwitch が学習窓で発動すると全後続窓をブロック）

### 2. 15min バックフィル完了

- 取得量: **150,528 bars** (2022-01-01 〜 2026-04-18)
- 保存先: `data/btc_jpy/15min/2022〜2026.parquet`

### 3. グリッドサーチ結果（旧: window=5 固定 → 2D グリッドに更新済み）

旧結果（KillSwitch 汚染あり、window=5 固定）は Section 0 の新結果に置き換え。

### 4. Walk-Forward 結果（現最良: threshold=3.0, window=20）

Section 0 参照。

---

## Plan A 実装サマリー（2026-04-19）

### 実装済みコミット（branch: feat/plan-a-paper-trade-critical-fixes）

| コミット | 内容 |
|---------|------|
| `1326594` | feat(config): PaperSettings/LiveSettings に momentum_threshold/window 追加 |
| `ff4162e` | feat(main): _resolve_strategy に threshold 引数を追加し settings から渡す |
| `6e2c9bd` | fix(paper/live): OHLCV ロード後に normalize() を必ず適用する |
| `1d3ce0a` | fix(fetch): Storage 初期化漏れ修正・--fetch-profile で paper/live 設定を選択可能に |
| `a3e119f` | fix(main): settings.kill_switch.active を起動時に KillSwitch へ反映する + startup_config audit_log 記録 |
| `848ade2` | docs(config): paper セクションに WF採用パラメータを追記 |

### Plan A 完了基準チェック

- [x] `pytest -q` が全テスト PASS（678 passed）
- [x] `--mode paper` で `audit_log` に `signal` イベントが記録される
- [x] `--mode paper` で threshold=3.0, window=20 の strategy が使われる
- [x] `--mode fetch-ohlcv --fetch-profile paper` が `paper.timeframe=15min` でデータ取得
- [x] `--mode fetch-ohlcv` を新規 DB に対して実行してもエラーにならない
- [x] `config.yaml` で `kill_switch.active: true` 設定時に KillSwitch が即発動
- [x] `audit_log` に `startup_config` イベントが戦略名・閾値・window 付きで記録される

## 次のアクション

1. **【必須】Plan A を master へマージ**
   - `git checkout master && git merge --no-ff feat/plan-a-paper-trade-critical-fixes`
   - または PR 経由でマージ

2. **【次フェーズ】Plan B: Paper Trade 運用タスク**
   - Paper trade 起動（`--mode paper`）
   - 2週間モニタリング → Live 移行判断

3. **【任意】直近弱さの分析**
   - W10〜W11（2025-07〜2026-03）4連敗中
   - 市場レジーム変化（volatility 低下など）の可能性あり

---

## リポジトリ

- GitHub: https://github.com/temunto-png/crypt.git（branch: master）
- ローカル: `C:\tool\claude\crypt`
- 未追跡（意図的）: `.claude/`、`docs/superpowers/`

---

## クオンツ分析サマリー（2026-04-18）

| 項目 | 1hour 足 | 15min 足 |
|------|----------|----------|
| 戦略 | MomentumStrategy(threshold=3.0) | MomentumStrategy(threshold=3.0, window=20) |
| momentum window | pct_change(5) | pct_change(20) |
| データ量 | 37,632 bars | 150,528 bars |
| バックテスト全期間取引数 | 13 trades | 15 trades |
| WF 合計取引数 | 13 trades | **111 trades** ← 基準クリア |
| WF 勝ち窓 | — | **8/12** |
| 本番基準 30+ 達成 | ✗ | ✓ |

---

## 主要な設計判断

| 項目 | 決定 |
|------|------|
| 取引所 | bitbank（APIドキュメント品質・OHLCV API充実） |
| 銘柄 | BTC/JPY 現物（Long-only） |
| 時間足 | 15min（1hour から変更、取引頻度改善のため） |
| コスト | Taker 0.1% + スリッページ 0.05% = 0.15% |
| データ保存 | SQLite + Parquet |
| 設定 | YAML + pydantic |
| ログ | loguru (JSON Lines) + SQLite audit_log |
| asyncio 永続プロセス | asyncio.run(engine.run()) |
| 二重発注防止 | BEGIN EXCLUSIVE + CREATED チェック |

---

## claude-mem 導入状況（2026-04-18 時点）

Phase 0〜1 完了。vector search 有効化済み（ChromaDB）。

| テスト | 結果 |
|-------|------|
| SL-01: DB secret スキャン | PASS |
| SL-02: tool output secret 保存確認 | PASS |
| SL-03: 個別 observation 削除 | PASS |
| MP-01: 誤情報注入テスト | PASS |
| MP-03: 有害 memory 削除 | PASS |
