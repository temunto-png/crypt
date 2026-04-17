# claude-mem 対象 repo / 除外 repo レジストリ

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## 登録方法

- 新規 repo を追加・変更する場合はこのファイルを直接編集する
- 変更時は「最終更新日」を更新する
- 除外理由は具体的に記載する（「機密」だけでなく「顧客 PII 含む」「秘密鍵ローテーション作業が頻繁」など）

---

## 導入対象 repo

| repo パス / URL | owner | 削除責任者 | 追加日 | 備考 |
|---|---|---|---|---|
| C:/tool/claude/crypt | temunto (temunto@gmail.com) | temunto (temunto@gmail.com) | 2026-04-17 | 初期 PoC 対象 |

---

## 除外 repo（`CLAUDE_MEM_EXCLUDED_PROJECTS` に設定する）

<!-- 追加候補: 確認後に除外理由・登録日を記入すること -->

| repo パス / URL | 除外理由 | 登録者 | 登録日 |
|---|---|---|---|
| C:/tool/NEM Wallet-2.4.7-optin | 秘密鍵・ウォレット credentials が含まれる可能性 | temunto | 2026-04-17 |
| C:/tool/NanoWallet-2.3.2-win64 | 秘密鍵・ウォレット credentials が含まれる可能性 | temunto | 2026-04-17 |
| C:/tool/claude/ai-diagnosis-platform | 診断データ・PII が含まれる可能性（要確認） | temunto | 2026-04-17 |
| C:/tool/claude/Kink Vision | プライバシー性の高いデータが含まれる可能性（要確認） | temunto | 2026-04-17 |

---

## 削除責任者

| 担当範囲 | 担当者名 / 連絡先 | 代替担当者 |
|---|---|---|
| 個人端末上の `~/.claude-mem/` データ | temunto (temunto@gmail.com) | なし（個人運用） |
| チーム共有 DB（将来） | 未定 | 未定 |

---

## `CLAUDE_MEM_EXCLUDED_PROJECTS` 設定例

```bash
# ~/.bashrc または ~/.zshrc に追記
export CLAUDE_MEM_EXCLUDED_PROJECTS="/path/to/repo1:/path/to/repo2"
```

Windows (PowerShell profile):

```powershell
$env:CLAUDE_MEM_EXCLUDED_PROJECTS = "C:/work/client-project;C:/work/infra-secrets"
```

---

## 保存期間ポリシー

| データ種別 | 保存期間 | 削除方法 |
|---|---|---|
| SQLite (`claude-mem.db`) | 30 日 | 手動または cron で `~/.claude-mem/claude-mem.db` をクリア |
| Chroma ベクトル DB | 30 日 | `~/.claude-mem/chroma/` ディレクトリを削除 |
| corpora | 30 日 | `~/.claude-mem/corpora/` ディレクトリを削除 |

---

## ライセンス確認記録

| 確認項目 | 担当者 | 確認日 | 結論 |
|---|---|---|---|
| AGPL-3.0 社内利用への適用 | （記入する） | （記入する） | （記入する） |
| `ragtime` PolyForm Noncommercial 商用該当有無 | （記入する） | （記入する） | （記入する） |

---

最終更新日: 2026-04-17（Phase 0 記入）
