#!/usr/bin/env python3
"""
gitea_repos_to_s3.py - backs up Gitea to an S3 bucket on the MinIO instance
on aeacus-data-handler, as a second, off-TrueNAS copy alongside the local
storage-tank -> backup-tank replication already done by
backup_to_backup_tank.py (Part A covers all of storage-tank/ix-apps,
including gitea's config/data/postgres_data datasets, but that copy lives on
a disk physically attached to the same Proxmox host/VM chain - this gives an
independent copy on a different machine).

Uses the LAN IP of aeacus-data-handler (not the public s3.ereuna.org domain)
so this runs entirely over the local network, not through the internet/
Cloudflare tunnel. Update S3_ENDPOINT if that host's LAN IP changes.

Two separate backups, both uploaded every run:

Part A - raw bare repos (fast, incremental)
    Mirrors /mnt/.ix-apps/app_mounts/gitea/data/git (Gitea's own bare-repo
    layout) to s3://gitea-backup/git/ - code only (commits/branches/tags),
    no issues/PRs/wiki/users/settings. Cheap to restore from anywhere with
    just `git clone` on the resulting .git folders, no Gitea install needed.

Part B - full `gitea dump` archive (complete, slower)
    Runs Gitea's own built-in dump command inside the live container
    (`docker exec ... gitea dump`), which produces a single self-consistent
    zip: full Postgres DB (issues, PRs, wiki, users, settings, webhooks...),
    every repo, and app.ini - everything needed to fully restore a working
    Gitea instance, not just the code. Uploaded to
    s3://gitea-backup/full-dumps/gitea-dump-<timestamp>.zip. Requires
    docker access, hence root - this script must run as root (it does, via
    TrueNAS cron). Keeps the last DUMP_RETENTION dumps in S3, prunes older.

Source: root-only, this script must run as root.

Credentials: read from CREDENTIALS_FILE (root-only, not committed anywhere).
Currently uses MinIO's root account because no scoped access key could be
created (MinIO discontinued free `mc` client downloads needed to mint one
via its admin API) - rotate to a bucket-scoped key when that's set up, and
update this file to match.

Part A's upload is additive only (never deletes from S3) - skips files whose
size on S3 already matches the local file, uploads everything else. Safe to
re-run; safe if interrupted.

Usage: gitea_repos_to_s3.py [--dry-run]
"""
import argparse
import datetime
import fcntl
import json
import os
import subprocess
import sys
import tempfile

import boto3
from botocore.exceptions import ClientError

GIT_DATA_DIR = "/mnt/.ix-apps/app_mounts/gitea/data/git"
S3_ENDPOINT = "http://192.168.100.9:9000"  # aeacus-data-handler LAN IP, not s3.ereuna.org - keep this on the LAN
S3_BUCKET = "gitea-backup"
S3_PREFIX = "git"
DUMP_PREFIX = "full-dumps"
DUMP_RETENTION = 7  # keep this many most-recent full dumps in S3
SCRIPT_DIR_FOR_CREDS = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR_FOR_CREDS, ".gitea_s3_credentials.json")  # {"access_key": "...", "secret_key": "..."}
# NOTE: not /root/ - this dataset (storage-tank/data-store) has NFSv4
# aclmode=restricted, so chmod doesn't work and every file here inherits a
# wide-open ACL by default. This file's ACL was explicitly tightened to
# owner-only (0700, owner truenas_admin) via `midclt filesystem.setacl`
# after creation - root (this script, run via cron) can still read it since
# root bypasses permission checks, but no other user can.

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "gitea_repos_to_s3.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".gitea_repos_to_s3.lock")
DRY = False


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def s3_client():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
    )


def remote_size(s3, key):
    try:
        return s3.head_object(Bucket=S3_BUCKET, Key=key)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def sync(s3):
    uploaded = skipped = failed = 0
    for root, _, names in os.walk(GIT_DATA_DIR):
        for name in names:
            local_path = os.path.join(root, name)
            rel = os.path.relpath(local_path, GIT_DATA_DIR)
            key = f"{S3_PREFIX}/{rel}"
            try:
                local_size = os.path.getsize(local_path)
            except OSError as e:
                log(f"FAIL stat {local_path}: {e}")
                failed += 1
                continue
            if remote_size(s3, key) == local_size:
                skipped += 1
                continue
            if DRY:
                log(f"DRY upload {local_path} -> s3://{S3_BUCKET}/{key}")
                uploaded += 1
                continue
            try:
                s3.upload_file(local_path, S3_BUCKET, key)
                uploaded += 1
            except Exception as e:
                log(f"FAIL upload {local_path}: {e}")
                failed += 1
    log(f"done uploaded={uploaded} skipped={skipped} failed={failed}")
    return failed


def find_gitea_container():
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        name, _, image = line.partition("\t")
        if image.startswith("gitea/gitea"):
            return name
    return None


def dump_gitea(s3):
    container = find_gitea_container()
    if not container:
        log("FAIL dump: no running gitea/gitea container found")
        return 1

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    remote_zip = f"/tmp/gitea-dump-{ts}.zip"
    key = f"{DUMP_PREFIX}/gitea-dump-{ts}.zip"

    if DRY:
        log(f"DRY dump {container} -> s3://{S3_BUCKET}/{key}")
        return 0

    try:
        # gitea dump (rootless image) picks up GITEA_APP_INI/GITEA_WORK_DIR
        # from the container's own environment - no need to pass -c/-w.
        subprocess.run(
            ["docker", "exec", container, "gitea", "dump", "--type", "zip", "-f", remote_zip],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        log(f"FAIL gitea dump in {container}: {e.stderr[-800:]}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        local_zip = os.path.join(tmp, os.path.basename(remote_zip))
        try:
            subprocess.run(["docker", "cp", f"{container}:{remote_zip}", local_zip], check=True, capture_output=True, text=True)
            s3.upload_file(local_zip, S3_BUCKET, key)
            size = os.path.getsize(local_zip)
        except subprocess.CalledProcessError as e:
            log(f"FAIL docker cp dump from {container}: {e.stderr[-800:]}")
            return 1
        except Exception as e:
            log(f"FAIL upload dump: {e}")
            return 1
        finally:
            subprocess.run(["docker", "exec", container, "rm", "-f", remote_zip], check=False)

    log(f"dump: uploaded s3://{S3_BUCKET}/{key} ({size} bytes)")
    return 0


def prune_old_dumps(s3):
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{DUMP_PREFIX}/")
    objs = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    for o in objs[DUMP_RETENTION:]:
        if DRY:
            log(f"DRY prune s3://{S3_BUCKET}/{o['Key']}")
            continue
        s3.delete_object(Bucket=S3_BUCKET, Key=o["Key"])
        log(f"pruned s3://{S3_BUCKET}/{o['Key']}")


def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    DRY = ap.parse_args().dry_run

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another run is in progress", file=sys.stderr)
        return 1

    if not os.path.isdir(GIT_DATA_DIR):
        log(f"SKIP: {GIT_DATA_DIR} not found or not readable (must run as root)")
        return 1

    log(f"start dry_run={DRY}")
    try:
        s3 = s3_client()
        failed = sync(s3)
        failed += dump_gitea(s3)
        prune_old_dumps(s3)
    except Exception:
        import traceback
        log("FAIL uncaught exception:\n" + traceback.format_exc())
        return 1
    log(f"end {'FAILED' if failed else 'OK'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
