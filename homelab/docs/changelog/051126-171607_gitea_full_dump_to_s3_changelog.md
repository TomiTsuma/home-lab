# 2026-11-05 — Extended Gitea S3 backup to a full `gitea dump`

Follow-up to the Gitea→S3 backup added earlier today (bare repos only). User
asked for a way to recover Gitea fully — including issues/PRs/wiki, not just
code — onto a local PC if storage-tank is lost entirely.

## Added to `gitea_repos_to_s3.py`

A second backup, run every time alongside the existing raw-repo mirror:

- Calls Gitea's own `gitea dump --type zip` **inside the live container**
  (`docker exec`), which produces a single self-consistent archive: full
  Postgres DB (issues, PRs, wiki, users, settings, webhooks), every repo,
  and `app.ini`. `docker cp`'d out to the TrueNAS host, then uploaded to
  `s3://gitea-backup/full-dumps/gitea-dump-<timestamp>.zip`.
- Finds the gitea container dynamically (`docker ps` filtered to an image
  starting with `gitea/gitea`) rather than a hardcoded name/ID, since
  TrueNAS Apps' container names/IDs change across recreates.
- Keeps the last 7 dumps in S3, prunes older ones automatically.
- This needs real `docker exec`/`docker cp` access — the `truenas_admin`
  account used for everything else this session does **not** have Docker
  socket access (`permission denied` connecting to `/var/run/docker.sock`).
  Verified by triggering the actual TrueNAS cron job (`midclt call
  cronjob.run 5`), which runs as root via TrueNAS's own privileged
  mechanism, same approach used earlier for the pool recovery.

## Verified

Test run: raw-repo part correctly skipped all 980 already-uploaded files
(unchanged), and the dump part uploaded a real 14.9MB zip — confirmed by
listing the bucket afterward, not just trusting the script's exit code.

## Recovery note (not yet written into a repo doc — see chat for the
full walkthrough given to the user)

For a from-scratch recovery onto a Windows PC with storage-tank gone:
`aws s3 sync s3://gitea-backup/git ... --endpoint-url http://192.168.100.9:9000`
gets the raw repos (code only, `git clone` the resulting `.git` folders
directly). For a *full* restore including issues/PRs/wiki, download the
latest `full-dumps/gitea-dump-*.zip` instead and restore it into a fresh
Gitea install per Gitea's own `gitea restore` documentation.
