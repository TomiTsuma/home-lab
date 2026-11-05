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

## Known gaps

- `OLLAMA_URL=http://localhost:11434` in `.env.example` assumes Ollama runs
  on the same host as Chlio. The homelab's actual Ollama instance runs on
  `aeacus-dl` (VM 101), a **different** host from `minos` where Chlio is
  deployed — if Chlio's Ollama integration is actually used, this value
  likely needs to point at `aeacus-dl`'s Tailscale/LAN address instead of
  `localhost`. Not verified against the live `.env` (which has real values
  and wasn't read).
- This repo doesn't yet document whether `minos` fronts Chlio with a reverse
  proxy/Cloudflare Tunnel to reach the public `chlio.ereuna.org` URL, or how
  — `minos` does run its own Cloudflare Tunnel client (see
  `../../docs/network-topology.md`), most likely how this is exposed, but
  the tunnel's route config wasn't inspected.
