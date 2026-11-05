# Lyre

Self-built music player web app, deployed on `aeacus-data-handler` (VM 100).
React/Vite frontend (served via nginx in production) + FastAPI backend, with
Spotify-style search (RapidAPI `spotify81`), YouTube Music and Lidarr
integration, and recommendations. Bundles its own GlitchTip instance
(self-hosted Sentry-compatible error tracking) at `https://logs.lyre.qd.je`.

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

First boot: GlitchTip needs its own setup (create an org/project in its UI at
`:8085` or the public domain, then copy the generated DSNs back into `.env`
as `GLITCHTIP_INTERNAL_DSN`/`VITE_GLITCHTIP_DSN`/`GLITCHTIP_DSN` and restart).

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
