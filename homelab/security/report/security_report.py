#!/usr/bin/env python3
"""Homelab security posture: evening report, nightly vulnerability scan, hourly urgent alerts. Runs on pve.

  security_report.py              evening report (19:00 EAT): collect, diff against baselines, score, email
  security_report.py --scan       nightly (01:30): Trivy client scans of every running container image,
                                  matched against the trivy-server on TrueNAS -> /var/lib/homelab-security/vulns.json
  security_report.py --urgent     hourly: email immediately for high-risk Suricata alerts (rate-limited per rule)
  add --dry-run to print instead of sending and leave baselines untouched.

Sources (no agents installed on the hosts; read-only on everything except pve's own state dir):
  Suricata on pve (vmbr0 + tailscale0)  network IDS alerts and flow records ("unusual traffic")
  probes/host_audit.py over SSH         pve, aeacus-data-handler, aeacus-dl, minos: logins, sudo, accounts,
                                        file integrity, listening ports, updates, sshd config, docker risks
  probes/truenas_audit.py via qm agent  TrueNAS logins, SSH, users/2FA, shares, listening ports
  nmap from pve                         LAN device inventory and open ports, diffed against yesterday
  tailscale status                      tailnet device inventory
  Trivy (client -> trivy-server)        container image CVEs (from the nightly --scan)

Standard library only.
"""
from __future__ import annotations

import argparse
import gzip
import html
import ipaddress
import json
import os
import re
import smtplib
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("SEC_STATE", "/var/lib/homelab-security"))
ACK_FILE = Path(os.environ.get("SEC_ACK", "/etc/homelab-security/acknowledged.txt"))
TOKEN_FILE = Path("/etc/homelab-security/trivy.token")
EVE = Path("/var/log/suricata/eve.json")
SMTP_HOST, SMTP_PORT = os.environ.get("SMTP_HOST", "192.168.100.10"), 587
MAIL_FROM = os.environ.get("REPORT_FROM", "security@ereuna.org")
# Recipient comes only from the environment (systemd: EnvironmentFile=/etc/homelab-security/report.env,
# one line `REPORT_TO=you@example.org`), so no address lives in the repo.
MAIL_TO = os.environ.get("REPORT_TO", "")

SSH_HOSTS = {"aeacus-data-handler": "aeacus@192.168.100.9", "aeacus-dl": "aeacus@192.168.100.17",
             "minos": "minos@192.168.100.12"}
TRUENAS_VMID = "102"
TRIVY_SERVER = "http://192.168.100.10:4954"
TRIVY_IMAGE = "aquasec/trivy:0.58.1"
# Hosts whose docker the scanner can reach. aeacus-dl: user not in the docker group and VM 101 has no guest agent.
SCAN_HOSTS = {"aeacus-data-handler": "aeacus@192.168.100.9", "minos": "minos@192.168.100.12", "truenas": None}
LAN = "192.168.100.0/24"
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                                              "127.0.0.0/8", "169.254.0.0/16", "fd00::/8", "fe80::/10", "::1/128",
                                              "224.0.0.0/4", "255.255.255.255/32", "ff00::/8")]
COMMON_OUT_PORTS = {53, 80, 123, 443, 853, 22, 465, 587, 993, 995, 3478, 3479, 5349, 41641, 51820, 7844, 8443, 5222, 5228, 19302}
HIGH_RISK_CLASSES = {"trojan-activity", "command-and-control", "exploit-kit", "targeted-activity", "attempted-admin",
                     "successful-admin", "successful-user", "web-application-attack", "domain-c2", "credential-theft",
                     "coin-mining", "shellcode-detect", "successful-recon-largescale"}

NAMES = {"192.168.100.1": "router", "192.168.100.9": "aeacus-data-handler", "192.168.100.10": "truenas",
         "192.168.100.12": "minos", "192.168.100.17": "aeacus-dl", "192.168.100.145": "pve"}
POLICY_CLASSES = {"policy-violation", "misc-activity", "protocol-command-decode", "not-suspicious", "unknown",
                  "bad-unknown", "attempted-recon", "network-scan", "string-detect", "external-ip-check"}

CRIT, HIGH, MED, LOW, INFO = "critical", "high", "medium", "low", "info"
SEV_ORDER = [CRIT, HIGH, MED, LOW, INFO]
WEIGHT = {CRIT: 20, HIGH: 8, MED: 3, LOW: 1, INFO: 0}
ICON = {CRIT: "🟥", HIGH: "🟧", MED: "🟨", LOW: "🟦", INFO: "⬜"}
COLOR = {CRIT: "#cf222e", HIGH: "#bc4c00", MED: "#9a6700", LOW: "#0969da", INFO: "#59636e"}


# ----------------------------------------------------------------------------- helpers
def finding(sev, area, host, title, detail="", fix="", fid=None):
    return {"sev": sev, "area": area, "host": host, "title": title, "detail": detail, "fix": fix,
            "id": fid or f"{area}:{host}:{title}"}


def name(ip: str) -> str:
    """'192.168.100.9' -> 'aeacus-data-handler (192.168.100.9)'; IPv6 compressed."""
    try:
        ip = ipaddress.ip_address(ip).compressed
    except ValueError:
        pass
    return f"{NAMES[ip]} ({ip})" if ip in NAMES else ip


def is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    return any(a in n for n in PRIVATE if n.version == a.version)


def sh(cmd, timeout=120, stdin=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, input=stdin)
    return r.returncode, r.stdout, r.stderr


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    tmp.replace(p)


def load_acks() -> set[str]:
    try:
        return {l.strip() for l in ACK_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def guest_exec(script: str, timeout=300) -> dict:
    rc, out, err = sh(["qm", "guest", "exec", TRUENAS_VMID, "--timeout", str(timeout), "--", "python3", "-c", script],
                      timeout=timeout + 30)
    d = json.loads(out)
    if d.get("exitcode") != 0:
        raise RuntimeError((d.get("err-data") or "")[-300:])
    return json.loads(d["out-data"])


# ----------------------------------------------------------------------------- collectors
def collect_hosts() -> dict:
    probe = (HERE / "probes" / "host_audit.py").read_text()
    out = {}
    try:
        rc, o, e = sh(["python3", "-"], stdin=probe, timeout=300)
        out["pve"] = json.loads(o)
    except Exception as ex:  # noqa: BLE001
        out["pve"] = {"error": str(ex)}
    for name, target in SSH_HOSTS.items():
        try:
            rc, o, e = sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, "nice", "-n", "10", "python3", "-"],
                          stdin=probe, timeout=300)
            out[name] = json.loads(o) if rc == 0 else {"error": (e or "ssh failed").strip()[-200:]}
        except Exception as ex:  # noqa: BLE001
            out[name] = {"error": f"{type(ex).__name__}: {ex}"}
    return out


def collect_truenas() -> dict:
    try:
        return guest_exec((HERE / "probes" / "truenas_audit.py").read_text())
    except Exception as ex:  # noqa: BLE001
        return {"error": f"{type(ex).__name__}: {ex}"}


def eve_lines(since: datetime):
    """Yield parsed EVE events newer than `since` from eve.json and its rotations (newest last)."""
    files = sorted(EVE.parent.glob("eve.json.*"), key=lambda p: p.stat().st_mtime) + [EVE]
    for p in files:
        try:
            if p.stat().st_mtime < since.timestamp():
                continue
            fh = gzip.open(p, "rt", errors="replace") if p.suffix == ".gz" else open(p, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                try:
                    ts = datetime.strptime(ev["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(
                        tzinfo=timezone(timedelta(hours=int(ev["timestamp"][-5:-2]), minutes=int(ev["timestamp"][-2:]))))
                except (KeyError, ValueError):
                    continue
                if ts >= since:
                    yield ev


def analyze_suricata(events) -> dict:
    """Aggregate alerts and flows. Pure function of the event stream (unit tested)."""
    alerts, noise = {}, 0
    inbound, outbound_odd, ext_bytes, dests = {}, {}, {}, {}
    out_ports = set()     # (internal ip, internal port) that sent to the internet: replies to them aren't "inbound"
    for ev in events:
        et = ev.get("event_type")
        if et == "alert":
            a = ev["alert"]
            sig = a.get("signature", "?")
            if sig.startswith("SURICATA "):         # engine/decoder events, not threats
                noise += 1
                continue
            k = a.get("signature_id")
            e = alerts.setdefault(k, {"sid": k, "sig": sig, "sev": a.get("severity", 3), "class": a.get("category", ""),
                                      "classtype": (a.get("metadata") or {}).get("classtype", [""])[0] if a.get("metadata") else "",
                                      "count": 0, "src": {}, "dst": {}, "last": ev["timestamp"]})
            e["count"] += 1
            e["last"] = ev["timestamp"]
            e["src"][ev.get("src_ip")] = e["src"].get(ev.get("src_ip"), 0) + 1
            e["dst"][f"{ev.get('dest_ip')}:{ev.get('dest_port', '')}"] = e["dst"].get(f"{ev.get('dest_ip')}:{ev.get('dest_port', '')}", 0) + 1
        elif et == "flow":
            src, dst, dport = ev.get("src_ip", ""), ev.get("dest_ip", ""), ev.get("dest_port")
            f = ev.get("flow", {})
            b = (f.get("bytes_toserver") or 0) + (f.get("bytes_toclient") or 0)
            sp, dp = is_private(src), is_private(dst)
            if sp and not dp:                       # internal -> internet
                out_ports.add((src, ev.get("src_port")))
                ext_bytes[src] = ext_bytes.get(src, 0) + b
                d = dests.setdefault(dst, {"bytes": 0, "hosts": set(), "ports": set()})
                d["bytes"] += b
                d["hosts"].add(src)
                d["ports"].add(dport)
                if ev.get("proto") in ("TCP", "UDP") and dport not in COMMON_OUT_PORTS and (dport or 0) < 49152:
                    k = f"{name(src)} → {name(dst)}:{dport}/{ev.get('proto')}"
                    outbound_odd[k] = outbound_odd.get(k, 0) + 1
            elif dp and not sp:                     # internet -> internal, unsolicited
                established = f.get("state") in ("established", "closed") and (f.get("pkts_toclient") or 0) > 0
                if ev.get("proto") == "TCP" and not established:
                    continue                        # SYN with no answer: blocked/filtered, not reachable
                k = f"{dst}:{dport}/{ev.get('proto')}"
                e = inbound.setdefault(k, {"count": 0, "sources": set(), "bytes": 0, "proto": ev.get("proto"), "dst": (dst, dport)})
                e["count"] += 1
                e["sources"].add(src)
                e["bytes"] += b
    # A UDP "inbound" flow to a port the host itself used outbound is a reply whose flow Suricata had already
    # expired (cloudflared QUIC, WireGuard/Tailscale keepalives), not a connection opened from the internet.
    inbound = {k: v for k, v in inbound.items() if v["proto"] != "UDP" or v["dst"] not in out_ports}
    top_dests = sorted(({"ip": ip, "bytes": d["bytes"], "hosts": sorted(d["hosts"]), "ports": sorted(p for p in d["ports"] if p)[:6]}
                        for ip, d in dests.items()), key=lambda x: -x["bytes"])[:10]
    return {"alerts": sorted(alerts.values(), key=lambda a: (a["sev"], -a["count"])), "noise": noise,
            "inbound": {k: {"count": v["count"], "sources": len(v["sources"]), "bytes": v["bytes"],
                            "sample": sorted(v["sources"])[:5]} for k, v in inbound.items()},
            "outbound_odd": dict(sorted(outbound_odd.items(), key=lambda kv: -kv[1])[:15]),
            "ext_bytes": ext_bytes, "top_dests": top_dests}


def collect_suricata(hours=24) -> dict:
    rc, active, _ = sh(["systemctl", "is-active", "suricata"])
    mem = 0
    try:
        mem = int(Path("/sys/fs/cgroup/system.slice/suricata.service/memory.current").read_text()) // 1048576
    except OSError:
        pass
    rules = Path("/var/lib/suricata/rules/suricata.rules")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    res = analyze_suricata(eve_lines(since))
    res.update({"active": active.strip() == "active", "mem_mb": mem,
                "rules": sum(1 for l in open(rules) if l.startswith("alert")) if rules.exists() else 0,
                "rules_age_h": (time.time() - rules.stat().st_mtime) / 3600 if rules.exists() else None})
    return res


def collect_network() -> dict:
    res = {"devices": {}, "ports": {}, "tailnet": {}}
    rc, out, err = sh(["nmap", "-sn", "-n", "-oX", "-", LAN], timeout=300)
    for h in ET.fromstring(out).findall("host") if out else []:
        ip = mac = vendor = None
        for a in h.findall("address"):
            if a.get("addrtype") == "ipv4":
                ip = a.get("addr")
            elif a.get("addrtype") == "mac":
                mac, vendor = a.get("addr"), a.get("vendor", "")
        if ip:
            res["devices"][mac or f"self:{ip}"] = {"ip": ip, "vendor": vendor or ""}
    ips = sorted({d["ip"] for d in res["devices"].values()}, key=lambda s: tuple(int(x) for x in s.split(".")))
    if ips:
        rc, out, err = sh(["nmap", "-sS", "-n", "-Pn", "-T4", "--top-ports", "1000", "--max-retries", "1",
                           "--host-timeout", "120s", "-oX", "-", *ips], timeout=1200)
        for h in ET.fromstring(out).findall("host") if out else []:
            ip = h.find("address").get("addr")
            ports = []
            for p in h.findall("ports/port"):
                if p.find("state").get("state") == "open":
                    svc = p.find("service")
                    ports.append(f"{p.get('portid')}/{p.get('protocol')} {svc.get('name') if svc is not None else ''}".strip())
            res["ports"][ip] = ports
    rc, out, err = sh(["tailscale", "status", "--json"], timeout=30)
    if rc == 0:
        st = json.loads(out)
        for p in list((st.get("Peer") or {}).values()) + [st.get("Self") or {}]:
            res["tailnet"][p.get("HostName", "?")] = {"os": p.get("OS", ""), "ip": (p.get("TailscaleIPs") or [""])[0],
                                                     "online": p.get("Online"), "last_seen": p.get("LastSeen", "")}
    return res


# ----------------------------------------------------------------------------- findings
def host_findings(name: str, d: dict, base: dict) -> tuple[list, list]:
    f, changes = [], []
    if "error" in d:
        return [finding(MED, "Hosts", name, "Audit could not run", d["error"], "Check SSH access from pve.")], []
    a = d["auth"]
    ext_fail = {ip: n for ip, n in a["failed_by_ip"].items() if not is_private(ip)}
    int_fail = {ip: n for ip, n in a["failed_by_ip"].items() if is_private(ip)}
    if ext_fail:
        f.append(finding(HIGH, "Intrusion", name, f"SSH login attempts from the internet ({sum(ext_fail.values())})",
                         ", ".join(f"{ip}×{n}" for ip, n in sorted(ext_fail.items(), key=lambda kv: -kv[1])[:8]),
                         "SSH should not be reachable from the internet: check router port forwards. Enable auto-blocking (CrowdSec/fail2ban) if it must be."))
    for ip, n in int_fail.items():
        if n >= 10:
            f.append(finding(MED, "Intrusion", name, f"{n} failed SSH logins from {ip}",
                             f"usernames tried: {', '.join(list(a['failed_users'])[:6])}", "Find what device that is; a LAN/tailnet host guessing passwords may be compromised."))
    for k, n in a["accepted"].items():
        user, ip, method = k.split("|")
        if not is_private(ip):
            f.append(finding(HIGH, "Intrusion", name, f"SSH login from the internet: {user} from {ip}",
                             f"{n} session(s), method {method}", "If this wasn't you, rotate keys and inspect the host now."))
        elif method == "password":
            f.append(finding(MED, "Hardening", name, f"Password SSH login used ({user} from {ip})", f"{n}×",
                             "Use keys only: PasswordAuthentication no."))
    if a["sudo_fail"]:
        f.append(finding(MED, "Intrusion", name, f"{a['sudo_fail']} failed sudo attempt(s)", "", "Check who tried; expected only if you mistyped."))
    for line in a["account_changes"]:
        f.append(finding(MED, "Integrity", name, "User/group account changed", line, "Confirm you made this change."))
    if [u for u in d["uid0"] if u != "root"]:
        f.append(finding(CRIT, "Integrity", name, "Extra UID-0 (root) account", ", ".join(d["uid0"]), "Remove it; a second root account is a classic backdoor."))

    # Integrity vs baseline (first run just records)
    old = base.get("integrity", {}).get(name)
    if old is not None:
        for path, h in d["integrity"].items():
            if path in old and old[path] != h:
                sev = HIGH if re.search(r"authorized_keys|sudoers|ld\.so\.preload|shadow|passwd$", path) else MED
                f.append(finding(sev, "Integrity", name, f"Changed: {path}", "", "Confirm this change was yours.", fid=f"integrity:{name}:{path}"))
                changes.append(f"{name}: {path} changed")
            elif path not in old:
                f.append(finding(MED, "Integrity", name, f"New: {path}", "", "Confirm this file was expected.", fid=f"integrity:{name}:{path}"))
                changes.append(f"{name}: {path} appeared")
        for path in set(old) - set(d["integrity"]):
            changes.append(f"{name}: {path} removed")
    old_l = base.get("listening", {}).get(name)
    if old_l is not None:
        new = sorted(set(d["listening"]) - set(old_l))
        if new:
            f.append(finding(LOW, "Exposure", name, f"{len(new)} new listening port(s)", ", ".join(new[:12]),
                             "Expected if you deployed something; otherwise find the process with `ss -lntp`.", fid=f"listen:{name}:{','.join(new[:12])}"))
            changes += [f"{name}: now listening {s}" for s in new[:12]]
        gone = sorted(set(old_l) - set(d["listening"]))
        changes += [f"{name}: stopped listening {s}" for s in gone[:8]]

    u = d["updates"]
    if u["security"]:
        f.append(finding(HIGH if u["security"] >= 20 else MED, "Vulnerabilities", name,
                         f"{u['security']} security update(s) pending", ", ".join(u["security_pkgs"][:12]),
                         "apt update && apt upgrade (pve: apt full-upgrade, then reboot when the kernel changed)."))
    if u["reboot_required"]:
        f.append(finding(LOW, "Vulnerabilities", name, "Reboot required to finish updates", "", "Reboot at a quiet time."))
    if d.get("unattended_upgrades") != "active" and name != "pve":
        f.append(finding(LOW, "Hardening", name, "Automatic security updates are off", "", "apt install unattended-upgrades"))
    s = d["sshd"]
    if s.get("passwordauthentication") == "yes":
        f.append(finding(HIGH, "Hardening", name, "SSH accepts passwords", f"({s['source']})", "Set PasswordAuthentication no."))
    if s.get("permitrootlogin") == "yes":
        f.append(finding(LOW, "Hardening", name, "SSH permits root login with any method", "",
                         "Use PermitRootLogin prohibit-password (keys only)."))
    dk = d["docker"]
    if dk.get("available"):
        for r in dk["risky"]:
            sev = MED if ("privileged" in r["issue"] or "docker.sock" in r["issue"] or "host /" in r["issue"]) else LOW
            f.append(finding(sev, "Hardening", name, f"Container {r['container']}: {r['issue']}", "",
                             "Only acceptable for infrastructure containers that need it.", fid=f"docker:{name}:{r['container']}:{r['issue']}"))
        if dk["exposed"]:
            f.append(finding(MED, "Exposure", name, f"{len(dk['exposed'])} database/admin port(s) published on all interfaces",
                             ", ".join(f"{e['service']} :{e['port']} ({e['container']})" for e in dk["exposed"][:14]),
                             "Bind to 127.0.0.1 or drop the `ports:` mapping; services on the same docker network don't need it.",
                             fid=f"exposed:{name}"))
    return f, changes


def truenas_findings(t: dict, base: dict) -> tuple[list, list]:
    if "error" in t:
        return [finding(MED, "Hosts", "truenas", "Audit could not run", t["error"])], []
    f, changes = [], []
    lg = t.get("logins", {})
    for k, n in (lg.get("failed") or {}).items():
        user, addr, cred = k.split("|")
        f.append(finding(HIGH if not is_private(addr) else MED, "Intrusion", "truenas",
                         f"{n} failed TrueNAS login(s) for {user} from {addr}", cred, "Confirm it was you; otherwise investigate the source."))
    known = set(base.get("truenas_login_sources", []))
    for k, n in (lg.get("ok") or {}).items():
        user, addr, cred = k.split("|")
        if known and addr not in known:
            f.append(finding(MED if is_private(addr) else HIGH, "Intrusion", "truenas", f"TrueNAS login from a new address: {user} from {addr}",
                             f"{n}× via {cred}", "Confirm it was you."))
            changes.append(f"truenas: first login from {addr}")
    ssh = t.get("ssh", {})
    if ssh.get("password_auth"):
        f.append(finding(HIGH, "Hardening", "truenas", "TrueNAS SSH accepts passwords", "", "System → Services → SSH: disable password login."))
    if ssh.get("root_login"):
        f.append(finding(MED, "Hardening", "truenas", "TrueNAS SSH allows root login", ""))
    us = t.get("users", {})
    if us.get("twofactor_enabled") is False:
        f.append(finding(LOW, "Hardening", "truenas", "Two-factor authentication is off for the TrueNAS UI", "",
                         "Credentials → 2FA: enable it for admin accounts."))
    for u in us.get("users", []):
        if u.get("ssh_password") and u.get("sudo") and not u.get("locked"):
            f.append(finding(LOW, "Hardening", "truenas", f"User {u['name']} may SSH with a password and has sudo",
                             "global SSH password login is " + ("ON" if ssh.get("password_auth") else "off, so not usable now"),
                             "Untick 'SSH password login' for this user."))
    sh_ = t.get("shares", {})
    for s in sh_.get("smb_guest", []):
        f.append(finding(MED, "Exposure", "truenas", f"SMB share '{s['name']}' allows guest access", s["path"], "Disable guest access unless intended."))
    for s in sh_.get("nfs_open_to_all", []):
        f.append(finding(MED, "Exposure", "truenas", f"NFS export open to every host: {s['path']}", "", "Restrict to specific networks/hosts."))
    old = base.get("listening", {}).get("truenas")
    if old is not None and isinstance(t.get("listening"), list):
        new = sorted(set(t["listening"]) - set(old))
        if new:
            f.append(finding(LOW, "Exposure", "truenas", f"{len(new)} new listening port(s)", ", ".join(new[:12]), "",
                             fid=f"listen:truenas:{','.join(new[:12])}"))
            changes += [f"truenas: now listening {s}" for s in new[:12]]
    return f, changes


def suricata_findings(s: dict, history: list) -> list:
    f = []
    if not s.get("active"):
        f.append(finding(HIGH, "Intrusion", "pve", "Suricata (network IDS) is not running", "",
                         "systemctl status suricata; journalctl -u suricata. Check the memory cap if it was OOM-killed."))
    if s.get("rules_age_h") and s["rules_age_h"] > 72:
        f.append(finding(LOW, "Intrusion", "pve", f"IDS rules are {s['rules_age_h'] / 24:.0f} days old", "", "systemctl start suricata-update"))
    for a in s.get("alerts", []):
        risky = a["classtype"] in HIGH_RISK_CLASSES or re.search(r"\b(TROJAN|MALWARE|EXPLOIT|CnC|C2|COINMINER|Backdoor)\b", a["sig"], re.I)
        policy = a["classtype"] in POLICY_CLASSES or re.match(r"(ET|GPL) (INFO|POLICY|P2P|USER_AGENTS|TA_ABUSED_SERVICES|GAMES|CHAT|DNS) ", a["sig"])
        if risky:
            sev = HIGH if a["sev"] == 1 else MED
        elif policy:
            sev = LOW if a["sev"] == 1 else INFO
        else:
            sev = MED if a["sev"] == 1 else LOW if a["sev"] == 2 else INFO
        srcs = ", ".join(f"{name(ip)}×{n}" for ip, n in sorted(a["src"].items(), key=lambda kv: -kv[1])[:4])
        dsts = ", ".join(f"{d}×{n}" for d, n in sorted(a["dst"].items(), key=lambda kv: -kv[1])[:4])
        f.append(finding(sev, "Intrusion", "network", f"IDS: {a['sig']}", f"{a['count']}× · from {srcs} → {dsts} · sid {a['sid']}",
                         "Look up the rule (sid) and the internal host involved; acknowledge with suricata-sid:<sid> if benign.",
                         fid=f"suricata-sid:{a['sid']}"))
    for k, v in s.get("inbound", {}).items():
        ip = k.split(":")[0]
        if not ip.startswith("192.168.100.") or v["count"] < 3:
            continue
        f.append(finding(MED, "Exposure", NAMES.get(ip, ip), f"Internet connects directly to {k}",
                         f"{v['count']} connections from {v['sources']} source(s), e.g. {', '.join(v['sample'][:3])}",
                         "This is a router port forward or UPnP mapping. Close it unless you need it (prefer Cloudflare Tunnel / Tailscale).",
                         fid=f"inbound:{k}"))
    if s.get("outbound_odd"):
        top = list(s["outbound_odd"].items())[:6]
        f.append(finding(INFO, "Traffic", "network", f"{len(s['outbound_odd'])} outbound flows to uncommon ports",
                         "; ".join(f"{k} ×{n}" for k, n in top), "Usually apps (games, P2P, NTP variants); unknown destinations deserve a look."))
    # Volume anomaly: today's bytes to the internet per host vs its 7-day median.
    for host, b in s.get("ext_bytes", {}).items():
        past = [d.get(host, 0) for d in history[-7:]]
        med = statistics.median(past) if len(past) >= 3 else None
        if med is not None and b > 500 * 2**20 and b > 5 * max(med, 1):
            f.append(finding(MED, "Traffic", host, f"Unusual upload/download volume: {b / 2**30:.1f} GB to the internet",
                             f"7-day median {med / 2**30:.2f} GB", "Check what this host is transferring (backups, updates, or exfiltration)."))
    return f


def network_findings(n: dict, base: dict) -> tuple[list, list]:
    f, changes = [], []
    old_dev = base.get("lan_devices")
    if old_dev is not None:
        for mac, d in n["devices"].items():
            if mac not in old_dev:
                f.append(finding(MED, "Exposure", "network", f"New device on the LAN: {d['ip']} ({d['vendor'] or 'unknown vendor'})",
                                 mac, "Identify it. Unknown devices on the LAN are an intrusion signal.", fid=f"device:{mac}"))
                changes.append(f"LAN: new device {d['ip']} {mac} {d['vendor']}")
    old_ports = base.get("lan_ports")
    if old_ports is not None:
        for ip, ports in n["ports"].items():
            if ip not in old_ports:      # a brand-new host is already reported as a new device
                continue
            new = sorted(set(ports) - set(old_ports[ip]))
            if new:
                f.append(finding(MED, "Exposure", ip, f"New open port(s) on {ip}", ", ".join(new), "Expected if you deployed something new.",
                                 fid=f"lanport:{ip}:{','.join(new)}"))
                changes.append(f"{ip}: now open {', '.join(new)}")
    risky = {"23/tcp": "telnet", "2375/tcp": "Docker API (unauthenticated)", "3389/tcp": "RDP", "5900/tcp": "VNC", "21/tcp": "FTP",
             "11211/tcp": "memcached", "6379/tcp": "Redis", "27017/tcp": "MongoDB"}
    for ip, ports in n["ports"].items():
        hits = [f"{p} ({risky[p.split()[0]]})" for p in ports if p.split()[0] in risky]
        if hits:
            f.append(finding(MED, "Exposure", ip, f"Risky service reachable on the LAN at {ip}", ", ".join(hits),
                             "Restrict it to localhost/docker network or put auth in front.", fid=f"risky:{ip}:{','.join(hits)}"))
    old_tn = base.get("tailnet")
    if old_tn is not None:
        for host, d in n["tailnet"].items():
            if host not in old_tn:
                f.append(finding(MED, "Exposure", "tailnet", f"New device joined the tailnet: {host}", f"{d['os']} {d['ip']}",
                                 "Confirm it's yours; remove it in the Tailscale admin console otherwise.", fid=f"tailnet:{host}"))
                changes.append(f"tailnet: new device {host} ({d['os']})")
    return f, changes


def vuln_findings(v: dict) -> list:
    f = []
    if not v:
        return [finding(LOW, "Vulnerabilities", "all", "No container vulnerability scan yet", "", "Runs nightly at 01:30 (security-scan.timer).")]
    age_h = (time.time() - v.get("finished", 0)) / 3600
    if age_h > 36:
        f.append(finding(LOW, "Vulnerabilities", "all", f"Container vulnerability scan is {age_h:.0f} h old", "", "journalctl -u security-scan"))
    for host, h in v.get("hosts", {}).items():
        if "error" in h:
            f.append(finding(LOW, "Vulnerabilities", host, "Vulnerability scan failed", h["error"][:200]))
            continue
        fc = sum(i["fixable_critical"] for i in h["images"])
        fh = sum(i["fixable_high"] for i in h["images"])
        worst = sorted(h["images"], key=lambda i: (-i["fixable_critical"], -i["fixable_high"]))[:5]
        if fc or fh:
            f.append(finding(HIGH if fc else MED, "Vulnerabilities", host,
                             f"{fc} critical / {fh} high fixable CVEs in {sum(1 for i in h['images'] if i['fixable_critical'] or i['fixable_high'])} image(s)",
                             "; ".join(f"{i['image']} ({i['fixable_critical']}C/{i['fixable_high']}H)" for i in worst if i['fixable_critical'] or i['fixable_high']),
                             "Pull newer image tags and recreate the containers (fixed versions exist for all of these).",
                             fid=f"vulns:{host}"))
    return f


def apply_acks(findings, acks):
    out = []
    for x in findings:
        if x["id"] in acks or any(x["id"].startswith(a.rstrip("*")) for a in acks if a.endswith("*")):
            x = {**x, "sev": INFO, "detail": (x["detail"] + " [acknowledged]").strip()}
        out.append(x)
    return out


def score(findings) -> tuple[int, str]:
    s = max(0, 100 - sum(WEIGHT[x["sev"]] for x in findings))
    grade = "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 65 else "D" if s >= 50 else "F"
    return s, grade


# ----------------------------------------------------------------------------- rendering
def human_bytes(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def render(findings, changes, ctx) -> tuple[str, str, str]:
    e = html.escape
    sc, grade = score(findings)
    counts = {s: sum(1 for x in findings if x["sev"] == s) for s in SEV_ORDER}
    when = datetime.now().strftime("%a %d %b")
    shield = {"A": "🛡️", "B": "🛡️", "C": "⚠️", "D": "🚩", "F": "🚨"}[grade]
    subject = (f"{shield} Homelab security {when}: {grade} ({sc}/100) — "
               f"{counts[CRIT]} critical, {counts[HIGH]} high, {counts[MED]} medium")
    act = [x for x in findings if x["sev"] in (CRIT, HIGH, MED)]
    act.sort(key=lambda x: SEV_ORDER.index(x["sev"]))
    rest = [x for x in findings if x["sev"] in (LOW, INFO)]

    # ---- text
    t = [subject, "", f"Score {sc}/100 (grade {grade}). " + " · ".join(f"{counts[s]} {s}" for s in SEV_ORDER), ""]
    if act:
        t.append("NEEDS ATTENTION")
        for x in act:
            t.append(f"  [{x['sev'].upper()}] {x['host']}: {x['title']}")
            if x["detail"]:
                t.append(f"      {x['detail']}")
            if x["fix"]:
                t.append(f"      → {x['fix']}")
        t.append("")
    if changes:
        t.append("CHANGED SINCE YESTERDAY")
        t += [f"  - {c}" for c in changes[:40]]
        t.append("")
    t.append("LOWER PRIORITY")
    t += [f"  [{x['sev']}] {x['host']}: {x['title']} {('— ' + x['detail']) if x['detail'] else ''}" for x in rest]
    t += ["", ctx["summary_text"]]
    text = "\n".join(t)

    # ---- html
    css_td = "padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top"
    p = [f"<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;color:#1f2328;max-width:900px'>",
         f"<table style='border-collapse:collapse;margin-bottom:12px'><tr>"
         f"<td style='font-size:44px;font-weight:700;padding-right:16px;color:{COLOR[CRIT] if grade in 'DF' else COLOR[MED] if grade == 'C' else '#1a7f37'}'>{grade}</td>"
         f"<td><div style='font-size:20px;font-weight:600'>Security posture {sc}/100</div>"
         f"<div style='color:#59636e'>{' · '.join(f'{ICON[s]} {counts[s]} {s}' for s in SEV_ORDER)}</div>"
         f"<div style='color:#59636e;font-size:12px'>{e(datetime.now().strftime('%Y-%m-%d %H:%M %Z'))} · last 24 h</div></td></tr></table>"]
    if act:
        p.append("<h3 style='margin:14px 0 6px'>Needs attention</h3><table style='border-collapse:collapse;width:100%'>")
        for x in act:
            p.append(f"<tr><td style='{css_td};width:20px'>{ICON[x['sev']]}</td><td style='{css_td}'>"
                     f"<b style='color:{COLOR[x['sev']]}'>{e(x['title'])}</b> <span style='color:#59636e'>· {e(x['host'])} · {e(x['area'])}</span>"
                     + (f"<br><span style='color:#59636e'>{e(x['detail'])}</span>" if x["detail"] else "")
                     + (f"<br>→ {e(x['fix'])}" if x["fix"] else "") + "</td></tr>")
        p.append("</table>")
    else:
        p.append("<p style='color:#1a7f37'><b>Nothing needs attention.</b></p>")
    if changes:
        p.append("<h3 style='margin:16px 0 6px'>Changed since yesterday</h3><ul style='margin:0;padding-left:18px'>"
                 + "".join(f"<li>{e(c)}</li>" for c in changes[:40]) + "</ul>")
    p.append(ctx["summary_html"])
    if rest:
        p.append("<h3 style='margin:16px 0 6px'>Lower priority</h3><table style='border-collapse:collapse;width:100%'>")
        for x in rest:
            p.append(f"<tr><td style='{css_td};width:20px'>{ICON[x['sev']]}</td><td style='{css_td}'>{e(x['title'])} "
                     f"<span style='color:#59636e'>· {e(x['host'])}</span>"
                     + (f"<br><span style='color:#59636e'>{e(x['detail'])}</span>" if x["detail"] else "") + "</td></tr>")
        p.append("</table>")
    p.append("<p style='color:#59636e;font-size:12px;margin-top:20px'>Detection only: nothing is blocked automatically. "
             "Silence a known finding by adding its id to /etc/homelab-security/acknowledged.txt on pve "
             "(ids: <code>python3 /opt/homelab-security/security_report.py --dry-run --ids</code>). "
             "Source: homelab/security in the home-lab repo.</p></div>")
    return subject, text, "".join(p)


def summary_sections(hosts, truenas, sur, net, vulns) -> tuple[str, str]:
    """Context tables: what was checked, so 'nothing found' is distinguishable from 'nothing looked'."""
    e = html.escape
    td = "padding:3px 8px;border-bottom:1px solid #eee;vertical-align:top"
    rows, txt = [], ["COVERAGE"]
    # Intrusion detection
    sev_counts = {1: 0, 2: 0, 3: 0}
    for a in sur.get("alerts", []):
        sev_counts[a["sev"]] = sev_counts.get(a["sev"], 0) + a["count"]
    ids = (f"Suricata {'running' if sur.get('active') else 'NOT RUNNING'}, {sur.get('rules', 0):,} rules, {sur.get('mem_mb', 0)} MB · "
           f"alerts: {sev_counts[1]} high-sev, {sev_counts[2]} medium, {sev_counts[3]} low · {sur.get('noise', 0)} engine events ignored")
    rows.append(("Network IDS (pve: VM bridge + Tailscale)", ids))
    txt.append("  " + ids)
    logins = []
    for h, d in list(hosts.items()):
        if "error" in d:
            logins.append(f"{h}: audit failed")
            continue
        a = d["auth"]
        acc = sum(a["accepted"].values())
        srcs = sorted({k.split('|')[1] for k in a["accepted"]})
        logins.append(f"{h}: {acc} logins from {', '.join(srcs[:4]) or '—'}; {sum(a['failed_by_ip'].values())} failed; {a['sudo_cmds']} sudo")
    tl = truenas.get("logins", {}) if "error" not in truenas else {}
    logins.append(f"truenas: {sum((tl.get('ok') or {}).values())} UI/API logins, {sum((tl.get('failed') or {}).values())} failed")
    rows.append(("Logins (24 h)", "<br>".join(e(x) for x in logins)))
    txt += ["  " + x for x in logins]
    # Traffic
    ext = sorted(sur.get("ext_bytes", {}).items(), key=lambda kv: -kv[1])[:6]
    traffic = "Internet traffic by host: " + (", ".join(f"{name(h)} {human_bytes(b)}" for h, b in ext) or "—")
    dests = "Top destinations: " + (", ".join(f"{d['ip']} ({human_bytes(d['bytes'])})" for d in sur.get("top_dests", [])[:5]) or "—")
    inbound = "Unsolicited inbound from internet: " + (", ".join(f"{k} ×{v['count']}" for k, v in list(sur.get("inbound", {}).items())[:6]) or "none seen")
    rows.append(("Traffic (24 h)", "<br>".join(e(x) for x in (traffic, dests, inbound))))
    txt += ["  " + traffic, "  " + dests, "  " + inbound]
    # Exposure
    exp = (f"{len(net.get('devices', {}))} LAN devices, {sum(len(p) for p in net.get('ports', {}).values())} open ports on "
           f"{len(net.get('ports', {}))} hosts, {len(net.get('tailnet', {}))} tailnet devices")
    rows.append(("Network inventory", e(exp)))
    txt.append("  " + exp)
    # Vulns
    vl = []
    for h, d in (vulns or {}).get("hosts", {}).items():
        if "error" in d:
            vl.append(f"{h}: scan failed")
            continue
        c = sum(i["critical"] for i in d["images"])
        hi = sum(i["high"] for i in d["images"])
        vl.append(f"{h}: {len(d['images'])} images, {c} critical / {hi} high CVEs ({sum(i['fixable_critical'] for i in d['images'])}/{sum(i['fixable_high'] for i in d['images'])} fixable)")
    for h, d in hosts.items():
        if "error" not in d:
            vl.append(f"{h} OS: {d['updates']['security']} security updates pending")
    vl.append("aeacus-dl containers: not scanned (no docker access for the audit user; VM has no guest agent)")
    rows.append(("Vulnerabilities", "<br>".join(e(x) for x in vl)))
    txt += ["  " + x for x in vl]
    h = ("<h3 style='margin:16px 0 6px'>Coverage and activity</h3><table style='border-collapse:collapse;width:100%'>"
         + "".join(f"<tr><td style='{td};white-space:nowrap;font-weight:600'>{e(k)}</td><td style='{td};color:#3d444d'>{v if '<br>' in v else e(v)}</td></tr>" for k, v in rows)
         + "</table>")
    return "\n".join(txt), h


def send(subject, text, body_html):
    if not MAIL_TO:
        raise SystemExit("REPORT_TO is not set: create /etc/homelab-security/report.env with REPORT_TO=<address>")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Homelab security <{MAIL_FROM}>", MAIL_TO, subject
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
        s.send_message(msg)


# ----------------------------------------------------------------------------- modes
def update_baseline(base, hosts, truenas, net):
    base = dict(base)
    base.setdefault("integrity", {})
    base.setdefault("listening", {})
    for h, d in hosts.items():
        if "error" not in d:
            base["integrity"][h] = d["integrity"]
            base["listening"][h] = d["listening"]
    if "error" not in truenas:
        if isinstance(truenas.get("listening"), list):
            base["listening"]["truenas"] = truenas["listening"]
        srcs = set(base.get("truenas_login_sources", []))
        srcs |= {k.split("|")[1] for k in (truenas.get("logins", {}).get("ok") or {})}
        base["truenas_login_sources"] = sorted(srcs)
    if net.get("devices"):
        base["lan_devices"] = {**base.get("lan_devices", {}), **net["devices"]}
    if net.get("ports"):
        base["lan_ports"] = {**base.get("lan_ports", {}), **net["ports"]}
    if net.get("tailnet"):
        base["tailnet"] = {**base.get("tailnet", {}), **net["tailnet"]}
    return base


def run_report(dry: bool, show_ids: bool):
    base = load_json(STATE / "baseline.json", {})
    history = [json.loads(l) for l in open(STATE / "traffic_history.jsonl")] if (STATE / "traffic_history.jsonl").exists() else []
    hosts = collect_hosts()
    truenas = collect_truenas()
    sur = collect_suricata()
    net = collect_network()
    vulns = load_json(STATE / "vulns.json", {})

    findings, changes = [], []
    for h, d in hosts.items():
        f, c = host_findings(h, d, base)
        findings += f
        changes += c
    f, c = truenas_findings(truenas, base)
    findings += f
    changes += c
    findings += suricata_findings(sur, history)
    f, c = network_findings(net, base)
    findings += f
    changes += c
    findings += vuln_findings(vulns)
    if not base:
        findings.append(finding(INFO, "Integrity", "all", "Baselines recorded today",
                                "File hashes, listening ports, LAN devices and tailnet devices are compared against today's state from tomorrow on."))
    findings = apply_acks(findings, load_acks())

    txt, htm = summary_sections(hosts, truenas, sur, net, vulns)
    subject, text, body = render(findings, changes, {"summary_text": txt, "summary_html": htm})
    if show_ids:
        for x in findings:
            print(f"{x['sev']:8} {x['id']}")
        return
    if dry:
        print(text)
        return
    send(subject, text, body)
    save_json(STATE / "baseline.json", update_baseline(base, hosts, truenas, net))
    with open(STATE / "traffic_history.jsonl", "a") as fh:
        fh.write(json.dumps(sur.get("ext_bytes", {})) + "\n")
    print("sent:", subject)


def summarize_trivy(image: str, report: dict) -> dict:
    out = {"image": image, "critical": 0, "high": 0, "fixable_critical": 0, "fixable_high": 0, "top": []}
    for res in report.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            sev = v.get("Severity")
            fixed = bool(v.get("FixedVersion"))
            if sev == "CRITICAL":
                out["critical"] += 1
                out["fixable_critical"] += fixed
            elif sev == "HIGH":
                out["high"] += 1
                out["fixable_high"] += fixed
            if sev == "CRITICAL" and fixed and len(out["top"]) < 5:
                out["top"].append(f"{v['VulnerabilityID']} {v['PkgName']} {v.get('InstalledVersion')}→{v['FixedVersion']}")
    return out


def run_scan():
    token = TOKEN_FILE.read_text().strip()
    trivy = (f"docker run --rm -e TRIVY_TOKEN={token} -v /var/run/docker.sock:/var/run/docker.sock:ro {TRIVY_IMAGE} "
             f"image --server {TRIVY_SERVER} --scanners vuln --severity HIGH,CRITICAL --format json --quiet --timeout 15m ")
    result = {"started": time.time(), "hosts": {}}
    for host, target in SCAN_HOSTS.items():
        images, errors = [], []
        try:
            if target:
                rc, out, err = sh(["ssh", "-o", "BatchMode=yes", target, "docker ps --format '{{.Image}}' | sort -u"], 60)
            else:
                d = guest_exec("import subprocess;print(__import__('json').dumps(sorted(set(subprocess.run(['docker','ps','--format','{{.Image}}'],capture_output=True,text=True).stdout.split()))))", 60)
                out = "\n".join(d)
            for img in out.split():
                if img.startswith("aquasec/trivy"):
                    continue
                cmd = trivy + img
                try:
                    if target:
                        rc, o, err = sh(["ssh", "-o", "BatchMode=yes", target, f"nice -n 15 {cmd}"], 1200)
                        rep = json.loads(o) if rc == 0 else None
                    else:
                        r = subprocess.run(["qm", "guest", "exec", TRUENAS_VMID, "--timeout", "1100", "--", "sh", "-c", cmd],
                                           capture_output=True, text=True, timeout=1200)
                        d = json.loads(r.stdout)
                        rep = json.loads(d["out-data"]) if d.get("exitcode") == 0 else None
                        err = (d.get("err-data") or "")[-200:]
                    if rep is None:
                        errors.append(f"{img}: {err.strip()[-150:]}")
                    else:
                        images.append(summarize_trivy(img, rep))
                except Exception as ex:  # noqa: BLE001
                    errors.append(f"{img}: {type(ex).__name__}: {ex}")
            result["hosts"][host] = {"images": images, "errors": errors}
        except Exception as ex:  # noqa: BLE001
            result["hosts"][host] = {"error": f"{type(ex).__name__}: {ex}"}
        print(host, len(images), "images scanned,", len(errors), "errors", flush=True)
    result["finished"] = time.time()
    save_json(STATE / "vulns.json", result)


def run_urgent(dry: bool):
    """Hourly: high-risk IDS alerts get an email right away (once per rule per 24 h)."""
    sent = load_json(STATE / "urgent_sent.json", {})
    acks = load_acks()
    since = datetime.now(timezone.utc) - timedelta(minutes=65)
    s = analyze_suricata(eve_lines(since))
    hits = [x for x in suricata_findings({**s, "active": True}, []) if x["sev"] in (CRIT, HIGH)
            and x["id"] not in acks and time.time() - sent.get(x["id"], 0) > 86400]
    if not hits:
        print("urgent: nothing new")
        return
    subject = f"🚨 Homelab security alert: {hits[0]['title'][:90]}" + (f" (+{len(hits) - 1})" if len(hits) > 1 else "")
    text = "\n\n".join(f"{x['title']}\n{x['detail']}\n→ {x['fix']}" for x in hits)
    body = "<div style='font-family:sans-serif;font-size:14px'>" + "".join(
        f"<p><b style='color:{COLOR[x['sev']]}'>{html.escape(x['title'])}</b><br>{html.escape(x['detail'])}<br>→ {html.escape(x['fix'])}</p>"
        for x in hits) + "<p style='color:#59636e;font-size:12px'>Sent at most once per rule per 24 h. Full picture in tonight's 19:00 report.</p></div>"
    if dry:
        print(subject, "\n", text)
        return
    send(subject, text, body)
    for x in hits:
        sent[x["id"]] = time.time()
    save_json(STATE / "urgent_sent.json", sent)
    print("sent:", subject)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--urgent", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ids", action="store_true", help="list finding ids (for acknowledged.txt) and exit")
    a = ap.parse_args()
    if a.scan:
        run_scan()
    elif a.urgent:
        run_urgent(a.dry_run)
    else:
        run_report(a.dry_run, a.ids)


if __name__ == "__main__":
    sys.exit(main())
