# Claude memories, projects and chats into Open WebUI; Gmail read+drafts; qwen3.5:9b findings (2026-11-22)

Details are in `ai/open-webui/README.md` ("Claude memories, projects and chats", "Gmail", "Why qwen3.5:9b sometimes thinks and stops").

## Done and verified
- **Memories:** 77 Claude memory files -> Open WebUI path-based Memory (`claude/ereuna/...`, `claude/gmail/...`), 3 `user` + the rest `context`; integrity ok,
  list/read by path and semantic search verified (e.g. a pricing question returns `coreoutline-pricing.md`). Backup `webui.db.bak-pre-claude-memories-*`.
- **Projects -> Folders:** 17 folders under per-account parents; instructions as system prompt, documents uploaded with processing (all processed, no warnings).
  Trial with one project first, then the rest.
- **Chats -> folders:** 54 conversations (55 chats) moved. Mapping inferred from evidence inside the conversations because the export has no project field:
  memory writes into a project path, the memory snapshot's file-level project listing, project documents named in the chat, project-knowledge-search use.
  Checked against the definite markers ("connected folder" notes, project_knowledge_search) and for agreement between signals. 170 conversations left
  unfiled (no evidence). UI check: folders appear in the sidebar, "DRUG DISCOVERY RESEARCH" lists its 14 chats with their tags.
- **calendar_planner:** return shape changed to `(HTMLResponse, text)` like the Reveal.js tool (confirmed to render inline on 0.13.4); 38 unit tests pass; reinstalled.

## Gmail (installed, waiting for the owner's Google consent)
`gmail:readonly+drafts` (scopes gmail.readonly + gmail.compose, confirmed in the sign-in URL; no send/modify/labels/settings), `send_gmail_message` and
label/filter/modify tools disabled in the server; two Open WebUI connections with allow-lists (`gworkspace` 10 tools, `gmail` 7 tools); `gmail` default-on for the
four Claude models only. NOT yet tested against a real mailbox (needs the consent).

## Findings / not done
- `qwen3.5:9b`: empty answer after a short reasoning block; ~30k tokens of tools; `visualize` skill detour; thinking on. Variant `qwen3.5:9b (agent)` created
  (think off, no skill, lean tools); its end-to-end test was interrupted (network change, then the laptop ran out of memory), so it is NOT validated.
- `claude/ereuna/preferences.md` (a prompt-rewrite routine) is a `user` memory and is injected into every chat: owner to decide whether to demote it.
- The Gmail parent folder was renamed "Claude · Personal" by the owner after creation; re-running `import_claude_projects.py` must use `--account-title Personal`.
- Rollbacks: `import_claude_memories.py --rollback`, `import_claude_projects.py --rollback`, `assign_chats_to_folders.py --rollback`.
