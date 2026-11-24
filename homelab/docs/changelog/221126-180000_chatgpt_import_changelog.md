# ChatGPT conversations imported into Open WebUI (2026-11-22)

**Request:** import `Downloads\chatgpt_conversations` (ChatGPT export) into Open WebUI, attachments included.

## Result
- 993 chats, 17,494 messages imported on aeacus-dl (989 conversations + 4 recovered fragments), Jan 2023 to Nov 2026, tag `chatgpt-import`.
  Counts match the source conversion exactly (messages, `chat_message` rows, no duplicate `chatgpt_id`), `pragma integrity_check` ok.
- 174 images embedded inline, 24 other files uploaded (process=false), 60 missing attachments replaced by a visible note.
- Verified in the real UI (headless browser, short-lived admin token): images render, file chip shows, branch switcher shows; sidebar page 1 loads
  in 0.04 s, full-text search 2-5 s. Database 14 MB -> 228 MB.

## How
New `ai/open-webui/chatgpt-import/import_chatgpt.py` (own code): rebuilds each conversation from `parent` pointers, drops hidden reasoning and
citation markers, keeps dates/model names, uses `POST /api/v1/chats/import` in batches, idempotent via `meta.chatgpt_id`, has `--dry-run`,
`--only`, `--limit` and `--rollback`. Flow: dry run (local, then inside the container) -> DB backup -> 3-chat trial -> UI check -> full run
(about 6 minutes) -> verification -> removal of the staging copies of the export.

## Findings that shaped it
- 480 visible messages (2.7%, 102 conversations) are not reachable from `current_node`: the export's `children` lists are incomplete. Dropping
  them would have lost real content (some even from other topics than the chat title), so they become branches or fragment chats.
- First trial stored images as `{"type":"image","url":"/api/v1/files/<id>/content"}`; the UI requested `/api/v1/files//api/v1/files/<id>/content`
  (it treats a relative url as a file id) and got the SPA HTML. Fixed by embedding images as data URIs; the trial was rolled back with `--rollback`
  (server returned to 16 chats / 5 files) and redone.
- 863 hidden `thoughts`/`reasoning_recap` entries are intentionally not imported.

## Not done / to know
- ChatGPT model ids (gpt-4o, gpt-5, ...) do not exist in Open WebUI: read-only history unless you pick another model.
- Non-image attachments are chips only (not processed for RAG). 60 attachments were not in the export.
- Unrelated, pre-existing: Open WebUI logs `Cannot connect to host host.docker.internal:11434` every ~10 s since the v0.13.4 upgrade (2026-11-21) from a stale
  Ollama connection entry; host Ollama itself answers (HTTP 200). Not touched.
- DB backup `webui.db.bak-pre-chatgpt-import-20261122-030613` is kept in the open-webui volume.
