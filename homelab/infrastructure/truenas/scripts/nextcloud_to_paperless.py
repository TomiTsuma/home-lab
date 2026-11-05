#!/usr/bin/env python3
"""
nextcloud_to_paperless.py - feed Paperless-ngx from aeacus' Nextcloud files.

Mirror (copy): PDFs and images under MIRROR_FOLDERS are COPIED into Paperless'
    consume dir, keeping their relative path so the folder names become tags
    (PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS). Nextcloud is never modified. A state
    file remembers the sha256 sent for each path, so every version of a file is
    sent once; an edited file is sent again and becomes a new Paperless document
    (byte-identical duplicates are dropped by Paperless itself).

Inbox (move): files dropped into the Nextcloud folder "Paperless Inbox" are MOVED
    into consume/Inbox (tag "Inbox"), then Nextcloud rescans that folder so they
    disappear from Nextcloud cleanly. Unsupported file types are left in place.

Files are written under a temporary ".part" name (an extension Paperless ignores)
and renamed into place, so Paperless never picks up a half-written file. At most
MAX_PER_RUN files are queued per run, to keep the initial import gentle on the CPU.

Usage: nextcloud_to_paperless.py [--dry-run]
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

NC_USER = "aeacus"
NC_FILES = f"/mnt/.ix-apps/app_mounts/nextcloud/data/{NC_USER}/files"
NC_CONTAINER = "ix-nextcloud-nextcloud-1"
NC_UID = 33  # www-data in the Nextcloud container
MIRROR_FOLDERS = ["Geothermal", "INTI University", "Aether", "ActivationSteering",
                  "DenovoDrugDiscoveryDatasets", "Personal"]
INBOX = "Paperless Inbox"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif"}

CONSUME = "/mnt/.ix-apps/app_mounts/paperless-ngx/consume"
PAPERLESS_UID = 1000  # the app's run_as user; the image's native uid (568 made init chown 28k files on ZFS overlay)
MAX_PER_RUN = 100
INBOX_MIN_AGE = 120  # seconds; skip files that may still be uploading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "nextcloud_to_paperless.state.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "nextcloud_to_paperless.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".nextcloud_to_paperless.lock")
DRY = False


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def supported(name):
    return os.path.splitext(name)[1].lower() in EXTENSIONS and not name.startswith(".")


def deliver(src, rel_dst, move=False):
    """Copy (or move) src to CONSUME/rel_dst atomically, owned by the Paperless user."""
    dst = os.path.join(CONSUME, rel_dst)
    if DRY:
        log(f"DRY {'move' if move else 'copy'} {src} -> {dst}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d = os.path.dirname(dst)  # hand every created directory to the Paperless user
    while d.startswith(CONSUME) and d != CONSUME:
        os.chown(d, PAPERLESS_UID, PAPERLESS_UID)
        d = os.path.dirname(d)
    tmp = dst + ".part"
    if move:
        shutil.move(src, tmp)
    else:
        shutil.copy2(src, tmp)
    os.chown(tmp, PAPERLESS_UID, PAPERLESS_UID)
    os.rename(tmp, dst)


def mirror(state, budget):
    sent = 0
    for folder in MIRROR_FOLDERS:
        for root, dirs, files in os.walk(os.path.join(NC_FILES, folder)):
            dirs.sort()
            for name in sorted(files):
                if not supported(name):
                    continue
                src = os.path.join(root, name)
                rel = os.path.relpath(src, NC_FILES)
                st = os.stat(src)
                prev = state.get(rel)
                if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
                    continue  # unchanged since last delivery
                digest = sha256(src)
                if prev and prev[2] == digest:
                    state[rel] = [st.st_size, st.st_mtime_ns, digest]  # touched, same content
                    continue
                if sent >= budget:
                    return sent
                deliver(src, rel)
                state[rel] = [st.st_size, st.st_mtime_ns, digest]
                sent += 1
    return sent


def inbox():
    path = os.path.join(NC_FILES, INBOX)
    if not os.path.isdir(path):
        if not DRY:
            os.makedirs(path)
            os.chown(path, NC_UID, NC_UID)
        log(f"created Nextcloud folder '{INBOX}'")
        nextcloud_rescan(INBOX)  # so Nextcloud shows the new folder
        return 0
    moved = 0
    now = time.time()
    for root, _, files in os.walk(path):
        for name in sorted(files):
            src = os.path.join(root, name)
            if not supported(name) or now - os.stat(src).st_mtime < INBOX_MIN_AGE:
                continue
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            deliver(src, os.path.join("Inbox", f"{stamp}_{name}"), move=True)
            moved += 1
    return moved


def nextcloud_rescan(rel):
    if DRY:
        return
    r = subprocess.run(["docker", "exec", "-u", str(NC_UID), NC_CONTAINER, "php", "occ",
                        "files:scan", f"--path={NC_USER}/files/{rel}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"WARN nextcloud rescan failed: {r.stderr.strip()[-300:]}")


def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    DRY = ap.parse_args().dry_run

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # previous run still going (initial import); try again next time
    if not os.path.isdir(CONSUME):
        log(f"SKIP: consume dir {CONSUME} missing - is the paperless-ngx app installed?")
        return 2

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)

    moved = inbox()
    if moved:
        nextcloud_rescan(INBOX)
    sent = mirror(state, MAX_PER_RUN - moved)

    if not DRY:
        with open(STATE_FILE + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    if moved or sent:
        log(f"inbox moved={moved} mirror sent={sent} tracked={len(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
