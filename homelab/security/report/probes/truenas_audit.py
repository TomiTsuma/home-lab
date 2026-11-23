"""Security audit of TrueNAS, run as root through `qm guest exec 102 -- python3 -c <this>` (no stored creds).
Read-only. Prints one JSON object.

  logins  - middleware audit log, 24 h: failed and successful logins by user, source address and method
  ssh     - SSH service running/enabled, password auth, root login
  users   - non-builtin users: SSH password allowed, sudo, 2FA
  shares  - SMB guest access, NFS exports open to everyone
  listen  - listening sockets
  images  - container images of running apps (fed to the vulnerability scanner)
  alerts  - active alerts (count by level)
"""
import json
import subprocess
import time


def m(method, *args):
    out = subprocess.run(["midclt", "call", method, *[json.dumps(a) for a in args]],
                         capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


def safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def logins():
    since = int(time.time()) - 86400
    rows = m("audit.query", {"services": ["MIDDLEWARE"],
                             "query-filters": [["event", "=", "AUTHENTICATION"], ["message_timestamp", ">", since]],
                             "query-options": {"limit": 5000}}) or []
    failed, ok = {}, {}
    for r in rows:
        cred = ((r.get("event_data") or {}).get("credentials") or {}).get("credentials", "?")
        if cred == "UNIX_SOCKET":          # local midclt calls (including this audit): not a login
            continue
        k = f"{r.get('username') or '?'}|{r.get('address') or '?'}|{cred}"
        d = ok if r.get("success") else failed
        d[k] = d.get(k, 0) + 1
    return {"failed": failed, "ok": ok}


def ssh():
    cfg = m("ssh.config") or {}
    svc = (m("service.query", [["service", "=", "ssh"]]) or [{}])[0]
    return {"running": svc.get("state") == "RUNNING", "enabled_at_boot": svc.get("enable"),
            "password_auth": cfg.get("passwordauth"), "root_login": cfg.get("rootlogin", False)}


def users():
    out = []
    tfa = (m("auth.twofactor.config") or {}).get("enabled")
    for u in m("user.query", [["builtin", "=", False]]) or []:
        out.append({"name": u["username"], "locked": u.get("locked"), "password_disabled": u.get("password_disabled"),
                    "ssh_password": u.get("ssh_password_enabled"), "sudo": bool(u.get("sudo_commands") or u.get("sudo_commands_nopasswd")),
                    "sudo_nopasswd": bool(u.get("sudo_commands_nopasswd")), "keys": bool(u.get("sshpubkey")),
                    "admin": "builtin_administrators" in [g.get("group") for g in u.get("groups_details", [])] if u.get("groups_details") else None})
    return {"twofactor_enabled": tfa, "users": out}


def shares():
    smb = [{"name": s["name"], "path": s["path"]} for s in (m("sharing.smb.query") or []) if s.get("guestok") and s.get("enabled")]
    nfs = [{"path": s.get("path") or s.get("paths"), "maproot": s.get("maproot_user")} for s in (m("sharing.nfs.query") or [])
           if s.get("enabled") and not s.get("networks") and not s.get("hosts")]
    return {"smb_guest": smb, "nfs_open_to_all": nfs}


def listen():
    r = subprocess.run(["ss", "-Hlntu"], capture_output=True, text=True)
    socks = set()
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) >= 5 and not f[4].startswith(("127.", "[::1]", "::1")):
            socks.add(f"{f[0]} {f[4]}")
    return sorted(socks)


def images():
    r = subprocess.run(["docker", "ps", "--format", "{{.Image}}"], capture_output=True, text=True)
    return sorted(set(r.stdout.split()))


def alerts():
    out = {}
    for a in m("alert.list") or []:
        if not a.get("dismissed"):
            out[a.get("level", "?")] = out.get(a.get("level", "?"), 0) + 1
    return out


print(json.dumps({"logins": safe(logins), "ssh": safe(ssh), "users": safe(users), "shares": safe(shares),
                  "listening": safe(listen), "images": safe(images), "alerts": safe(alerts)}))
