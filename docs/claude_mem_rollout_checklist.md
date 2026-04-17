# claude-mem 導入チェックリスト

作成日: 2026-04-17
参照: docs/ai_agent_tool_adoption_review_integrated_for_claude_code.md

---

## Phase 0: 導入前準備チェック

### 対象 / 除外設定

- [x] 導入対象 repo を列挙した（`docs/claude_mem_repo_registry.md` 参照）— crypt 追加済み（2026-04-17）
- [x] 除外 repo を列挙した（同上）— NEM Wallet / NanoWallet / ai-diagnosis-platform / Kink Vision 追加済み（要確認）
- [x] `CLAUDE_MEM_EXCLUDED_PROJECTS` を環境変数またはシェル設定に追加した — PowerShell profile に追記済み（2026-04-17）
- [x] 除外設定が実際に有効か動作確認した — worker-service.cjs の除外ロジック（session-init / observation / file-context 各 hook で cwd チェック）を確認済み（2026-04-17）

### Worker 設定

- [x] worker host が `127.0.0.1` であることを確認した — `~/.claude-mem/settings.json` で `CLAUDE_MEM_WORKER_HOST: "127.0.0.1"` 確認済み（2026-04-17）
- [x] `0.0.0.0` または LAN bind になっていないことを確認した — 127.0.0.1 固定を確認済み
- [x] デフォルト port `37777` の競合有無を確認した — 競合なし（2026-04-17 確認）
- [x] port 競合時の変更手順を文書化した — `docs/claude_mem_rollback_and_deps.md` に記載

### Provider / 外部送信制限

- [x] provider を Claude のみに設定した — `CLAUDE_MEM_PROVIDER: "claude"` 確認済み（2026-04-17）
- [x] Gemini / OpenRouter 等の追加 provider が無効であることを確認した — API key 空、provider=claude のみ確認済み（2026-04-17）
- [x] 送信先 LLM が社内規程・顧客契約の範囲内であることを確認した — 個人利用のため制約なし

### データ保存・削除

- [x] データ保存先ディレクトリを明示した — `docs/claude_mem_repo_registry.md` 保存期間ポリシー欄に記載
  - `~/.claude-mem/claude-mem.db`
  - `~/.claude-mem/chroma`
  - `~/.claude-mem/corpora`
- [x] 保存期間ポリシーを決めた — 30 日（`docs/claude_mem_repo_registry.md` 参照）
- [x] 手動削除手順を文書化した — `docs/claude_mem_rollback_and_deps.md` に記載
- [ ] 手動削除手順を実際に実行して確認した — Phase 1 期間中に実施する
- [x] 削除責任者を指定した — temunto (temunto@gmail.com)（`docs/claude_mem_repo_registry.md` 参照）

### Secret / 機密対策

- [ ] secret scanning ツール（truffleHog 等）を保存先ディレクトリに対して実行できる状態にした — インストール後に確認する
- [x] `<private>...</private>` タグの使い方を開発者に共有した — 個人利用のため本人認識済み
- [x] API key / secret が出る作業時の一時停止手順を決めた — `CLAUDE_MEM_EXCLUDED_PROJECTS` に追加または一時的に全除外

### ライセンス / 法務

- [ ] AGPL-3.0 の社内利用への適用方針を法務に確認した — 個人利用 PoC のため暫定 OK、商用展開時は要確認
- [ ] `ragtime` 配下 `PolyForm Noncommercial` の commercial use 該当有無を確認した — 個人・非商用利用のため暫定 OK
- [x] 改変・配布・ネットワーク利用時の義務を確認した — 改変・配布なし、ネットワーク送信先は Claude のみに制限する
- [ ] 確認結果を記録した — 個人・非商用 PoC のため暫定 OK。`docs/claude_mem_repo_registry.md` ライセンス確認記録欄に記入すること

### Supply Chain

- [x] Bun / uv のインストールスクリプトを事前にレビューした — Bun は winget 経由（v1.3.12）でインストール済み。uv は claude-mem 初回動作時に自動インストール（2026-04-17）
- [ ] 依存パッケージのバージョンを固定した — `~/.claude-mem/settings.json` で model バージョン等を固定済み。npm パッケージは 12.1.6 で固定
- [x] 開発者端末の Claude 設定変更を rollback できる手順を作成した — `docs/claude_mem_rollback_and_deps.md` に記載、バックアップ `~/.claude/settings.json.backup-before-claude-mem-2026-04-17` 作成済み

### Hook 確認

- [x] `plugin/hooks/hooks.json` の発火タイミングを確認した — Setup / SessionStart / UserPromptSubmit / PostToolUse(全) / PreToolUse(Read のみ) / Stop / SessionEnd（2026-04-17）
- [ ] `PreToolUse` の file context 注入がノイズにならないか確認した — Phase 1 運用中に観察する
- [ ] `PostToolUse` の tool output 保存に secret が混入しないか確認した — SL-02 テストで確認する（Phase 1）
- [ ] `Stop` / `SessionEnd` の要約内容に機密が含まれないか確認した — Phase 1 運用中に観察する

---

## Phase 1: 個人導入チェック（1〜2 週間）

- [ ] Phase 0 の全チェックが完了している
- [ ] 非機密 repo 1 つを対象に claude-mem を有効化した
- [ ] memory on/off で同じタスクを比較した（`docs/claude_mem_golden_tasks.md` 参照）
- [ ] secret leakage test を実行した（`docs/claude_mem_security_tests.md` 参照）
- [ ] memory poisoning test を実行した（同上）
- [ ] 保存データを実際に削除できることを確認した
- [ ] worker 障害時の disable 手順を確認した

### Phase 1 完了判定

- [ ] 再説明時間が減っている（体感または計測）
- [ ] 同じ調査の繰り返しが減っている
- [ ] secret が保存されていない、または検知・削除できた
- [ ] memory が原因の明確な品質劣化がない

---

## Phase 2: 小チーム導入チェック（2〜4 週間、2〜5 人）

- [ ] Phase 1 の完了判定を全てパスした
- [ ] インストール手順を固定・共有した
- [ ] チーム全員が削除手順を実行できることを確認した
- [ ] port `37777` 競合や worker 障害時の対応を共有した
- [ ] 以下の指標を測定した
  - [ ] 過去文脈の再説明時間
  - [ ] 同じファイル調査の再発回数
  - [ ] PR の無関係差分数
  - [ ] memory 注入が原因の誤判断数

### Phase 2 完了判定

- [ ] 個人導入と同等以上の効果がある
- [ ] チームでトラブルシュートできる
- [ ] Security / Legal / SRE の指摘事項が全て解消した

---

## 運用中チェック（定期）

### 月次

- [ ] 保存データの肥大化を確認した（SQLite DB サイズ）
- [ ] secret scanning を実行した
- [ ] 保存期間ポリシーに従って古いデータを削除した

### 四半期

- [ ] ライセンス状況・法務確認を更新した
- [ ] 依存パッケージのアップデートとバージョン固定を見直した
- [ ] 除外 repo リストを更新した

---

## 緊急停止手順

1. `CLAUDE_MEM_EXCLUDED_PROJECTS` に対象 repo を追加する
2. worker プロセスを停止する
3. `~/.claude-mem/` 配下のデータを確認・削除する
4. hook の無効化または削除を行う
5. 削除責任者に報告する
