# Lyre

Self-built music player web app, deployed on `aeacus-data-handler` (VM 100).
React/Vite frontend (served via nginx in production) + FastAPI backend, with
Spotify-style search (RapidAPI `spotify81`), YouTube Music and Lidarr
integration, and recommendations. Bundles its own GlitchTip instance
(self-hosted Sentry-compatible error tracking) at `https://logs.ereuna.org`
(migrated from the stale `logs.lyre.qd.je` on 2026-11-05).

### Backend logging → GlitchTip

As of 2026-11-05, the backend's `main.py` (in its own repo, not here) wraps
`sys.stdout`/`sys.stderr` so that **every `print()` call anywhere in the
backend** (it's entirely print-based, no `logging` module usage) is
forwarded to GlitchTip as its own event, in addition to the console output
you'd see in `docker logs`. This is deliberately scoped to the app's own
logger (`propagate=False`) so it does **not** touch `sentry_sdk`'s default
exception-tracking integrations — uvicorn's routine access logs are not
captured, only the app's own print-based diagnostics. See the GlitchTip
project "lyre-backend" (team "lyre", org "aeacus") for the live issue
stream — `shortId`s look like `LYRE-BACKEND-<n>`.

## Source code

Lives in the user's self-hosted Gitea, **not** vendored into this repo (the
app has real secret files alongside its code — a Google OAuth client secret,
a Firebase Admin SDK service account key, a OAuth token cache, and the live
sqlite DB — none of which belong in `home-lab`):

```
http://100.100.133.10:30008/aeacus/lyre.git
```

(Gitea is the `gitea` App on TrueNAS — see `../../infrastructure/truenas/README.md`.)

**Security flag**: as found on 2026-11-05, the live `.git/config` on
`aeacus-data-handler` has this remote with a **plaintext password embedded in
the URL**. Rotate that Gitea account's password and switch to an SSH
deploy key or a personal access token instead of a URL-embedded password.

## Deploying

```sh
git clone http://100.100.133.10:30008/aeacus/lyre.git ~/Lyre   # or wherever
cd ~/Lyre
cp /path/to/this/repo/homelab/apps/lyre/.env.example .env
cp /path/to/this/repo/homelab/apps/lyre/docker-compose.yml .
# fill in .env (RapidAPI key, GlitchTip password/secret/DSNs — see .env.example)
docker compose up -d
```

First boot: GlitchTip needs its own setup — sign up at the public domain
(open registration is on, see below), create a **Team**, create a
**Project** under that team, then copy the generated DSN back into `.env`
as `GLITCHTIP_INTERNAL_DSN` (host swapped to `glitchtip:8000`) /
`VITE_GLITCHTIP_DSN` / `GLITCHTIP_DSN` and restart. This homelab uses team
`lyre`, project `lyre-backend`.

**Critical gotcha**: GlitchTip's `glitchtip/glitchtip:latest` image defaults
to `SERVER_ROLE=web` — it will accept and queue every event (HTTP 200, looks
fine) but **never processes them into visible issues**, because no worker
is running to drain the queue. `GLITCHTIP_EMBED_WORKER: "true"` (already in
this repo's `docker-compose.yml`) runs web + worker + beat in the single
container, which this single-host deployment needs since there's no
separate worker service defined. Without it, `firstEvent` on the project
stays `null` forever and nothing shows up no matter how much you send.

## What changed from the live config when adding this to the repo

The live `docker-compose.yml` on `aeacus-data-handler` (as of 2026-11-05) had
these issues, fixed in the version committed here — **the live file should be
updated to match**:

1. **Hardcoded secrets**: `POSTGRES_PASSWORD` and `SECRET_KEY` for GlitchTip
   were committed in plaintext, as weak/guessable values, directly in the
   compose file itself. Parameterized here via
   `${GLITCHTIP_POSTGRES_PASSWORD}`/`${GLITCHTIP_SECRET_KEY}` (compose
   refuses to start if they're unset, rather than silently falling back to
   a weak default) — **rotate both values on the live deployment**, don't
   just reuse the old ones in the new `.env`.
2. **Open registration**: `ENABLE_OPEN_USER_REGISTRATION` was `"True"` on an
   internet-facing GlitchTip (`logs.lyre.qd.je`) — changed to `"False"` here.
   Create the admin account via `python manage.py createsuperuser` inside the
   `glitchtip` container instead, or re-enable deliberately if that's wanted.
3. The backend's `GLITCHTIP_DSN` was hardcoded to a specific internal DSN
   value directly in the compose `environment:` block (which always
   overrides a same-named `.env` value) — externalized to
   `GLITCHTIP_INTERNAL_DSN` so it's actually configurable via `.env`.

## Known gaps

- Backend requires a Google OAuth client-secret JSON and a Firebase Admin SDK
  service-account JSON to be present in its working directory at runtime —
  exact filenames/contents weren't captured here (they're real credentials).
  Recreate these via the Google Cloud Console / Firebase Console for a fresh
  deploy and place them per the backend's `auth.py`/`database.py` expectations.
- Whether the frontend's Vite build actually picks up `VITE_GLITCHTIP_DSN` at
  build time (the Dockerfile's build stage doesn't pass it as a `--build-arg`
  in the current compose) wasn't verified — check this if frontend error
  reporting isn't showing up in GlitchTip.
