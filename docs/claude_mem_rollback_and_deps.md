# claude-mem: Rollback 手順 / Dependency Review

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## 1. Claude 設定 Rollback 手順

claude-mem のインストールは `~/.claude/settings.json` の hooks セクションを変更する。
問題発生時は以下の手順で元に戻す。

### バックアップの場所

```
~/.claude/settings.json.backup-before-claude-mem-2026-04-17
```

### Rollback 手順

```bash
# 1. バックアップを確認
cat ~/.claude/settings.json.backup-before-claude-mem-2026-04-17

# 2. 現在の設定をさらにバックアップ
cp ~/.claude/settings.json ~/.claude/settings.json.backup-after-claude-mem-$(date +%Y%m%d)

# 3. バックアップに戻す
cp ~/.claude/settings.json.backup-before-claude-mem-2026-04-17 ~/.claude/settings.json

# 4. 確認
cat ~/.claude/settings.json | head -20
```

### claude-mem plugin を無効化する場合

```bash
# プラグインが追加された場合は settings.json の enabledPlugins から削除
# または Claude Code の設定 UI から無効化する
```

### worker プロセスを停止する場合

```bash
# Windows
tasklist | grep -i "claude-mem\|bun"
taskkill /F /IM bun.exe 2>/dev/null
netstat -ano | grep ":37777"
# 表示された PID を kill: taskkill /F /PID <PID>

# Linux/macOS
pkill -f claude-mem
lsof -i :37777
```

### データを完全削除する場合

```bash
rm -rf ~/.claude-mem/claude-mem.db
rm -rf ~/.claude-mem/chroma/
rm -rf ~/.claude-mem/corpora/
rm -rf ~/.claude-mem/
```

---

## 2. Dependency Review

### 確認日: 2026-04-17
### 確認対象: thedotmack/claude-mem (revision 53622b5, version 12.1.6)

### 現在の環境

| ツール | バージョン | 状態 |
|---|---|---|
| node | v24.13.0 | インストール済み |
| npm | 11.6.2 | インストール済み |
| bun | - | **未インストール** |
| uv | - | **未インストール** |

### claude-mem が必要とするランタイム

| ツール | 用途 | インストール前の対応 |
|---|---|---|
| Bun | TypeScript runtime / package manager | インストールスクリプトをレビューしてからインストール |
| uv | Python パッケージ管理（Chroma 等） | 必要になったタイミングでレビューしてからインストール |

### Bun インストール前チェックリスト

- [ ] Bun の公式インストールスクリプト内容を確認する
  - URL: `https://bun.sh/install`
  - 確認ポイント: スクリプトがダウンロードするバイナリのハッシュ検証があるか
- [ ] Bun のバージョンを固定する（`claude-mem` が要求するバージョンを確認）
- [ ] PATH への追加先を確認する（`~/.bun/bin`）
- [ ] アンインストール手順を確認する

### claude-mem npm / bun 依存パッケージ

インストール前に `package.json` を確認し、以下を記録する（インストール後に記入）：

| パッケージ名 | バージョン | ライセンス | 備考 |
|---|---|---|---|
| （インストール後に記入） | | | |

### 主要ライセンス

| ライセンス | 対象 | 商用利用 |
|---|---|---|
| AGPL-3.0 | claude-mem 本体 | 要法務確認 |
| PolyForm Noncommercial | ragtime 配下 | 商用利用不可（個人利用は可） |
| Apache-2.0 / MIT | その他依存パッケージ（想定） | 通常は問題なし |

### 結論（法務確認前の暫定）

個人・非商用利用の PoC であれば AGPL-3.0 / PolyForm Noncommercial の範囲内と考えられる。
商用・社内展開の場合は法務確認必須。

---

## 3. 確認済み事項（Phase 0 時点）

| 項目 | 結果 |
|---|---|
| port 37777 競合 | なし |
| settings.json バックアップ | `~/.claude/settings.json.backup-before-claude-mem-2026-04-17` に保存済み |
| bun インストール状態 | 未インストール（インストール前レビュー必要） |
| uv インストール状態 | 未インストール |
| node/npm | v24.13.0 / 11.6.2 インストール済み |
