"""Unit tests for the security-relevant pure logic of server.py (no network, no root needed).

Run inside the image:  docker compose run --rm --no-deps mcp-tools python -m pytest -q test_server.py
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import server


# ---- path confinement -------------------------------------------------------
def test_confine_allows_nested(tmp_path):
    assert server.confine(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_confine_rejects_dotdot(tmp_path):
    with pytest.raises(ValueError):
        server.confine(tmp_path, "../etc/passwd")


def test_confine_treats_absolute_as_workspace_relative(tmp_path):
    assert server.confine(tmp_path, "/out.pdf") == (tmp_path / "out.pdf").resolve()


def test_confine_rejects_symlink_escape(tmp_path):
    (tmp_path / "evil").symlink_to("/etc")
    with pytest.raises(ValueError):
        server.confine(tmp_path, "evil/passwd")


# ---- per-user sandbox UID ---------------------------------------------------
def test_sandbox_uid_stable_and_in_range():
    a = server.sandbox_uid("user-a")
    assert a == server.sandbox_uid("user-a")
    assert server.SANDBOX_UID_BASE <= a < server.SANDBOX_UID_BASE + 30000
    assert a != server.sandbox_uid("user-b")


def test_safe_user_key_strips_path_chars():
    assert server._safe_user_key("../x/y") == "xy"
    with pytest.raises(server.AuthError):
        server._safe_user_key("../")


# ---- S3 object keys ---------------------------------------------------------
def test_s3_key_layout_and_sanitising():
    k = server.s3_key("u-1", "../../my report (final).pdf", datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert k.startswith("u-1/2026/11/21/")
    assert k.endswith("-my_report__final_.pdf")
    assert ".." not in k


# ---- scrape.do normalisation ------------------------------------------------
def test_scrapedo_goto_links_nulled():
    payload = {"organic_results": [
        {"title": "A", "link": "https://www.google.com/goto?url=XYZ", "source": "Site A", "snippet": "s"},
        {"title": "B", "link": "https://example.com/b", "snippet": "t"},
    ]}
    res = server.normalize_scrapedo_results(payload, 10)
    assert res[0]["link"] is None and res[0]["source"] == "Site A"
    assert res[1]["link"] == "https://example.com/b"
    assert len(server.normalize_scrapedo_results(payload, 1)) == 1


def test_html_to_text_drops_scripts():
    title, text = server.html_to_text("<html><title>T</title><script>x()</script><p>Hello</p></html>")
    assert title == "T" and "Hello" in text and "x()" not in text


# ---- auth middleware --------------------------------------------------------
def _call(mw, headers, path="/mcp"):
    sent, seen = [], {}

    async def app(scope, receive, send):
        seen["user"] = scope["state"]["owui_user"]
        await send({"type": "http.response.start", "status": 200, "headers": []})

    mw.app = app

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "path": path, "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(mw(scope, None, send))
    return sent[0]["status"], seen.get("user")


def test_auth_rejects_missing_and_invalid_tokens():
    calls = []

    async def verifier(tok):
        calls.append(tok)
        return {"id": "u1", "role": "admin"} if tok == "good" else None

    mw = server.OpenWebUIAuth(None, verifier=verifier)
    assert _call(mw, {})[0] == 401
    assert _call(mw, {"authorization": "Bearer bad"})[0] == 401
    status, user = _call(mw, {"authorization": "Bearer good"})
    assert status == 200 and user["id"] == "u1"
    _call(mw, {"authorization": "Bearer good"})
    assert calls.count("good") == 1  # cached


def test_healthz_needs_no_auth():
    mw = server.OpenWebUIAuth(None, verifier=lambda t: None)
    assert _call(mw, {}, path="/healthz")[0] == 200


def test_exec_role_gate():
    server._require_exec({"role": "admin"})
    with pytest.raises(server.AuthError):
        server._require_exec({"role": "user"})


# ---- sandboxed execution (as current user when not root) --------------------
def test_run_as_timeout_and_clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET", "leak")
    res = server._run_as(["/bin/bash", "-c", "echo ${SUPER_SECRET:-clean}; sleep 5"], tmp_path, 0, timeout=1)
    assert res["timed_out"] is True
    assert "clean" in res["stdout"] and "leak" not in res["stdout"]
