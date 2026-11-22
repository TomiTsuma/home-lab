#!/usr/bin/env python3
"""Aether (ereuna) service health alerts — runs on the Proxmox host (pve) from a systemd timer.

Every 5 minutes: ship probes/host_probe.py (+ probes/container_probe.py) to aeacus-data-handler over SSH,
collect ~60 checks across 8 layers (see host_probe.py), add pve-side checks (host RAM, how much of the
aeacus VM is swapped out), then decide whether to email:

  ALERT      a critical check has failed on CONFIRM_RUNS consecutive runs and wasn't alerted yet
  REMINDER   still failing, REMIND_HOURS since the last email
  RECOVERED  everything that was alerted on is passing again
  --report   full report regardless of state (daily timer, 07:15)

Email goes through the TrueNAS mail-relay (no auth on the LAN). Standard library only.

Usage:  aether_health.py [--report] [--dry-run]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
AEACUS = os.environ.get("AETHER_HOST", "aeacus@192.168.100.9")
AEACUS_VMID = "100"
SMTP_HOST, SMTP_PORT = os.environ.get("SMTP_HOST", "192.168.100.10"), 587
MAIL_FROM = os.environ.get("REPORT_FROM", "aether-health@ereuna.org")
MAIL_TO = os.environ.get("REPORT_TO", "tomitsuma@ereuna.org")
STATE_FILE = Path(os.environ.get("AETHER_STATE", "/var/lib/aether-health/state.json"))
# Known, accepted problems: one "Group|Check name" per line (copy it from an email). Matching critical checks
# are shown as warnings ("acknowledged") so they stop paging. Delete the line once fixed.
ACK_FILE = Path(os.environ.get("AETHER_ACK", "/etc/aether-health/acknowledged.txt"))

CONFIRM_RUNS = 2          # a check must fail this many runs in a row (= 10 min) before it alerts
REMIND_HOURS = 6
VM_SWAP_WARN_MB, VM_SWAP_CRIT_MB = 256, 1024
HOST_AVAIL_WARN_MB, HOST_AVAIL_CRIT_MB = 2048, 512

OK, WARN, CRIT = "ok", "warn", "crit"
RANK = {OK: 0, WARN: 1, CRIT: 2}
ICON = {OK: "✅", WARN: "⚠️", CRIT: "❌"}
COLOR = {OK: "#1a7f37", WARN: "#9a6700", CRIT: "#cf222e"}


def check(group, name, status, detail, ms=None):
    return {"group": group, "name": name, "status": status, "detail": detail, "ms": ms}


# ----------------------------------------------------------------------------- collection
def build_bundle() -> str:
    container_src = (HERE / "probes" / "container_probe.py").read_text()
    return f"CONTAINER_PROBE_SRC = {container_src!r}\n" + (HERE / "probes" / "host_probe.py").read_text()


def collect_aeacus() -> tuple[list[dict], dict]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", AEACUS, "nice", "-n", "10", "python3", "-"]
    try:
        r = subprocess.run(cmd, input=build_bundle(), capture_output=True, text=True, timeout=420)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        return data["checks"], data["meta"]
    except subprocess.TimeoutExpired:
        return [check("Probe", "aeacus-data-handler", CRIT, "probe did not finish in 7 min — host unresponsive?")], {}
    except Exception as e:  # noqa: BLE001
        err = (locals().get("r").stderr.strip()[-200:] if locals().get("r") else "") or f"{type(e).__name__}: {e}"
        return [check("Probe", "aeacus-data-handler", CRIT, f"SSH/probe failed: {err}")], {}


def collect_pve() -> list[dict]:
    """The layer underneath aeacus: if pve runs out of RAM it swaps the VM's memory and everything stalls."""
    g, out = "Proxmox host (under aeacus-data-handler)", []
    try:
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        avail = mem["MemAvailable"] // 1024
        st = CRIT if avail < HOST_AVAIL_CRIT_MB else WARN if avail < HOST_AVAIL_WARN_MB else OK
        out.append(check(g, "pve RAM available", st, f"{avail} MB of {mem['MemTotal'] // 1024} MB"))
        pid = Path(f"/run/qemu-server/{AEACUS_VMID}.pid").read_text().strip()
        status = {l.split(":")[0]: l.split()[1] for l in open(f"/proc/{pid}/status") if ":" in l and len(l.split()) > 1}
        swap = int(status.get("VmSwap", "0")) // 1024
        st = CRIT if swap > VM_SWAP_CRIT_MB else WARN if swap > VM_SWAP_WARN_MB else OK
        out.append(check(g, "aeacus VM memory swapped out by pve", st,
                         f"{swap} MB of the VM's RAM is on pve swap" + (" — guest stalls on every touch" if st != OK else "")))
    except Exception as e:  # noqa: BLE001
        out.append(check(g, "pve memory", WARN, f"could not read: {type(e).__name__}: {e}"))
    return out


# ----------------------------------------------------------------------------- alert state machine
def key(c: dict) -> str:
    return f"{c['group']}|{c['name']}"


def apply_acks(checks: list[dict], acked: set[str]) -> list[dict]:
    out = []
    for c in checks:
        if c["status"] == CRIT and key(c) in acked:
            c = {**c, "status": WARN, "detail": c["detail"] + " [acknowledged]"}
        out.append(c)
    return out


def load_acks() -> set[str]:
    try:
        return {l.strip() for l in ACK_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def evaluate(checks: list[dict], state: dict, now: float, report: bool = False) -> tuple[str | None, dict]:
    """Return (kind of email to send or None, new state). Pure function — unit tested."""
    fails = dict(state.get("fails", {}))
    current_crit = {key(c) for c in checks if c["status"] == CRIT}
    for k in list(fails):
        if k not in current_crit:
            del fails[k]
    for k in current_crit:
        fails[k] = fails.get(k, 0) + 1
    confirmed = {k for k, n in fails.items() if n >= CONFIRM_RUNS}
    alerted = set(state.get("alerted", []))
    last = state.get("last_email", 0)

    kind = None
    if confirmed - alerted:
        kind = "alert"                       # something new is confirmed broken
    elif alerted and not current_crit:
        kind = "recovered"                   # everything we paged about passes again
    elif confirmed and now - last >= REMIND_HOURS * 3600:
        kind = "reminder"
    if report and kind is None:
        kind = "report"

    if kind in ("alert", "reminder"):
        alerted = confirmed
    elif kind == "recovered":
        alerted = set()
    else:
        alerted &= current_crit              # forget alerted checks that cleared while others still fail
    return kind, {"fails": fails, "alerted": sorted(alerted), "last_email": now if kind else last}


# ----------------------------------------------------------------------------- rendering
def worst(checks):
    return max((c["status"] for c in checks), key=RANK.get, default=OK)


def subject_for(kind: str, checks: list[dict], new_keys: set[str]) -> str:
    crit = [c for c in checks if c["status"] == CRIT]
    warn = [c for c in checks if c["status"] == WARN]
    when = time.strftime("%a %d %b %H:%M")
    if kind == "recovered":
        return f"✅ Aether recovered — all critical checks passing ({when})"
    if not crit:
        tail = f", {len(warn)} warnings" if warn else ""
        return f"{'⚠️' if warn else '✅'} Aether {when}: {len(checks)} checks, 0 critical{tail}"
    first = next((c for c in crit if key(c) in new_keys), crit[0])
    label = {"alert": "🔴 Aether DOWN", "reminder": "🔴 Aether still failing", "report": "🔴 Aether"}[kind]
    more = f" (+{len(crit) - 1} more)" if len(crit) > 1 else ""
    return f"{label}: {first['group'].split(' (')[0]} — {first['name']}{more}"


def grouped(checks):
    out: dict[str, list[dict]] = {}
    for c in checks:
        out.setdefault(c["group"], []).append(c)
    return out


def render_text(kind, checks, meta, header) -> str:
    lines = [header, ""]
    problems = [c for c in checks if c["status"] != OK]
    if problems:
        lines.append("PROBLEMS")
        for c in sorted(problems, key=lambda c: -RANK[c["status"]]):
            lines.append(f"  {ICON[c['status']]} [{c['group']}] {c['name']}: {c['detail']}")
        lines.append("")
    for g, cs in grouped(checks).items():
        lines.append(f"{ICON[worst(cs)]} {g}")
        for c in cs:
            lines.append(f"    {ICON[c['status']]} {c['name']}: {c['detail']}")
    lines += ["", f"Probe ran on {meta.get('host', '?')} in {meta.get('seconds', '?')} s."]
    return "\n".join(lines)


def render_html(kind, checks, meta, header) -> str:
    e = html.escape
    parts = [f"<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;color:#1f2328;max-width:820px'>",
             f"<h2 style='margin:0 0 4px'>{e(header)}</h2>",
             f"<p style='color:#59636e;margin:0 0 16px'>{len(checks)} checks · probe on {e(str(meta.get('host', '?')))} "
             f"took {e(str(meta.get('seconds', '?')))} s · {e(time.strftime('%Y-%m-%d %H:%M %Z'))}</p>"]
    problems = sorted([c for c in checks if c["status"] != OK], key=lambda c: -RANK[c["status"]])
    if problems:
        parts.append("<h3 style='margin:16px 0 6px'>Problems</h3><ul style='padding-left:18px;margin:0'>")
        for c in problems:
            parts.append(f"<li style='margin:3px 0'><b style='color:{COLOR[c['status']]}'>{ICON[c['status']]} "
                         f"{e(c['name'])}</b> <span style='color:#59636e'>({e(c['group'])})</span><br>{e(c['detail'])}</li>")
        parts.append("</ul>")
    for g, cs in grouped(checks).items():
        st = worst(cs)
        parts.append(f"<h3 style='margin:18px 0 6px;color:{COLOR[st]}'>{ICON[st]} {e(g)}</h3>"
                     "<table style='border-collapse:collapse;width:100%'>")
        for c in cs:
            parts.append(f"<tr><td style='padding:3px 8px;border-bottom:1px solid #eee;width:22px'>{ICON[c['status']]}</td>"
                         f"<td style='padding:3px 8px;border-bottom:1px solid #eee;white-space:nowrap'>{e(c['name'])}</td>"
                         f"<td style='padding:3px 8px;border-bottom:1px solid #eee;color:#59636e'>{e(c['detail'])}</td></tr>")
        parts.append("</table>")
    parts.append("<p style='color:#59636e;margin-top:20px;font-size:12px'>Sent by aether-health on pve "
                 "(homelab/monitoring/aether-health). Alerts fire after 2 consecutive failed runs (10 min).</p></div>")
    return "".join(parts)


def header_for(kind, checks, state_before):
    crit = sum(c["status"] == CRIT for c in checks)
    warn = sum(c["status"] == WARN for c in checks)
    return {"alert": f"Aether: {crit} critical check(s) failing",
            "reminder": f"Aether: still failing — {crit} critical",
            "recovered": "Aether recovered: all critical checks pass",
            "report": f"Aether daily report: {crit} critical, {warn} warnings"}[kind]


def send(subject, text, body_html):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Aether health <{MAIL_FROM}>", MAIL_TO, subject
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
        s.send_message(msg)


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="send the full report even if nothing changed")
    ap.add_argument("--dry-run", action="store_true", help="print, don't send or save state")
    args = ap.parse_args()

    checks, meta = collect_aeacus()
    checks = apply_acks(collect_pve() + checks, load_acks())

    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    now = time.time()
    kind, new_state = evaluate(checks, state, now, report=args.report)
    if args.dry_run:
        kind = kind or "report"

    if kind:
        new_keys = set(new_state["alerted"]) - set(state.get("alerted", []))
        subject = subject_for(kind, checks, new_keys)
        header = header_for(kind, checks, state)
        text = render_text(kind, checks, meta, header)
        if args.dry_run:
            print(subject, "\n", text, sep="")
            return 0
        send(subject, text, render_html(kind, checks, meta, header))
        print(f"sent {kind}: {subject}")
    else:
        crit = sum(c["status"] == CRIT for c in checks)
        print(f"no email: {len(checks)} checks, {crit} critical, pending={ {k: n for k, n in new_state['fails'].items()} }")

    if not args.dry_run:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(new_state, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
