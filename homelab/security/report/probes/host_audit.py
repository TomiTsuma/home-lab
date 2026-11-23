"""Security audit of one Linux host. Piped over SSH by security_report.py (`ssh host python3 -`), or run
locally on pve. Works as a normal user in the `adm` group (journal and auth logs readable). Root-only files
are skipped when not readable. Read-only: changes nothing. Prints one JSON object.

Collected:
  auth       - 24 h of sshd / sudo / user-management journal events: failed and accepted logins by source,
               sudo failures, account changes
  integrity  - sha256 of persistence- and access-relevant files (keys, sudoers, cron, units, sshd config).
               pve diffs these against yesterday's baseline
  listening  - listening TCP/UDP sockets (diffed against baseline on pve)
  updates    - pending (security) package updates, reboot-required
  docker     - risky container settings (privileged, host network, docker.sock or / mounted, dangerous caps)
               and sensitive ports published on all interfaces
"""
import glob
import hashlib
import json
import os
import re
import socket
import subprocess

WINDOW = "-24h"
SENSITIVE_PORTS = {2375: "Docker API", 2376: "Docker API", 5432: "Postgres", 3306: "MySQL", 6379: "Redis",
                   27017: "MongoDB", 9200: "OpenSearch/Elasticsearch", 8123: "ClickHouse HTTP", 9000: "ClickHouse/MinIO",
                   9002: "ClickHouse native", 9092: "Kafka", 2181: "ZooKeeper", 7687: "Neo4j bolt", 7474: "Neo4j HTTP",
                   8200: "Vault", 11434: "Ollama API", 9001: "MinIO console", 11211: "Memcached"}
RISKY_CAPS = {"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE", "DAC_READ_SEARCH", "ALL"}


def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def bump(d, k, n=1):
    d[k] = d.get(k, 0) + n


# ----------------------------------------------------------------------------- auth events
RE_FAILED = re.compile(r"Failed (?:password|publickey|keyboard-interactive/pam) for (?:invalid user )?(\S+) from (\S+)")
RE_INVALID = re.compile(r"Invalid user (\S*) from (\S+)")
RE_ACCEPTED = re.compile(r"Accepted (\S+) for (\S+) from (\S+)")
RE_SUDO_CMD = re.compile(r"sudo\[\d+\]:\s+(\S+) : .*COMMAND=(.*)")
RE_SUDO_FAIL = re.compile(r"sudo\[\d+\]:.*(authentication failure|incorrect password attempt|NOT in sudoers)")
RE_ACCOUNT = re.compile(r"(useradd|userdel|usermod|groupadd|groupdel|gpasswd|chpasswd|passwd)\[\d+\]: (.*)")


def auth_events():
    ids = ["sshd", "sshd-session", "sudo", "useradd", "userdel", "usermod", "groupadd", "groupdel", "gpasswd",
           "chpasswd", "passwd", "su"]
    rc, out = sh(["journalctl", "--since", WINDOW, "--no-pager", "-o", "short-iso", *sum([["-t", i] for i in ids], [])], 120)
    res = {"readable": rc == 0, "failed_by_ip": {}, "failed_users": {}, "accepted": {}, "sudo_cmds": 0,
           "sudo_fail": 0, "sudo_by_user": {}, "account_changes": []}
    for line in out.splitlines():
        if m := RE_ACCEPTED.search(line):
            bump(res["accepted"], f"{m.group(2)}|{m.group(3)}|{m.group(1)}")
        elif m := RE_FAILED.search(line):
            bump(res["failed_by_ip"], m.group(2))
            bump(res["failed_users"], m.group(1))
        elif m := RE_INVALID.search(line):
            bump(res["failed_by_ip"], m.group(2))
            bump(res["failed_users"], m.group(1) or "(empty)")
        elif RE_SUDO_FAIL.search(line):
            res["sudo_fail"] += 1
        elif m := RE_SUDO_CMD.search(line):
            res["sudo_cmds"] += 1
            bump(res["sudo_by_user"], m.group(1))
        elif m := RE_ACCOUNT.search(line):
            res["account_changes"].append(line[:200])
    res["account_changes"] = res["account_changes"][-20:]
    return res


# ----------------------------------------------------------------------------- integrity
def file_hashes():
    home = os.path.expanduser("~")
    patterns = ["/etc/passwd", "/etc/group", "/etc/shadow", "/etc/sudoers", "/etc/sudoers.d/*",
                "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*", "/etc/crontab", "/etc/cron.d/*",
                "/etc/ld.so.preload", "/root/.ssh/authorized_keys", f"{home}/.ssh/authorized_keys",
                "/home/*/.ssh/authorized_keys", "/etc/rc.local", "/etc/profile.d/*"]
    out = {}
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            try:
                out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
            except OSError:
                pass   # not readable as this user: skipped, not reported as a change
    # Persistence: new systemd units / timers and the user's crontab.
    units = sorted(os.path.basename(p) for p in glob.glob("/etc/systemd/system/*.service") +
                   glob.glob("/etc/systemd/system/*.timer"))
    out["(systemd units in /etc/systemd/system)"] = hashlib.sha256("\n".join(units).encode()).hexdigest()[:16]
    rc, cron = sh(["crontab", "-l"])
    out["(crontab of " + os.environ.get("USER", "?") + ")"] = hashlib.sha256(cron.encode()).hexdigest()[:16] if rc == 0 else "none"
    return out, units


def accounts():
    uid0, logins = [], []
    for line in open("/etc/passwd"):
        f = line.strip().split(":")
        if len(f) < 7:
            continue
        if f[2] == "0":
            uid0.append(f[0])
        if not f[6].endswith(("nologin", "false", "sync")) and (int(f[2]) >= 1000 or f[2] == "0"):
            logins.append(f[0])
    return uid0, logins


# ----------------------------------------------------------------------------- network / updates / ssh
def listening():
    rc, out = sh(["ss", "-Hlntu"])
    socks = set()
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5:
            addr = f[4]
            if addr.startswith(("127.", "[::1]", "::1")) or "%lo" in addr:
                continue
            socks.add(f"{f[0]} {addr}")
    return sorted(socks)


def updates():
    rc, out = sh("apt list --upgradable 2>/dev/null", 120)
    lines = [l for l in out.splitlines() if "upgradable" in l]
    sec = [l.split("/")[0] for l in lines if "-security" in l or "security" in l.split()[0]]
    return {"total": len(lines), "security": len(sec), "security_pkgs": sec[:40],
            "reboot_required": os.path.exists("/var/run/reboot-required")}


def sshd_settings():
    rc, out = sh(["sshd", "-T"])          # needs root; otherwise parse the readable config files
    text = out if rc == 0 else ""
    if not text:
        for p in ["/etc/ssh/sshd_config"] + sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf")):
            try:
                text += open(p).read() + "\n"
            except OSError:
                pass
    s = {}
    for line in text.splitlines():
        f = line.strip().split(None, 1)
        if len(f) == 2 and not f[0].startswith("#"):
            k = f[0].lower()
            if k in ("passwordauthentication", "permitrootlogin", "kbdinteractiveauthentication",
                     "challengeresponseauthentication", "permitemptypasswords", "pubkeyauthentication") and k not in s:
                s[k] = f[1].strip().lower()   # sshd uses the FIRST occurrence
    s["source"] = "sshd -T" if rc == 0 else "config files"
    return s


def docker_audit():
    rc, ids = sh(["docker", "ps", "-q"])
    if rc != 0:
        return {"available": False}
    ids = ids.split()
    rc, out = sh(["docker", "inspect", *ids], 120) if ids else (0, "[]")
    risky, exposed, images = [], [], set()
    for c in json.loads(out or "[]"):
        name = c["Name"].lstrip("/")
        hc = c.get("HostConfig", {})
        images.add(c["Config"]["Image"])
        if hc.get("Privileged"):
            risky.append({"container": name, "issue": "privileged (full root on the host)"})
        if hc.get("NetworkMode") == "host":
            risky.append({"container": name, "issue": "host network namespace"})
        caps = set(hc.get("CapAdd") or []) & RISKY_CAPS
        if caps:
            risky.append({"container": name, "issue": f"capabilities {sorted(caps)}"})
        for m in c.get("Mounts", []):
            src = m.get("Source", "")
            if src in ("/var/run/docker.sock", "/run/docker.sock") and m.get("RW", True):
                risky.append({"container": name, "issue": "docker.sock mounted read-write (root on host)"})
            elif src in ("/var/run/docker.sock", "/run/docker.sock"):
                risky.append({"container": name, "issue": "docker.sock mounted (read-only still allows full control)"})
            elif src == "/" :
                risky.append({"container": name, "issue": "host / mounted"})
        for cport, binds in (hc.get("PortBindings") or {}).items():
            for b in binds or []:
                ip, hp = b.get("HostIp", ""), int(b.get("HostPort") or 0)
                cp = int(cport.split("/")[0])
                if ip in ("", "0.0.0.0", "::") and (hp in SENSITIVE_PORTS or cp in SENSITIVE_PORTS):
                    exposed.append({"container": name, "port": hp,
                                    "service": SENSITIVE_PORTS.get(cp) or SENSITIVE_PORTS.get(hp)})
    return {"available": True, "risky": risky, "exposed": exposed, "images": sorted(images), "containers": len(ids)}


def main():
    hashes, units = file_hashes()
    uid0, logins = accounts()
    print(json.dumps({
        "host": socket.gethostname(), "is_root": os.geteuid() == 0,
        "auth": auth_events(), "integrity": hashes, "units": units, "uid0": uid0, "login_users": logins,
        "listening": listening(), "updates": updates(), "sshd": sshd_settings(), "docker": docker_audit(),
        "unattended_upgrades": sh(["systemctl", "is-active", "unattended-upgrades"])[1].strip() or "absent",
    }))


main()
