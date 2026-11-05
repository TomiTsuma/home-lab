# 2026-11-05 — Added Lyre and Chlio to the repo (new `apps/` category)

Follow-up to the recreation audit earlier today. Covers 2 of the previously
undocumented custom apps the owner confirmed should be in the repo: Lyre
(on `aeacus-data-handler`) and Chlio (on `minos`). Ereuna/Aether/Harbor and
the rest of `minos`'s stack are still pending (see the earlier changelog
entry's "Not done yet").

## Added — `homelab/apps/` (new top-level category for first-party custom apps)

- `lyre/docker-compose.yml`, `.env.example`, `README.md` — pulled the live
  compose/env-example from `aeacus-data-handler:~/Lyre/`. Source code is
  **not** vendored (it has real secret files alongside it — Google OAuth
  client secret, Firebase Admin SDK key, OAuth token cache, live sqlite DB) —
  documented its Gitea location instead.
- `chlio/docker-compose.yml`, `.env.example`, `README.md` — pulled the live
  compose/env-example from `minos:~/Chlio/`. Source lives on GitHub
  (`TomiTsuma/notes`, not vendored here either).

## Security fixes made while adding Lyre

The live `docker-compose.yml` on `aeacus-data-handler` had two issues not
reproduced in the version committed here — **the live file should be updated
to match**:

1. GlitchTip's `POSTGRES_PASSWORD` and `SECRET_KEY` were hardcoded in
   plaintext in the compose file. Parameterized via `.env` instead (compose
   now refuses to start without them set, rather than falling back to the
   old weak hardcoded values).
2. `ENABLE_OPEN_USER_REGISTRATION` was `"True"` on an internet-facing
   GlitchTip instance (`logs.lyre.qd.je`) — changed to `"False"` in the
   committed version.

## Flagged, not fixed (needs owner action)

- **Lyre's own git remote has a plaintext password embedded in the URL**
  (`.git/config` on `aeacus-data-handler`, pointing at the self-hosted Gitea
  on TrueNAS). Recommend rotating that Gitea account's password and
  switching to an SSH deploy key or personal access token.
- Chlio's `.env.example` has `OLLAMA_URL=http://localhost:11434`, but the
  homelab's actual Ollama instance runs on `aeacus-dl` (VM 101), a different
  host from `minos` (where Chlio runs) — if that integration is actually
  used, the real `.env` likely needs a different host, not `localhost`. Not
  verified (the live `.env` has real values and wasn't read).
- Chlio's git remote repo is named `notes`, not `chlio`/`clio` — confirmed
  this is intentional (renamed project) vs. possibly the wrong remote; not
  re-verified here.
