# Chlio (aka "Clio")

Self-built personal productivity app, deployed on `minos`. Vite + React
frontend, Express + SQLite (`better-sqlite3`) backend. Integrates Google
Calendar (OAuth2), Jira Cloud, Nextcloud (WebDAV), and a local Ollama
endpoint. Served at `https://chlio.ereuna.org` (per its OAuth redirect URI).

## Source code

Lives in the user's GitHub, **not** vendored into this repo:

```
https://github.com/TomiTsuma/notes.git
```

(Note: the remote is named `notes`, not `chlio`/`clio` — the app was likely
renamed at some point. Confirm this is still the right repo before relying
on it for a rebuild.)

## Deploying

```sh
git clone https://github.com/TomiTsuma/notes.git ~/Chlio   # or wherever
cd ~/Chlio
cp /path/to/this/repo/homelab/apps/chlio/.env.example .env
cp /path/to/this/repo/homelab/apps/chlio/docker-compose.yml .
# fill in .env — see .env.example for what each integration needs
docker compose up -d
```

Exposed on host port `4191` (overridable via `PORT` in `.env`). Data
(SQLite DB etc.) persists in the `clio-data` named volume, mounted at `/data`
inside the container.

## Ollama integration ("Ask Chlio")

Confirmed working end-to-end as of 2026-11-07 (see
`../../docs/changelog/071126-091000_ollama_gpu_and_claude_integration_changelog.md`):

- `OLLAMA_URL=http://192.168.100.17:11434` in the live `.env` on `minos` —
  `aeacus-dl`'s **LAN** address, not `localhost` and not a Tailscale address
  (both hosts share the same LAN, so the direct LAN IP is lowest-latency).
  `.env.example`'s `http://localhost:11434` was wrong for this deployment —
  fixed below.
- `OLLAMA_MODEL='qwen3.5:researcher'` is hardcoded in
  `src/services/ollama.ts` (not an env var) — a custom model built from a
  Modelfile with a detailed "Ask Chlio" system prompt (visual-first:
  Mermaid diagrams, chart JSON blocks, LaTeX math, page-cited claims).
  Rebuild it on `aeacus-dl` with:
  ```sh
  ollama create qwen3.5:researcher -f ~/Chlio/Modelfile
  ```
  The Modelfile itself lives with the Chlio source (`~/Chlio/Modelfile` on
  `minos`, mirrored to `aeacus-dl` to build), not in this repo.
- `server/index.js`'s `/api/ollama` proxy route already handles dual
  candidate base URLs and automatic fallback-model discovery via
  `/api/tags` if the configured model 404s — no changes needed there.
- A cold request (model not yet loaded into the Ollama container's VRAM) can
  take **minutes**, not seconds — see "GPU gotchas" in
  `../../ai/open-webui/README.md`. The Ollama stack sets
  `OLLAMA_KEEP_ALIVE=-1` specifically so this only happens once per Ollama
  container restart, not on every gap in usage.

## Known gaps

- This repo doesn't yet document whether `minos` fronts Chlio with a reverse
  proxy/Cloudflare Tunnel to reach the public `chlio.ereuna.org` URL, or how
  — `minos` does run its own Cloudflare Tunnel client (see
  `../../docs/network-topology.md`), most likely how this is exposed, but
  the tunnel's route config wasn't inspected.
