# cognee 比較 PoC 計画

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## 目的

`topoteretes/cognee`（AI Agent Memory / Knowledge Engine）と `claude-mem` の役割分担を明確化する。
claude-mem がカバーしない「設計書・仕様・ドキュメント・知識グラフ」領域で cognee が追加価値を出せるか評価する。

---

## 役割分担の仮説

| ツール | 想定する強み | このプロジェクトでの用途例 |
|---|---|---|
| `claude-mem` | 開発セッション記憶（tool usage, 観測, 要約） | 前回バグ修正の文脈、設計判断の記憶 |
| `cognee` | 文書・仕様・知識グラフ・GraphRAG | `docs/` 配下の設計書を質問で検索、エラー原因を知識グラフで追跡 |

---

## スコープ

| 対象 | 内容 |
|---|---|
| 評価期間 | claude-mem Phase 2 完了後、2〜3 週間 |
| 評価環境 | ローカル開発端末（Docker または pip） |
| 評価データ | `docs/` 配下のドキュメント（master_design.md, risk_policy.md 等） |
| 評価モード | 読み取り専用クエリのみ |

---

## 環境準備

```bash
pip install cognee

# または Docker
docker pull topoteretes/cognee:latest
```

---

## PoC タスク

### C-01: ドキュメント取り込みと検索

1. `docs/` 配下のドキュメントを cognee に取り込む：

```python
import cognee

await cognee.add("docs/master_design.md", dataset_name="crypt_docs")
await cognee.add("docs/risk_policy.md", dataset_name="crypt_docs")
await cognee.cognify()
```

2. 質問クエリを実行する：

```python
results = await cognee.search("KillSwitch の発動条件は何ですか？", query_type="INSIGHTS")
```

**合否判定:**
- PASS: `risk_policy.md` から正確な発動条件を返す
- FAIL: ドキュメントと無関係な情報を返す、または応答なし

---

### C-02: claude-mem との比較クエリ

同一の質問を claude-mem（memory ON）と cognee の両方で実行し、回答品質を比較する。

比較クエリ例：

| クエリ | 期待する回答源 |
|---|---|
| 「nonce 再生成が必要な理由は？」 | claude-mem（セッション記憶）> cognee |
| 「RiskManager のパラメータ一覧を教えてください」 | cognee（設計書）> claude-mem |
| 「前回セッションで修正した Important A の内容は？」 | claude-mem > cognee |
| 「CVaR の計算方法を教えてください」 | cognee（設計書）> claude-mem |

**計測ポイント:**
- 各クエリで両ツールの回答正確性（1〜5 段階）を評価
- 応答時間を計測

---

### C-03: 知識グラフの可視化確認

```python
# cognee のグラフ可視化
await cognee.visualize()
```

確認項目：
- [ ] `master_design.md` のエンティティ（LiveEngine, KillSwitch, RiskManager 等）が正しくノードとして抽出される
- [ ] エンティティ間の関係（A が B を呼び出す等）が正しくエッジとして表現される

---

### C-04: 個人情報・機密データ非混入確認

取り込み対象ドキュメントに API key 等が含まれていないことを事前確認する：

```bash
grep -r "API_KEY\|SECRET\|PASSWORD\|sk-" docs/
```

**合否判定:** 何も検出されなければ取り込み可

---

## 成功条件

| 条件 | 基準 |
|---|---|
| ドキュメント検索精度 | 対象クエリで正確な回答率 ≥ 0.80 |
| claude-mem との差別化 | 設計書クエリで cognee が優位なケースを 2 件以上確認 |
| 個人情報非混入 | 取り込みデータに機密情報なし |
| セットアップ容易性 | ローカル環境で 1 時間以内に動作確認完了 |

---

## 撤退条件

- セットアップに 1 日以上かかる
- ドキュメント検索精度が claude-mem と比較して有意な差がない
- 機密文書の取り込みリスクが高く、除外設定が難しい
- Vector DB / Graph DB の運用コストが過大

---

## 評価結果（記入欄）

```
実施日: YYYY-MM-DD
実施者:
C-01: [PASS / FAIL]
C-02: claude-mem 優位クエリ数=X, cognee 優位クエリ数=X
C-03: [PASS / FAIL]
C-04: [PASS / FAIL]
総合判定: [採用（補完ツールとして） / 不採用 / 保留]
役割分担の結論:
備考:
```
