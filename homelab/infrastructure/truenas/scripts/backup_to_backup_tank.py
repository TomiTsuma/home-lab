#!/usr/bin/env python3
"""
backup_to_backup_tank.py - daily backup of irreplaceable data from storage-tank
(2-disk stripe, no redundancy) to backup-tank (separate physical disk).

Part A - app data (ZFS replication)
    storage-tank/ix-apps is hidden from TrueNAS' own snapshot/replication tasks,
    so we drive ZFS directly: an atomic recursive snapshot (crash-consistent for
    the Postgres/SQLite databases inside), then a per-dataset incremental
    `zfs send -p -I | zfs recv` into backup-tank/apps/ix-apps.
    Excluded: docker images, app catalog, the orphaned jellyfin/storage_entry.
    The replica is received read-only, unmounted, with its mountpoint moved under
    /mnt/backup-tank so it can never shadow the live /mnt/.ix-apps.

Part B - selected data-store folders (rsync)
    data-store is one 1.1 TB dataset (mostly re-downloadable media), so only the
    valuable folders are copied, read from a short-lived snapshot (consistent
    copy of jellyfin.db etc.), into backup-tank/data-store-files, which is then
    snapshotted for version history.

Part C - TrueNAS configuration database (+ its secret seed), root-only.

Part D - docker-compose files of every app, as plain browsable files (the Part A
    replica holds them too, but unmounted). Per TrueNAS app: the rendered
    docker-compose.yaml that actually runs, user_config.yaml (the app's settings
    in the UI) and metadata.yaml; plus any non-TrueNAS compose project found via
    docker labels. Written to data-store-files/_compose, root-only because the
    rendered files contain app secrets; rebuilt each run, history via snapshots.

Pre-flight: if backup-tank is not ONLINE (missing/suspended/degraded disk) the run
    is skipped with a clear log line instead of failing halfway or hanging on I/O.

Monitoring: each run reports to an Uptime Kuma push monitor (status up/down + message)
    if its push URL is in scripts/.backup_kuma_push_url; give the monitor a heartbeat
    interval of ~25 h so a run that never happens also alerts.

Retention: source snapshots 14 days, backup snapshots 30 days.
Usage: backup_to_backup_tank.py [--dry-run]
"""
import argparse
import datetime
import fcntl
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

SRC_APPS = "storage-tank/ix-apps"
EXCLUDE = ["storage-tank/ix-apps/docker",
           "storage-tank/ix-apps/truenas_catalog",
           "storage-tank/ix-apps/app_mounts/jellyfin/storage_entry"]
DST_APPS = "backup-tank/apps/ix-apps"
DST_APPS_MOUNT = "/backup-tank/apps/ix-apps"  # TrueNAS pools use altroot=/mnt, so this lands in /mnt/backup-tank/...

SRC_DS = "storage-tank/data-store"
SRC_DS_MOUNT = "/mnt/storage-tank/data-store"
DS_PATHS = ["audiobooks", "config", "data", "metadata", "plugins", "root", "log",
            "remux", "home-lab", "plex", "scripts", "Downloads/qBittorrent",
            "tst.yml", ".jellyfin-data"]
DST_FILES = "backup-tank/data-store-files"
DST_FILES_MOUNT = "/mnt/backup-tank/data-store-files"

APP_CONFIGS = "/mnt/.ix-apps/app_configs"
COMPOSE_DIR = f"{DST_FILES_MOUNT}/_compose"

PREFIX = "bk-"
SRC_KEEP_DAYS = 14
DST_KEEP_DAYS = 30
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "backup_to_backup_tank.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".backup_to_backup_tank.lock")
PUSH_URL_FILE = os.path.join(SCRIPT_DIR, ".backup_kuma_push_url")
DST_POOL = "backup-tank"
DRY = False


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sh(cmd, check=True):
    """Run a shell pipeline; in dry-run mode only print state-changing commands."""
    if DRY and not cmd.startswith(("zfs list", "zfs get")):
        log(f"DRY {cmd}")
        return ""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr.strip()[-600:]}")
    return r.stdout


def snapshots(dataset):
    out = sh(f"zfs list -H -t snapshot -d 1 -o name -s creation {dataset}", check=False)
    return [l.split("@", 1)[1] for l in out.splitlines() if "@" + PREFIX in l]


def exists(dataset):
    return subprocess.run(["zfs", "list", "-H", dataset], capture_output=True).returncode == 0


def prune(dataset, keep_days, protect):
    """Destroy bk- snapshots older than keep_days (recursively), never `protect`."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    for snap in snapshots(dataset):
        try:
            when = datetime.datetime.strptime(snap[len(PREFIX):], "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        if when < cutoff and snap != protect:
            sh(f"zfs destroy -r {dataset}@{snap}")
            log(f"pruned {dataset}@{snap}")


def backup_apps(tag):
    sh(f"zfs snapshot -r {SRC_APPS}@{tag}")
    for ex in EXCLUDE:  # don't let snapshots pin docker-image churn on the source
        sh(f"zfs destroy -r {ex}@{tag}", check=False)
    # Send dataset by dataset (parents sort before children). `zfs send -R -X` would
    # still demand the snapshot on excluded datasets, which we just destroyed.
    names = sh(f"zfs list -H -r -o name {SRC_APPS}").split()
    datasets = [d for d in sorted(names) if not any(d == ex or d.startswith(ex + "/") for ex in EXCLUDE)]
    full = incr = 0
    for ds in datasets:
        rel = ds[len(SRC_APPS):]
        dst = DST_APPS + rel
        # -p keeps app dataset properties; the replica is read-only and its top level
        # is re-pointed under /mnt/backup-tank so it can never mount over /mnt/.ix-apps
        opts = "-u -o readonly=on " + (f"-o mountpoint={DST_APPS_MOUNT} -o canmount=noauto" if not rel else "-x mountpoint")
        if not exists(dst):
            sh(f"zfs send -p {ds}@{tag} | zfs recv {opts} {dst}")
            full += 1
            continue
        common = [s for s in snapshots(dst) if s in set(snapshots(ds)) and s != tag]
        if not common:
            raise RuntimeError(f"no common snapshot between {ds} and {dst}; re-seed needed "
                               f"(rename/destroy {dst} and rerun)")
        sh(f"zfs send -p -I @{common[-1]} {ds}@{tag} | zfs recv {opts} {dst}")
        incr += 1
    log(f"apps: {len(datasets)} datasets replicated ({full} full, {incr} incremental)")
    prune(SRC_APPS, SRC_KEEP_DAYS, protect=tag)
    prune(DST_APPS, DST_KEEP_DAYS, protect=tag)


def backup_files(tag):
    snap = f"{PREFIX}tmp-{tag[len(PREFIX):]}"  # e.g. bk-tmp-2026-10-17_01-05
    sh(f"zfs snapshot {SRC_DS}@{snap}")
    try:
        root = f"{SRC_DS_MOUNT}/.zfs/snapshot/{snap}"
        for rel in DS_PATHS:
            src = f"{root}/{rel}"
            if not DRY and not os.path.exists(src):
                log(f"files: skip missing {rel}")
                continue
            dst_parent = os.path.dirname(f"{DST_FILES_MOUNT}/{rel}")
            if not DRY:
                os.makedirs(dst_parent, exist_ok=True)
            slash = "/" if DRY or os.path.isdir(src) else ""
            # -a keeps owner/mode/times; NFSv4 ACLs are not carried (restore re-applies dataset ACL)
            sh(f"rsync -a --delete '{src}{slash}' '{DST_FILES_MOUNT}/{rel}{slash}'")
        log(f"files: rsync of {len(DS_PATHS)} paths done")
    finally:
        sh(f"zfs destroy {SRC_DS}@{snap}", check=False)  # never leave media blocks pinned

    # Part C: TrueNAS config DB (consistent copy) + secret seed, root-only
    cfg = f"{DST_FILES_MOUNT}/_truenas-config"
    if not DRY:
        os.makedirs(cfg, mode=0o700, exist_ok=True)
        os.chmod(cfg, 0o700)
    sh(f"sqlite3 /data/freenas-v1.db \".backup '{cfg}/freenas-v1.db'\"")
    if not DRY:
        shutil.copy2("/data/pwenc_secret", f"{cfg}/pwenc_secret")
        os.chmod(f"{cfg}/pwenc_secret", 0o600)

    sh(f"zfs snapshot {DST_FILES}@{tag}")
    prune(DST_FILES, DST_KEEP_DAYS, protect=tag)


def backup_compose(tag):
    import yaml  # ships with TrueNAS' python3

    copies = []  # (src, path relative to COMPOSE_DIR)
    for app in sorted(os.listdir(APP_CONFIGS)):
        app_dir = f"{APP_CONFIGS}/{app}"
        with open(f"{app_dir}/metadata.yaml") as f:
            version = yaml.safe_load(f)["version"]
        ver_dir = f"{app_dir}/versions/{version}"
        copies += [(f"{app_dir}/metadata.yaml", f"{app}/metadata.yaml"),
                   (f"{ver_dir}/user_config.yaml", f"{app}/user_config.yaml"),
                   (f"{ver_dir}/templates/rendered/docker-compose.yaml", f"{app}/docker-compose.yaml")]

    # compose projects started by hand (not TrueNAS apps, whose projects are named ix-*)
    labels = sh("docker ps -a --format '{{.Label \"com.docker.compose.project\"}}\t"
                "{{.Label \"com.docker.compose.project.config_files\"}}'", check=False)
    for line in sorted(set(labels.splitlines())):
        project, _, files = line.partition("\t")
        if not project or project.startswith("ix-"):
            continue
        for path in filter(None, files.split(",")):
            copies.append((path, f"_custom/{project}/{os.path.basename(path)}"))

    if DRY:
        for src, rel in copies:
            log(f"DRY copy {src} -> {COMPOSE_DIR}/{rel}")
        return
    shutil.rmtree(COMPOSE_DIR, ignore_errors=True)  # removed apps drop out; old copies live in snapshots
    os.makedirs(COMPOSE_DIR, mode=0o700)
    missing = []
    for src, rel in copies:
        if not os.path.exists(src):
            missing.append(src)
            continue
        os.makedirs(os.path.dirname(f"{COMPOSE_DIR}/{rel}"), exist_ok=True)
        shutil.copy2(src, f"{COMPOSE_DIR}/{rel}")
    os.chmod(COMPOSE_DIR, 0o700)
    log(f"compose: {len(copies) - len(missing)} files copied" +
        (f", missing: {', '.join(missing)}" if missing else ""))


def pool_health(pool):
    try:  # bounded: commands on a sick pool can block
        r = subprocess.run(["zpool", "list", "-H", "-o", "health", pool],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    return r.stdout.strip() or "MISSING"


def push(ok, msg):
    """Report to the Uptime Kuma push monitor; never let monitoring break the backup."""
    if DRY or not os.path.exists(PUSH_URL_FILE):
        return
    with open(PUSH_URL_FILE) as f:
        base = f.read().strip().split("?")[0]
    query = urllib.parse.urlencode({"status": "up" if ok else "down", "msg": msg[:200], "ping": ""})
    try:
        urllib.request.urlopen(f"{base}?{query}", timeout=15).read()
    except Exception as e:
        log(f"push to uptime kuma failed: {e}")


def main():
    global DRY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    DRY = ap.parse_args().dry_run

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another backup is running", file=sys.stderr)
        return 1

    tag = PREFIX + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    log(f"start {tag} dry_run={DRY}")
    health = pool_health(DST_POOL)
    if health != "ONLINE":
        log(f"SKIP {tag}: {DST_POOL} is {health}, not ONLINE - nothing written")
        push(False, f"backup skipped: {DST_POOL} is {health}")
        return 2
    failed = False
    # compose runs before files so the data-store-files snapshot taken there includes it
    for name, fn in (("apps", backup_apps), ("compose", backup_compose), ("files", backup_files)):
        try:
            fn(tag)
        except Exception as e:  # one part failing must not skip the other
            failed = True
            log(f"FAIL {name}: {e}")
    used = sh("zfs list -H -o used,avail backup-tank", check=False).split()
    log(f"end {tag} {'FAILED' if failed else 'OK'} backup-tank used/avail={used}")
    push(not failed, f"{tag} {'FAILED - see backup_to_backup_tank.log' if failed else 'OK'} used/avail={used}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
