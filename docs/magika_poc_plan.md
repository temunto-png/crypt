# Magika PoC 計画

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## 目的

`google/Magika`（AI-powered file type detection）を、このプロジェクトの以下のユースケースで評価する：

1. `claude-mem` / RAG へのファイル取り込み前の種別判定
2. ファイルアップロードやドキュメント処理ワークフローの安全確認

---

## スコープ

| 対象 | 内容 |
|---|---|
| 評価期間 | claude-mem Phase 1 完了後、1〜2 週間 |
| 評価環境 | ローカル開発端末 |
| 評価モード | advisory mode のみ（blocking には使わない） |
| 対象ファイル | プロジェクト内の Parquet / SQLite / YAML / Python / CSV ファイル |

---

## 環境準備

```bash
# Python API 経由でインストール
pip install magika

# または CLI
pip install magika[cli]

# バージョン確認
magika --version
```

---

## PoC タスク

### M-01: 基本動作確認

```bash
# プロジェクト内ファイルの種別判定
magika src/cryptbot/config/settings.py
magika data/btc_jpy_1h.parquet
magika tests/fixtures/sample.db
```

確認項目：
- [ ] Python ファイルを正しく `python` と判定する
- [ ] Parquet ファイルを正しく識別する
- [ ] SQLite ファイルを正しく `sqlite` と判定する

---

### M-02: Precision / Recall 測定

1. テスト corpus として以下のファイルタイプを各 5 件用意する：

| ファイルタイプ | 用途 |
|---|---|
| `.py` | Python ソースコード |
| `.yaml` | 設定ファイル |
| `.parquet` | OHLCV データ |
| `.db` (SQLite) | データベース |
| `.csv` | 出力データ |
| `.json` | API レスポンス等 |
| `.txt` | ログ |

2. 正解ラベルを手動で付与した後、Magika の判定と比較する

```python
from magika import Magika

m = Magika()
results = m.identify_paths([path1, path2, ...])
for r in results:
    print(r.path, r.output.ct_label, r.output.score)
```

3. Precision / Recall を計算する

**合否基準:**
- PASS: 対象 7 タイプで Precision ≥ 0.90、Recall ≥ 0.85
- CONDITIONAL: 一部タイプで基準未達 → 対象ファイルタイプを絞って使う
- FAIL: 誤検出が多く advisory としても使えない

---

### M-03: 未知ファイルの挙動確認

1. 拡張子なしのファイル、誤った拡張子のファイルを用意する
2. Magika の判定と confidence score を確認する

```bash
cp src/cryptbot/config/settings.py /tmp/unknown_file
magika /tmp/unknown_file
```

確認項目：
- [ ] 低 confidence の場合に `unknown` または `generic` を返す
- [ ] 誤った拡張子のファイルを内容から正しく判定する

---

### M-04: claude-mem 連携シナリオ確認

claude-mem がファイルを取り込む前に Magika でフィルタリングするシナリオ：

```python
from magika import Magika

ALLOWED_TYPES = {"python", "yaml", "json", "text", "csv"}

m = Magika()

def is_safe_to_ingest(file_path: str) -> bool:
    result = m.identify_path(file_path)
    label = result.output.ct_label
    score = result.output.score
    if label not in ALLOWED_TYPES:
        print(f"BLOCKED: {file_path} detected as {label} (score={score:.2f})")
        return False
    return True
```

確認項目：
- [ ] `.py` / `.yaml` / `.json` が `ALLOWED_TYPES` 内に判定される
- [ ] バイナリファイル（例: ELF、ZIP）が BLOCKED される
- [ ] 実行速度が許容範囲内（1 ファイルあたり 100ms 以内を目安）

---

## 成功条件

| 条件 | 基準 |
|---|---|
| 基本動作確認 | M-01 全項目 PASS |
| Precision / Recall | 対象 7 タイプで Precision ≥ 0.90 |
| 低 confidence の挙動 | unknown / low-score ファイルを誤って許可しない |
| 速度 | 1 ファイルあたり 100ms 以内 |
| 実用性 | advisory mode として有用な信号を出せる |

---

## 撤退条件

- 主要ファイルタイプで Precision < 0.80
- 実行速度が 1 ファイルあたり 1 秒超
- ライセンス上の問題（Apache-2.0 確認済みだが変更があった場合）

---

## 次アクション（PoC 完了後）

- [ ] 結果をこのファイルの「評価結果」セクションに記録する
- [ ] 本番採用する場合は `docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md` の Magika セクションを更新する
- [ ] 採用しない場合は撤退理由を記録する

---

## 評価結果（記入欄）

```
実施日: YYYY-MM-DD
実施者:
M-01: [PASS / FAIL]
M-02: Precision=X.XX, Recall=X.XX  判定: [PASS / CONDITIONAL / FAIL]
M-03: [PASS / FAIL]
M-04: [PASS / FAIL]
総合判定: [採用 / 条件付き採用 / 不採用]
備考:
```
