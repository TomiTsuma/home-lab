#!/usr/bin/env python3
"""Daily homelab status report, emailed through the TrueNAS mail-relay.

Runs on the Proxmox host (root), standard library only. Collects:
  * Proxmox host: CPU (now + 24h avg/max), memory, storage, VM states, public DNS
  * Linux VMs/hosts over SSH: load, memory, disks, docker containers, failed units, GPU
  * TrueNAS via `qm guest exec` (root): pools, apps, alerts, backup snapshot age,
    mail-relay queue, internet speed, public endpoint checks
and sends one HTML email with the problems listed first.

Usage:
  homelab_report.py            collect + send
  homelab_report.py --dry-run  collect + print the plain-text report, send nothing
See README.md next to this file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import socket
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage

# --------------------------------------------------------------------------- config
MAIL_FROM = os.environ.get("REPORT_FROM", "homelab@ereuna.org")
MAIL_TO = os.environ.get("REPORT_TO", "tomitsuma@ereuna.org")
SMTP_HOST, SMTP_PORT = "192.168.100.10", 587          # TrueNAS mail-relay (LAN)
TRUENAS_VMID = "102"
HISTORY_FILE = "/var/lib/homelab-report/history.jsonl"

# SSH targets (LAN addresses; pve's root key is authorized on each).
SSH_HOSTS = {
    "aeacus-data-handler": "aeacus@192.168.100.9",
    "aeacus-dl": "aeacus@192.168.100.17",
    "minos": "minos@192.168.100.12",
}

# Public URLs checked from TrueNAS. Anything that answers with HTTP < 500 counts as up
# (MinIO answers 403 on "/", which is fine).
ENDPOINTS = [
    "https://lyre.ereuna.org/",
    "https://logs.ereuna.org/",
    "https://tracking.ereuna.org/",
    "https://chlio.ereuna.org/",
    "https://app.ereuna.org/",
    "https://storage.ereuna.org/",
    "https://s3.ereuna.org/minio/health/live",
]

# TrueNAS apps that are stopped on purpose (not reported as problems).
TRUENAS_EXPECTED_STOPPED = {"plex1", "seerr", "lyrion-music-server", "wg-easy", "adguard-home", "readarr"}

DISK_WARN, DISK_CRIT = 85.0, 95.0          # % used
MEM_WARN = 90.0                             # % used
LOAD_PER_CORE_WARN = 1.5
CPU_24H_AVG_WARN = 80.0                     # % (Proxmox host)
GPU_TEMP_WARN = 85                          # °C
BACKUP_MAX_AGE_H = 36                       # newest storage-tank/ix-apps@bk-* snapshot
SPEED_DOWN_BYTES, SPEED_UP_BYTES = 10_000_000, 10_000_000

OK, WARN, CRIT = "ok", "warn", "crit"
_RANK = {OK: 0, WARN: 1, CRIT: 2}


# --------------------------------------------------------------------------- model
@dataclass
class Section:
    title: str
    rows: list[tuple[str, str, str]] = field(default_factory=list)   # (status, label, value)

    def add(self, status: str, label: str, value: str) -> None:
        self.rows.append((status, label, value))

    @property
    def status(self) -> str:
        return max((r[0] for r in self.rows), key=_RANK.get, default=OK)

    @property
    def issues(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[0] != OK]


# --------------------------------------------------------------------------- helpers
def run(cmd: list[str] | str, timeout: int = 60, shell: bool = False) -> str:
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def pct_status(pct: float, warn: float, crit: float | None = None) -> str:
    if crit is not None and pct >= crit:
        return CRIT
    return WARN if pct >= warn else OK


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    d, rem = divmod(int(seconds), 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m"


def parse_sections(text: str) -> dict[str, list[str]]:
    """Split '@name' delimited command output into {name: [lines]}."""
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("@") and " " not in line:
            current = line[1:]
            out[current] = []
        elif current is not None and line.strip():
            out[current].append(line)
    return out


def classify_container(c: dict) -> tuple[str, str] | None:
    """Return (status, description) for a problem container, or None if it is fine.

    Running+healthy is fine. Exited(0) is treated as stopped on purpose.
    Unhealthy, restarting, dead, or exited with a non-zero code are problems.
    """
    state = c.get("State", "")
    text = c.get("Status", "")
    name = c.get("Names", "?")
    if state == "running":
        if "(unhealthy)" in text:
            return WARN, f"{name}: unhealthy ({text})"
        return None
    if state in ("restarting", "dead"):
        return CRIT, f"{name}: {state} ({text})"
    if state == "exited" and not text.startswith("Exited (0)"):
        return WARN, f"{name}: {text}"
    return None


# --------------------------------------------------------------------------- collectors
def collect_pve() -> Section:
    node = socket.gethostname()
    s = Section(f"Proxmox host ({node})")

    with open("/proc/uptime") as f:
        s.add(OK, "Uptime", human_duration(float(f.read().split()[0])))

    load1, load5, load15 = os.getloadavg()
    cores = os.cpu_count() or 1
    s.add(OK if load15 / cores < LOAD_PER_CORE_WARN else WARN,
          "Load (1/5/15 min)", f"{load1:.2f} / {load5:.2f} / {load15:.2f} on {cores} cores")

    rrd = json.loads(run(["pvesh", "get", f"/nodes/{node}/rrddata", "--timeframe", "day",
                          "--output-format", "json"]))
    cpu = [x["cpu"] * 100 for x in rrd if x.get("cpu") is not None]
    if cpu:
        avg = statistics.mean(cpu)
        s.add(OK if avg < CPU_24H_AVG_WARN else WARN, "CPU last 24h",
              f"avg {avg:.0f}%, peak {max(cpu):.0f}%")

    mem = {k: int(v.split()[0]) * 1024 for k, v in
           (line.split(":", 1) for line in open("/proc/meminfo")) if k in ("MemTotal", "MemAvailable")}
    used_pct = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"])
    s.add(pct_status(used_pct, MEM_WARN), "Memory",
          f"{used_pct:.0f}% used ({human_bytes(mem['MemTotal'] - mem['MemAvailable'])} of {human_bytes(mem['MemTotal'])})")

    for st in json.loads(run(["pvesh", "get", f"/nodes/{node}/storage", "--output-format", "json"])):
        if not st.get("active") or not st.get("total"):
            continue
        p = 100 * st["used"] / st["total"]
        s.add(pct_status(p, DISK_WARN, DISK_CRIT), f"Storage {st['storage']}",
              f"{p:.0f}% used ({human_bytes(st['used'])} of {human_bytes(st['total'])})")

    for vm in sorted(json.loads(run(["pvesh", "get", f"/nodes/{node}/qemu", "--output-format", "json"])),
                     key=lambda v: int(v["vmid"])):
        running = vm.get("status") == "running"
        detail = (f"running {human_duration(vm.get('uptime', 0))}, "
                  f"RAM {human_bytes(vm.get('mem', 0))} / {human_bytes(vm.get('maxmem', 0))}") if running else vm.get("status")
        s.add(OK if running else CRIT, f"VM {vm['vmid']} {vm.get('name', '')}", detail)

    try:
        socket.getaddrinfo("cloudflare.com", 443)
        s.add(OK, "Public DNS", "resolves")
    except OSError:
        s.add(WARN, "Public DNS", "cannot resolve public names (resolv.conf is Tailscale-only); apt updates likely failing")
    return s


REMOTE_SCRIPT = r"""
echo @uptime; cat /proc/uptime
echo @load; cat /proc/loadavg; nproc
echo @mem; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo @disk; df -B1 --output=target,size,used -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs -x iso9660 -x udf 2>/dev/null | tail -n +2
echo @docker; docker ps -a --format '{{json .}}' 2>/dev/null
echo @failed; systemctl --failed --no-legend --plain 2>/dev/null
echo @gpu; nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null
true
"""


def collect_ssh_host(name: str, target: str) -> Section:
    s = Section(name)
    try:
        out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, REMOTE_SCRIPT], timeout=90)
    except (subprocess.SubprocessError, OSError) as e:
        s.add(CRIT, "Reachable", f"no ({type(e).__name__})")
        return s
    sec = parse_sections(out)

    s.add(OK, "Uptime", human_duration(float(sec["uptime"][0].split()[0])))
    load = sec["load"][0].split()
    cores = int(sec["load"][1])
    s.add(OK if float(load[2]) / cores < LOAD_PER_CORE_WARN else WARN,
          "Load (1/5/15 min)", f"{load[0]} / {load[1]} / {load[2]} on {cores} cores")

    mem = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in sec["mem"]}
    used = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"])
    s.add(pct_status(used, MEM_WARN), "Memory", f"{used:.0f}% used of {human_bytes(mem['MemTotal'])}")

    for line in sec.get("disk", []):
        mount, size, used_b = line.rsplit(None, 2)   # mount paths may contain spaces
        size, used_b = int(size), int(used_b)
        if size < 2 * 1024**3:          # skip /boot, /boot/efi and other small mounts
            continue
        p = 100 * used_b / size
        s.add(pct_status(p, DISK_WARN, DISK_CRIT), f"Disk {mount}",
              f"{p:.0f}% used ({human_bytes(size - used_b)} free)")

    containers = [json.loads(x) for x in sec.get("docker", [])]
    if containers:
        running = sum(c.get("State") == "running" for c in containers)
        problems = [p for p in map(classify_container, containers) if p]
        s.add(OK, "Containers", f"{running} running, {len(containers) - running} stopped")
        for status, desc in problems:
            s.add(status, "Container", desc)

    failed = sec.get("failed", [])
    s.add(WARN if failed else OK, "Failed systemd units",
          ", ".join(f.split()[0] for f in failed) if failed else "none")

    for line in sec.get("gpu", []):
        gname, util, mused, mtotal, temp = [x.strip() for x in line.split(",")]
        s.add(OK if int(temp) < GPU_TEMP_WARN else WARN, f"GPU {gname}",
              f"{util}% busy, {mused}/{mtotal} MiB VRAM, {temp} °C")
    return s


# Runs inside TrueNAS as root (python3 there is the system one; stdlib only).
TRUENAS_SCRIPT = r"""
import json, subprocess as sp, time
def m(*a): return json.loads(sp.check_output(["midclt", "call", *a]))
def sh(c, t=120):
    try: return sp.run(c, shell=True, capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception as e: return "ERR " + type(e).__name__
o = {}
o["pools"] = [{"name": p["name"], "status": p["status"], "healthy": p["healthy"]} for p in m("pool.query")]
o["zfs"] = [l.split("\t") for l in sh("zfs list -Hp -d 0 -o name,used,avail").splitlines()]
o["apps"] = [{"name": a["name"], "state": a["state"]} for a in m("app.query")]
o["alerts"] = [{"level": a["level"], "text": a["formatted"]} for a in m("alert.list") if not a.get("dismissed")]
snap = sh("zfs list -Hp -t snapshot -o name,creation -s creation storage-tank/ix-apps | grep '@bk-' | tail -1")
o["backup"] = snap.split("\t") if snap and not snap.startswith("ERR") else None
q = sh("docker exec mail-relay postqueue -p 2>&1")
o["mailq"] = q
o["down"] = sh("curl -s -o /dev/null -w '%{speed_download} %{time_total} %{http_code}' --max-time 45 'https://speed.cloudflare.com/__down?bytes=__DOWN__'")
o["up"] = sh("head -c __UP__ /dev/urandom | curl -s -o /dev/null -w '%{speed_upload} %{time_total} %{http_code}' --max-time 45 --data-binary @- https://speed.cloudflare.com/__up")
o["ping"] = sh("ping -c 5 -q 1.1.1.1 | tail -1")
o["pubip"] = sh("curl -s --max-time 8 https://ifconfig.me")
o["endpoints"] = {u: sh("curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time 20 '" + u + "'") for u in __ENDPOINTS__}
print(json.dumps(o))
"""


def collect_truenas() -> tuple[Section, Section, dict]:
    nas, net = Section("TrueNAS (aeacus-backup)"), Section("Internet & public services")
    code = (TRUENAS_SCRIPT.replace("__DOWN__", str(SPEED_DOWN_BYTES))
            .replace("__UP__", str(SPEED_UP_BYTES)).replace("__ENDPOINTS__", repr(ENDPOINTS)))
    try:
        res = json.loads(run(["qm", "guest", "exec", TRUENAS_VMID, "--timeout", "400", "--",
                              "python3", "-c", code], timeout=420))
        d = json.loads(res["out-data"])
    except Exception as e:  # noqa: BLE001 - any failure here is itself the finding
        nas.add(CRIT, "Reachable via guest agent", f"no ({type(e).__name__}: {str(e)[:120]})")
        return nas, net, {}

    for p in d["pools"]:
        nas.add(OK if p["healthy"] and p["status"] == "ONLINE" else CRIT, f"Pool {p['name']}", p["status"])
    for name, used, avail in d["zfs"]:
        used, avail = int(used), int(avail)
        pct = 100 * used / (used + avail)
        nas.add(pct_status(pct, DISK_WARN, DISK_CRIT), f"Space {name}",
                f"{pct:.0f}% used ({human_bytes(avail)} free)")

    apps = d["apps"]
    bad = [a for a in apps if a["state"] != "RUNNING" and a["name"] not in TRUENAS_EXPECTED_STOPPED]
    # app.query's upgrade_available is true for every app on this box, so use TrueNAS's own update alerts instead.
    upg = sorted({m_.group(1) for a in d["alerts"] for m_ in [re.search(r'update is available for "([^"]+)"', a["text"])] if m_})
    nas.add(OK, "Apps", f"{sum(a['state'] == 'RUNNING' for a in apps)} running of {len(apps)}"
            + (f"; updates available: {', '.join(upg)}" if upg else ""))
    for a in bad:
        nas.add(CRIT if a["state"] in ("CRASHED", "STOPPED") else WARN, f"App {a['name']}", a["state"])

    for a in d["alerts"]:
        lvl = a["level"]
        if lvl in ("CRITICAL", "ALERT", "EMERGENCY", "ERROR"):
            nas.add(CRIT, f"Alert ({lvl})", a["text"][:200])
        elif lvl == "WARNING":
            nas.add(WARN, "Alert (WARNING)", a["text"][:200])
    minor = sum(a["level"] in ("INFO", "NOTICE") for a in d["alerts"])
    if minor:
        nas.add(OK, "Info/notice alerts", f"{minor} (see TrueNAS UI)")

    if d.get("backup"):
        name, created = d["backup"]
        age_h = (dt.datetime.now().timestamp() - int(created)) / 3600
        nas.add(OK if age_h <= BACKUP_MAX_AGE_H else WARN, "Latest backup snapshot",
                f"{name.split('@')[1]} ({age_h:.0f}h ago)")
    else:
        nas.add(WARN, "Latest backup snapshot", "none found")

    q = d["mailq"]
    if "Mail queue is empty" in q:
        nas.add(OK, "Mail relay queue", "empty")
    elif q.startswith("ERR") or "No such container" in q or "Error" in q:
        nas.add(WARN, "Mail relay queue", q[:120])
    else:
        n = sum(1 for line in q.splitlines() if line[:1].isalnum() and not line.startswith("-"))
        nas.add(WARN, "Mail relay queue", f"{n} message(s) waiting")

    metrics: dict = {}
    for key, label in (("down", "Download"), ("up", "Upload")):
        try:
            speed, secs, code_ = d[key].split()
            mbps = float(speed) * 8 / 1e6
            metrics[key] = round(mbps, 2)
            timed_out = float(secs) >= 44
            net.add(WARN if timed_out or code_ != "200" else OK, label,
                    f"{mbps:.1f} Mbit/s" + (" (timed out, partial)" if timed_out else ""))
        except ValueError:
            net.add(WARN, label, f"test failed ({d[key][:80]})")
    rtt = d["ping"].split("=")[-1].strip().split("/")
    net.add(OK, "Latency to 1.1.1.1", f"{float(rtt[1]):.0f} ms avg" if len(rtt) >= 3 else "n/a")
    net.add(OK, "Public IP", d["pubip"] or "unknown")
    for url, res_ in d["endpoints"].items():
        try:
            code_, secs = res_.split()
            up = code_ != "000" and int(code_) < 500
            net.add(OK if up else CRIT, url.split("//")[1].split("/")[0], f"HTTP {code_} in {float(secs):.1f}s")
        except ValueError:
            net.add(CRIT, url, f"check failed ({res_[:60]})")
    return nas, net, metrics


# --------------------------------------------------------------------------- history
def update_history(metrics: dict) -> dict[str, float]:
    """Append today's metrics; return 7-day medians of earlier days for comparison."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    rows = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            rows = [json.loads(line) for line in f if line.strip()][-14:]
    medians = {}
    for key in ("down", "up"):
        vals = [r[key] for r in rows[-7:] if r.get(key) is not None]
        if vals:
            medians[key] = statistics.median(vals)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({"date": dt.date.today().isoformat(), **metrics}) + "\n")
    return medians


# --------------------------------------------------------------------------- rendering
COLORS = {OK: "#1a7f37", WARN: "#9a6700", CRIT: "#cf222e"}
ICONS = {OK: "✅", WARN: "⚠️", CRIT: "❌"}


def summary_line(sections: list[Section]) -> tuple[str, list[tuple[str, str, str, str]]]:
    issues = [(st, sec.title, lbl, val) for sec in sections for st, lbl, val in sec.issues]
    issues.sort(key=lambda i: -_RANK[i[0]])
    crit = sum(i[0] == CRIT for i in issues)
    warn = len(issues) - crit
    if not issues:
        return "All systems healthy", issues
    parts = ([f"{crit} critical"] if crit else []) + ([f"{warn} warning{'s' * (warn != 1)}"] if warn else [])
    return ", ".join(parts), issues


def render_text(sections: list[Section], when: str) -> str:
    headline, issues = summary_line(sections)
    out = [f"Homelab report - {when}", headline, ""]
    if issues:
        out += ["Needs attention:"] + [f"  [{st.upper()}] {sec} - {lbl}: {val}" for st, sec, lbl, val in issues] + [""]
    for sec in sections:
        out.append(f"== {sec.title} ==")
        out += [f"  {ICONS[st]} {lbl}: {val}" for st, lbl, val in sec.rows]
        out.append("")
    return "\n".join(out)


def render_html(sections: list[Section], when: str) -> str:
    headline, issues = summary_line(sections)
    worst = max((s.status for s in sections), key=_RANK.get, default=OK)
    e = html.escape
    parts = [
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:auto;color:#1f2328'>",
        f"<h2 style='margin:0 0 4px'>Homelab report</h2><div style='color:#59636e'>{e(when)}</div>",
        f"<div style='margin:16px 0;padding:12px 16px;border-radius:8px;background:{COLORS[worst]}1a;"
        f"border-left:4px solid {COLORS[worst]};font-size:16px'><b>{ICONS[worst]} {e(headline)}</b></div>",
    ]
    if issues:
        parts.append("<h3 style='margin:20px 0 8px'>Needs attention</h3><ul style='padding-left:20px;margin:0'>")
        parts += [f"<li style='margin:4px 0'><span style='color:{COLORS[st]};font-weight:600'>{st.upper()}</span> "
                  f"{e(sec)}: <b>{e(lbl)}</b> {e(val)}</li>" for st, sec, lbl, val in issues]
        parts.append("</ul>")
    for sec in sections:
        parts.append(f"<h3 style='margin:24px 0 6px;border-bottom:1px solid #d1d9e0;padding-bottom:4px'>"
                     f"{ICONS[sec.status]} {e(sec.title)}</h3><table style='border-collapse:collapse;width:100%;font-size:14px'>")
        for st, lbl, val in sec.rows:
            parts.append(f"<tr><td style='padding:3px 8px 3px 0;width:34%;color:#59636e;vertical-align:top'>{e(lbl)}</td>"
                         f"<td style='padding:3px 0;color:{COLORS[st] if st != OK else '#1f2328'}'>{e(val)}</td></tr>")
        parts.append("</table>")
    parts.append("<p style='color:#59636e;font-size:12px;margin-top:24px'>Sent by homelab_report.py on the Proxmox host "
                 "(home-lab repo: homelab/monitoring/daily-report).</p></div>")
    return "".join(parts)


def send(subject: str, text: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Homelab <{MAIL_FROM}>", MAIL_TO, subject
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
        s.send_message(msg)


# --------------------------------------------------------------------------- main
def safe(title: str, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - one broken collector must not kill the report
        s = Section(title)
        s.add(CRIT, "Collector failed", f"{type(e).__name__}: {str(e)[:150]}")
        return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the report instead of emailing it")
    args = ap.parse_args()

    when = dt.datetime.now().strftime("%A %d %B %Y, %H:%M")
    sections: list[Section] = [safe("Proxmox host", collect_pve)]
    nas, net, metrics = collect_truenas()
    if metrics and not args.dry_run:
        medians = update_history(metrics)
        for key, label in (("down", "Download"), ("up", "Upload")):
            if key in medians:
                net.add(OK, f"{label} 7-day median", f"{medians[key]:.1f} Mbit/s")
    sections += [net, nas]
    sections += [safe(name, collect_ssh_host, name, target) for name, target in SSH_HOSTS.items()]

    text = render_text(sections, when)
    if args.dry_run:
        print(text)
        return 0
    headline, issues = summary_line(sections)
    worst = max((s.status for s in sections), key=_RANK.get, default=OK)
    send(f"{ICONS[worst]} Homelab {dt.date.today():%a %d %b}: {headline}", text, render_html(sections, when))
    print(f"sent to {MAIL_TO}: {headline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
