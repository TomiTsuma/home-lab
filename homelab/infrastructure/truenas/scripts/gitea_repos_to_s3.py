#!/usr/bin/env python3
"""
gitea_repos_to_s3.py - mirrors Gitea's bare git repositories to an S3 bucket
on the MinIO instance on aeacus-data-handler, as a second, off-TrueNAS copy
alongside the local storage-tank -> backup-tank replication already done by
backup_to_backup_tank.py (Part A covers all of storage-tank/ix-apps,
including gitea's config/data/postgres_data datasets, but that copy lives on
a disk physically attached to the same Proxmox host/VM chain - this gives an
independent copy on a different machine).

Uses the LAN IP of aeacus-data-handler (not the public s3.ereuna.org domain)
so this runs entirely over the local network, not through the internet/
Cloudflare tunnel. Update S3_ENDPOINT if that host's LAN IP changes.

Source: /mnt/.ix-apps/app_mounts/gitea/data/git (bare repositories, Gitea's
own layout - root-only, this script must run as root).

Credentials: read from CREDENTIALS_FILE (root-only, not committed anywhere).
Currently uses MinIO's root account because no scoped access key could be
created (MinIO discontinued free `mc` client downloads needed to mint one
via its admin API) - rotate to a bucket-scoped key when that's set up, and
update this file to match.

Upload is additive only (never deletes from S3) - skips files whose size on
S3 already matches the local file, uploads everything else. Safe to re-run;
safe if interrupted.

Usage: gitea_repos_to_s3.py [--dry-run]
"""
import argparse
import datetime
import fcntl
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

GIT_DATA_DIR = "/mnt/.ix-apps/app_mounts/gitea/data/git"
S3_ENDPOINT = "http://192.168.100.9:9000"  # aeacus-data-handler LAN IP, not s3.ereuna.org - keep this on the LAN
S3_BUCKET = "gitea-backup"
S3_PREFIX = "git"
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


def sync():
    s3 = s3_client()
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
        failed = sync()
    except Exception:
        import traceback
        log("FAIL uncaught exception:\n" + traceback.format_exc())
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
