# andrej-karpathy-skills 相当ルール — 最小差分提案

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md
対象: ~/.claude/CLAUDE.md または C:/tool/claude/crypt 配下の CLAUDE.md（新規作成の場合）

---

## 概要

`forrestchang/andrej-karpathy-skills` の主眼は以下の 4 点：

1. 曖昧なまま実装しない
2. 過剰設計を避ける（YAGNI）
3. 無関係変更をしない（scope を守る）
4. 成功条件を明確化してから実装し、完了後に検証する

これらは `claude-mem` が蓄積する memory の品質を直接左右する。agent の行動品質が低いと、誤った判断・無関係変更・失敗した試行が memory に記録され、次セッション以降に悪影響を与える。

---

## 現在の ~/.claude/CLAUDE.md との差分確認

既存ルール（グローバル CLAUDE.md から抜粋）で既にカバーされている項目：

- `不明な点や矛盾がある場合は推測で実装せず、直ちにユーザーへ確認を求める` → 曖昧な実装禁止に相当
- `Don't add features, refactor, or introduce abstractions beyond what the task requires.` → YAGNI に相当
- `Don't add error handling, fallbacks, or validation for scenarios that can't happen.` → 過剰実装禁止に相当
- `作業完了前に変更内容を自分で確認する（verification-before-completion スキル参照）` → 成功条件確認に相当

**既存ルールで十分カバーされている。** 重複追加は不要。

---

## 追加提案（未カバー項目のみ）

以下の 2 点が既存ルールにない。`crypt` プロジェクト配下の CLAUDE.md として追加することを推奨する。

### 追加案 A: スコープの明示

```markdown
# Scope discipline
- タスクを開始する前に「変更してよいファイル」と「変更してはいけないファイル」を自分に問う
- タスクと無関係な改善・cleanup・リファクタリングは実施しない（別 issue にする）
```

### 追加案 B: 成功条件の事前定義

```markdown
# Success criteria
- 実装前に「何をもって完了とするか」をユーザーと合意するか、自分で明示する
- 既存テストが壊れていないこと、新規テストが通ること、の両方を完了条件に含める
```

---

## 適用先の推奨

| 追加場所 | 理由 |
|---|---|
| `C:/tool/claude/crypt/CLAUDE.md`（新規作成） | crypt プロジェクト固有の agent 行動制約として管理する |
| `~/.claude/CLAUDE.md` への追記 | 全プロジェクト共通にする場合。重複になる可能性があるので不要かもしれない |

既存グローバルルールで主要項目はカバー済みのため、**プロジェクト CLAUDE.md への 2 項目追加が最小差分として妥当**。

---

## 適用判断

- [ ] 追加案 A（スコープ明示）を `crypt/CLAUDE.md` に追加する
- [ ] 追加案 B（成功条件）を `crypt/CLAUDE.md` に追加する
- [ ] 既存グローバルルールで十分と判断し、追加しない

適用する場合は、このファイルを削除して変更を CLAUDE.md に直接反映すること。
