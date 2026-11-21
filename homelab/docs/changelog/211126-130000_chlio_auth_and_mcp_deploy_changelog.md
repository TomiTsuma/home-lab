# Changelog: Chlio authentication + MCP server deployed (minos) and wired into Open WebUI

**Date:** 2026-11-21
**Hosts:** `minos` (Chlio, container `clio-app`), `aeacus-dl` (Open WebUI)
**Code:** Chlio repo, local branch `feat/auth-gate` (2 commits, not pushed). Full write-ups live in the Chlio repo:
`docs/changelog/211126-110844_auth_gate_changelog.md` and `211126-124500_mcp_server_changelog.md`.

## Why

`https://chlio.ereuna.org` served its whole API without authentication (read/overwrite everything, mint Firebase
tokens for any email, leak the stored Nextcloud password through its proxy). The owner also wanted to use Chlio from
Claude Desktop and Open WebUI.

## What was deployed

1. **Release 1 (auth):** Google sign-in screen (server-side OAuth + PKCE, works on iPad), cookie sessions, access
   tokens (`chlio_pat_...`), allow-list `CHLIO_ALLOWED_EMAILS=tommytsuma7@gmail.com,tomitsuma@ereuna.org`,
   Nextcloud proxy fixed, Firebase removed. Added the redirect URI
   `https://chlio.ereuna.org/api/auth/login/google/callback` to Google OAuth client `281104290097-h1lo0...`.
2. **Release 2 (MCP):** state revision + `If-Match`, three-way merge and polling in the browser, `POST /mcp` with 26 tools.
   The `app_state` table gained a `revision` column (additive migration).
3. **Open WebUI:** MCP server `chlio` registered (bearer token `open-webui`, 365 days), enabled by default on
   qwen3.5:9b and the Claude models, not on the 16k-context local models (the schemas would not fit).

## How it was deployed

Built on minos from the tree (frontend built there with its `.env`, as for earlier releases; minos is not logged into
Docker Hub, so nothing was pushed). Every image was tested before the switch: unit tests, an end-to-end suite against
the built image (fake credentials, throwaway volume), and real-browser checks.

| Image tag | Meaning |
|---|---|
| `ttsu/chlio:latest` = `ttsu/chlio:mcp-1` | live (id `a938cb0aa4d4`) |
| `ttsu/chlio:auth-gate` | release 1, rollback target (`c3764a0c6d87`) |
| `ttsu/chlio:pre-auth` | the original image from 2026-11-08 (`b7f09e1c113c`) |

Rollback: `docker tag ttsu/chlio:auth-gate ttsu/chlio:latest && cd ~/Chlio && docker compose up -d`. The `revision` column is
ignored by the older images.

## Verified live after the switch (from the public internet)

Every data route returns 401 anonymously (including the old token minter); `/mcp` needs a token; MCP `initialize`
works through Cloudflare; the migration added `revision` and the state (837,022 bytes) was unchanged; Google accepts the
new redirect URI; Open WebUI's own MCP client connects to the live server in ~0.7 s and lists 26 tools.

## Not verified

A real Google sign-in (needs the owner), a real Claude Desktop session (the `mcp-remote` bridge it uses was tested),
the Jira tools against a real Jira, and Google-Calendar event creation.

## Follow-ups for the owner

- **Rotate the Nextcloud password** (the old proxy would send it to any host a caller named).
- `~/Chlio/.env` on minos is world-readable (mode 644) and holds the Jira token, Nextcloud password and Firebase key:
  `chmod 600 ~/Chlio/.env`. `FIREBASE_*` is no longer used by Chlio and can be removed.
- Re-subscribe any calendar app that used the old public `feed.ics` URL (the new URL carries a secret key).
- `~/Chlio` is still on `main`: push/merge the branch and `git pull` before ever running `docker compose up --build`.
- Optional: push the images to Docker Hub (`docker login` as `ttsu`; `docker push ttsu/chlio:mcp-1 ttsu/chlio:latest`).
  `TomiTsuma/notes` is a public repo and the changelogs describe the old vulnerabilities, so review before pushing.
- Delete `~/Chlio/.env.bak-pre-auth-*` and the volume file `/data/clio.db.bak-pre-mcp` once satisfied.
- Tokens: `open-webui` expires in 365 days. Create one for Claude Desktop in Chlio (Account -> Access tokens).
