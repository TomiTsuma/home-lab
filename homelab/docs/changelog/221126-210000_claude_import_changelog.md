# Claude conversations imported into Open WebUI (2026-11-22)

**Request:** import the Claude chats from two claude.ai export manifests (`ereuna.org` and Gmail accounts).

## Result
- 224 chats / 3,601 messages: `ereuna` 50 chats (47 conversations + 2 recovered fragments + 1 design chat), `gmail` 174 (169 + 1 fragment + 4 design chats).
  Counts equal the dry run exactly; no duplicate `claude_uuid`; `chat_message` rows equal messages; integrity ok; database 228 -> 289 MB.
- 9,767 collapsed tool rows, 18 chats with branches, 85 pasted-text attachments uploaded as files (68 distinct), 933 uploaded images/PDFs noted as
  not recoverable. Tags `claude-import`, `claude-ereuna`, `claude-gmail`, `claude-design`.

## Getting the data
The manifests only contain download links. claude.ai answered a scripted download with a Cloudflare bot challenge (403, `cf-mitigated: challenge`),
which was not bypassed; the owner opened the links in a signed-in browser (15 zips, 2 accounts, saved to `Downloads\claude_exports`). The two sets were
told apart by `users.json` / the account uuid in each memories file. Zip integrity (`testzip`) ok.

## Design (own code `chatgpt-import/import_claude.py`)
Tree rebuilt from `parent_message_uuid`; newest message = chat end; other branches native branches or fragment chats. Tool call + result -> one collapsed
`<details>` row (Open WebUI renders it as its own collapsible, not a native element); per-message budget (40k chars: shrink, then summarise) after the
first dry run showed one message of 691,000 chars (453 tool calls) and 96 over 40k. Dropped: thinking, injected prompt blocks, token budget.
Trial of 3 chats (tool calls + attachment, a branch, a design chat) was verified in the real UI before the full run (8/8 checks).

## Incident
The first `scp` of the 26.7 MB archive hung for 100 minutes ("connection reset"; the path had fallen back to a DERP relay at ~430 ms). 23.8 MB had arrived;
the prefix was verified by checksum and only the remaining 3 MB were sent as a separate piece (40 s), then joined and verified against the full checksum.

## Not done / to know
- Not imported: projects (19, 39 docs), memories, frames/artifacts (about 600 MB), artifact_data, claude_code_cloud, feedback. Ask if wanted.
- Model per message is not in the export; chats show model "Claude". Non-image uploads other than pasted text are not in the export.
- Backups kept: `webui.db.bak-pre-chatgpt-import-20261122-030613`, `webui.db.bak-pre-claude-import-20261122-044919` (open-webui volume). Staging copies of the
  export were deleted from the server, container and scratch; `Downloads\claude_exports` (the owner's original zips) is untouched.
