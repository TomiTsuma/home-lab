# 2026-11-05 — Live Cloudflare/GlitchTip/S3 work for Lyre + Ereuna platform

Action work (not just documentation), performed directly against the live
hosts, then synced back into the repo where it affects recreation.

## Cloudflare Tunnel: added `logs.ereuna.org`

- Discovered the "Ereuna" tunnel (ID `b76fe173-5ecd-4cc2-b76f-2c48e55b11af`,
  on `aeacus-data-handler`) is **dashboard/API-managed**, not driven by the
  local `~/.cloudflared/config.yml` that exists on that host (both
  connectors — the systemd service and the `ereuna-cloudflared` docker
  container — ignore it; confirmed empirically, the live routes differ from
  that file in two ways). Rewrote that file as an accurate, clearly-labeled
  reference copy instead of a misleading dead config.
- Added `logs.ereuna.org → http://localhost:8085` (GlitchTip) to the real
  tunnel config via the Cloudflare API (user supplied a scoped API token,
  used once and not stored). Existing routes (`lyre`, `app`, `storage`,
  `s3`) preserved exactly.
- Created the DNS CNAME via `cloudflared tunnel route dns`.
- Verified external HTTPS access end-to-end.

## GlitchTip: domain migration + a real bug fix

- Updated the live `GLITCHTIP_DOMAIN` and the two DSN env vars from the
  stale `logs.lyre.qd.je` to `logs.ereuna.org`.
- **Found and fixed**: GlitchTip was silently not working at all — events
  posted successfully (HTTP 200, got an event id back) but never became
  visible issues (`firstEvent` stayed `null`). Root cause: the
  `glitchtip/glitchtip:latest` image's default `SERVER_ROLE=web` only runs
  the web/ingest process, not the worker that drains the queue into actual
  issues. Fixed with `GLITCHTIP_EMBED_WORKER: "true"` (runs web+worker+beat
  in the one container). This means **no GlitchTip event from this homelab
  — for any app, not just Lyre — was ever actually viewable** until today.
  Synced the fix into `homelab/apps/lyre/docker-compose.yml`.
- Noted but not chased further: a stale queued event from ~2026-10-20 fails
  repeatedly with a Postgres partition error (`no partition of relation
  "issue_events_issueevent" found for row`) — moves to GlitchTip's internal
  dead-letter queue each time, doesn't block new events. Cosmetic log noise
  only, confirmed via decoding the failing row's UUIDv7 timestamp.

## Lyre backend: added GlitchTip logging for all app output

- Backend (`main.py`, in Lyre's own repo, not vendored here) is entirely
  `print()`-based (75 call sites across 9 files, zero use of Python's
  `logging` module). Rather than rewrite every call site, wrapped
  `sys.stdout`/`sys.stderr` to duplicate into a dedicated `lyre` logger
  (`propagate=False`) wired directly to `sentry_sdk.capture_message()` —
  this is independent of `sentry_sdk`'s default `LoggingIntegration`
  (left at its default `event_level=ERROR`), so uvicorn's own access/error
  logs are **not** swept in, only Lyre's own output.
  Rebuilt and restarted the backend container.
- Created a new GlitchTip project for this: org `aeacus`, team `lyre`,
  project `lyre-backend`. The backend's `GLITCHTIP_DSN` now points at this
  project (internal DSN, `http://...@glitchtip:8000/3`) instead of the old
  project `1` under a since-replaced org.
- Verified: 13+ real issues appeared from actual app activity (track
  resolution, artwork sync, a RapidAPI search exception, the server's own
  startup messages) within seconds of the worker fix going in.

## Verified, no changes needed

- `s3.ereuna.org` → MinIO's S3 API (`localhost:9000` on `aeacus-data-handler`)
  was **already working** via the existing tunnel route — confirmed with a
  full round-trip (`aws s3 mb/cp/cp/rm/rb`) through the tunnel, not just a
  port-open check. Nothing to fix here; the user's "I also want s3 to be
  accessible programmatically" was already true.

## Security finding — not fixed, flagged for the owner

- MinIO's root credentials (`aeacus` / a password also reused on Lyre's
  Gitea git remote — see the 2026-11-05 recreation-audit changelog) are in
  **plaintext in two world-readable files** on `aeacus-data-handler`:
  `/etc/systemd/system/minio.service` and the stale, unused
  `/etc/default/minio` (the latter's `MINIO_VOLUMES` isn't even what's
  actually used — real data dir is `/mnt/data`, set directly in the
  systemd unit's `ExecStart`, not read from this file — pure leftover
  drift). Recommend: rotate the password, move it to a root-only
  `EnvironmentFile` (0600), and stop reusing it across services.

## Synced into this repo

- `homelab/apps/lyre/docker-compose.yml`: `GLITCHTIP_DOMAIN` default →
  `logs.ereuna.org`, added `GLITCHTIP_EMBED_WORKER: "true"` with a comment
  explaining why it's required.
- `homelab/apps/lyre/.env.example`: domain/DSN host examples updated,
  added guidance on the Team → Project → DSN flow and the internal-vs-
  external DSN host swap.
- `homelab/apps/lyre/README.md`: documented the stdout→GlitchTip logging
  architecture, the worker gotcha, and the `lyre-backend` project identity.
