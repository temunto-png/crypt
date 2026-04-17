# open-agents Threat Model

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## 対象

`vercel-labs/open-agents` — Cloud 上で coding agent を動かす参照実装。

---

## 評価前提

- 本番 repo への適用は行わない
- 検証 repo（read-only または sandbox）のみを対象とする
- GitHub token は最小権限に限定する
- PR は draft のみ、自動 merge は禁止する

---

## 1. アセット一覧

| アセット | 機密度 | 説明 |
|---|---|---|
| GitHub token | 高 | repo アクセスに使用するトークン |
| ソースコード | 中 | 検証 repo のコード |
| Issue / PR 内容 | 中 | プロジェクト情報が含まれる可能性 |
| ローカル環境変数 | 高 | API key 等が含まれる可能性 |
| cloud agent の実行ログ | 中 | 処理内容が含まれる |

---

## 2. 脅威一覧

### T-01: GitHub token 権限の過剰付与

**説明:** agent に渡す GitHub token の scope が広すぎると、意図しない repo 操作（write, delete, push to protected branch）が可能になる。

**影響:** 高 — 本番 repo の破壊的変更、secret の漏洩

**対策:**
- [ ] token scope を `repo:read`, `pull_requests:write`, `issues:read` のみに限定する
- [ ] Personal Access Token ではなく GitHub Apps（最小権限）を使う
- [ ] 検証 repo のみにアクセス可能な token を発行する

---

### T-02: Sandbox Escape

**説明:** cloud agent が実行する code/shell command がサンドボックス外のリソースへアクセスする。

**影響:** 高 — ネットワーク外部への通信、ファイルシステムの破壊

**対策:**
- [ ] agent の実行環境をネットワーク制限付きコンテナ（egress 制限）に限定する
- [ ] ファイルシステムアクセスを検証 repo のみに制限する
- [ ] `rm -rf` 等の破壊的コマンドを allowlist 外とする

---

### T-03: 自動 merge による未検証コードの本番投入

**説明:** agent が生成した PR が自動マージされ、未検証コードが本番に入る。

**影響:** 高 — バグ・セキュリティ脆弱性の混入

**対策:**
- [ ] 自動 merge を Branch Protection Rules で完全に禁止する
- [ ] agent が作成できるのは draft PR のみに制限する
- [ ] 人間のレビューと approve を必須にする

---

### T-04: Secret の cloud agent への漏洩

**説明:** agent が issue / PR / code を処理する中で、`.env` や config ファイルに含まれる secret を外部 LLM に送信する。

**影響:** 高 — API key / 認証情報の流出

**対策:**
- [ ] 検証 repo に secret を置かない（`.env.example` のみ）
- [ ] secret scanning を検証 repo に適用する
- [ ] agent の入出力ログを監視し、secret パターンを検出する

---

### T-05: agent action の監査不能

**説明:** agent が実行した操作（API コール、ファイル変更、コミット）のログが残らない。

**影響:** 中 — インシデント発生時の追跡不能

**対策:**
- [ ] agent のすべての action（tool call, API request, commit）を audit log に記録する
- [ ] 保存先は agent 実行環境外に置く
- [ ] ログの保持期間を最低 30 日にする

---

### T-06: GitHub token の漏洩

**説明:** token が cloud agent の実行環境から漏洩する（ログへの出力、環境変数のダンプ等）。

**影響:** 高 — repo の不正アクセス・改ざん

**対策:**
- [ ] token を環境変数として注入し、コードにハードコードしない
- [ ] ログから token パターン（`ghp_`, `github_pat_`）を redact する
- [ ] token の有効期限を短く設定する（最大 7 日）
- [ ] 漏洩検知時の即時 revoke 手順を文書化する

---

### T-07: PR の過剰スコープ変更

**説明:** agent が指示外のファイルも変更した PR を作成する。

**影響:** 中 — 意図しないコード変更のレビュー漏れ

**対策:**
- [ ] PR の diff を人間がレビューする前に approve しない
- [ ] agent へのタスク指示を 1 issue につき 1 つに限定する
- [ ] agent の変更ファイル数に上限を設ける（例: 5 ファイル以内）

---

## 3. PoC 開始前チェックリスト

- [ ] 検証 repo を本番 repo から完全に分離した
- [ ] GitHub token scope を最小権限に設定した（T-01）
- [ ] 自動 merge を Branch Protection Rules で禁止した（T-03）
- [ ] 検証 repo に secret がないことを確認した（T-04）
- [ ] audit log の保存先を設定した（T-05）
- [ ] token の漏洩 revoke 手順を文書化した（T-06）
- [ ] PoC の成功条件と撤退条件を明文化した

---

## 4. 成功条件

| 条件 | 基準 |
|---|---|
| PR 品質 | 生成 PR が人間レビューに耐える（無関係変更なし） |
| Secret 非漏洩 | audit log に secret パターンが含まれない |
| Sandbox 制限 | 検証 repo 外へのアクセスがない |
| 撤退可能性 | 問題発生時に token revoke と agent 停止を 5 分以内に実行できる |

---

## 5. 撤退条件

- token scope の最小化が技術的に不可能な場合
- audit log が取得できない場合
- sandbox escape の修正手段がない場合
- PR の自動 merge を完全に防げない場合

---

## 評価結果（記入欄）

```
実施日: YYYY-MM-DD
実施者:
PoC 対象 repo:
GitHub token scope:
T-01〜T-07 各対策の実施状況:
成功条件の達成状況:
総合判定: [採用（sandbox PoC に進む） / 保留 / 不採用]
備考:
```
