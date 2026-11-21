"""Homelab MCP tool server for Open WebUI.

Execution model adapted from CoreOutlineV2/dss-agent/tools/mcp_server.py (run_command /
python_execute / workspace files / upload-to-object-storage-and-return-presigned-URL),
WITHOUT the financial-analysis tools, and hardened for a network transport:

* dss-agent's server is safe only because it speaks stdio (`mcp.run()`): the one process
  that spawned it is the only possible caller. This server speaks Streamable HTTP, so the
  same guarantee is rebuilt explicitly:
    1. Network: the container publishes NO host port; only containers on the compose
       network (i.e. open-webui) can reach it.
    2. Identity: Open WebUI is configured with auth_type="session", so every call carries the
       logged-in Open WebUI user's own JWT. Each request's token is verified against Open
       WebUI's `/api/v1/auths/` — no valid Open WebUI session, no access (401).
    3. Authorisation: execution tools are limited to roles in EXEC_ALLOWED_ROLES (admin).
* Commands run as a per-user unprivileged UID (not the server's UID), in a per-user
  workspace (chmod 700), with a scrubbed environment and a timeout. So user code cannot read
  this server's secrets (env of PID 1 is not readable by another UID) or other users' files.
  dss-agent ran commands with the server's full environment and no timeout.
* Generated files are published to s3.ereuna.org bucket `openwebui-files` (like dss-agent's
  upload_file_to_minio) and returned as a presigned download URL + ready-to-render markdown.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from mcp.types import ImageContent, TextContent

log = logging.getLogger("mcp-tools")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------------
# Configuration (all from env; secrets only ever live in this process's env)
# ---------------------------------------------------------------------------
OPENWEBUI_URL = os.getenv("OPENWEBUI_URL", "http://open-webui:8080").rstrip("/")
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
EXEC_ALLOWED_ROLES = {r.strip() for r in os.getenv("EXEC_ALLOWED_ROLES", "admin").split(",") if r.strip()}
SANDBOX_UID_BASE = int(os.getenv("SANDBOX_UID_BASE", "20000"))
CMD_TIMEOUT_DEFAULT = int(os.getenv("CMD_TIMEOUT_DEFAULT", "120"))
CMD_TIMEOUT_MAX = int(os.getenv("CMD_TIMEOUT_MAX", "900"))
# Kept small on purpose: every tool result is re-sent to the model on each later step of a
# tool loop, so big outputs exhaust a local model's context and cut its answer off.
OUTPUT_LIMIT = int(os.getenv("OUTPUT_LIMIT", "12000"))
AUTH_CACHE_TTL = int(os.getenv("AUTH_CACHE_TTL", "60"))

LLAMA_CLOUD_API_KEY = os.getenv("LLAMA_CLOUD_API_KEY", "")
LLAMA_CLOUD_BASE = os.getenv("LLAMA_CLOUD_BASE", "https://api.cloud.llamaindex.ai").rstrip("/")
SCRAPEDO_TOKEN = os.getenv("SCRAPEDO_TOKEN", "")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "s3.ereuna.org")
S3_SECURE = os.getenv("S3_SECURE", "true").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "openwebui-files")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
PRESIGN_DEFAULT_HOURS = int(os.getenv("PRESIGN_DEFAULT_HOURS", "24"))
PRESIGN_MAX_HOURS = 168  # S3 SigV4 hard limit: 7 days
INLINE_IMAGE_MAX_BYTES = 4 * 1024 * 1024

SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"

# python:3.11-slim ships no /etc/mime.types, so Office formats would be served as
# application/octet-stream (browser can't preview / picks wrong app). Register them.
for _ext, _type in {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".webp": "image/webp",
}.items():
    mimetypes.add_type(_type, _ext)

mcp = FastMCP(
    "homelab-tools",
    instructions=(
        "Tools run in a sandboxed Linux workspace private to the current user. "
        "Typical flows: (1) document extraction: list_uploaded_files -> parse_document(file_id); "
        "(2) generate a file with run_python/run_command into the workspace -> publish_file -> "
        "show the returned `markdown` to the user verbatim so they can download/see it."
    ),
)


# ---------------------------------------------------------------------------
# Identity: who is calling (set by OpenWebUIAuth middleware)
# ---------------------------------------------------------------------------
class AuthError(Exception):
    pass


def _current_user() -> dict:
    try:
        user = get_http_request().state.owui_user
    except Exception as exc:  # no HTTP request in context => not called via Open WebUI
        raise AuthError("no authenticated Open WebUI user on this request") from exc
    if not user:
        raise AuthError("no authenticated Open WebUI user on this request")
    return user


def _require_exec(user: dict) -> None:
    if user.get("role") not in EXEC_ALLOWED_ROLES:
        raise AuthError(f"role '{user.get('role')}' may not execute code (allowed: {sorted(EXEC_ALLOWED_ROLES)})")


def _safe_user_key(user_id: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_-]", "", user_id)
    if not key:
        raise AuthError("unusable user id")
    return key


def sandbox_uid(user_id: str) -> int:
    """Deterministic, stable unprivileged UID per Open WebUI user (range BASE..BASE+30000)."""
    h = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    return SANDBOX_UID_BASE + (h % 30000)


def _workspace(user: dict) -> tuple[Path, int]:
    uid = sandbox_uid(user["id"])
    ws = WORKSPACE_ROOT / _safe_user_key(user["id"])
    if not ws.exists():
        ws.mkdir(parents=True)
    _chown(ws, uid)
    os.chmod(ws, 0o700)
    return ws, uid


def _chown(path: Path, uid: int) -> None:
    try:
        os.chown(path, uid, uid)
    except PermissionError:  # running unprivileged (e.g. unit tests): best effort
        pass


def confine(ws: Path, rel: str) -> Path:
    """Resolve `rel` inside workspace `ws`; refuse anything (incl. symlinks) that escapes it."""
    candidate = Path(rel)
    if candidate.is_absolute():
        # Treat absolute paths as workspace-relative ("/out.pdf" -> ws/out.pdf).
        candidate = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path(".")
    target = (ws / candidate).resolve()
    root = ws.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path '{rel}' escapes the workspace")
    return target


def _truncate(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


# ---------------------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------------------
def _run_as(argv: list[str], ws: Path, uid: int, timeout: int, stdin: Optional[str] = None) -> dict:
    env = {
        "PATH": SANDBOX_PATH,
        "HOME": str(ws),
        "LANG": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(ws / ".cache" / "matplotlib"),
        "TMPDIR": str(ws / ".tmp"),
    }
    (ws / ".tmp").mkdir(exist_ok=True)
    _chown(ws / ".tmp", uid)
    kwargs: dict[str, Any] = {}
    if os.geteuid() == 0:
        kwargs.update(user=uid, group=uid, extra_groups=[])
    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=ws,
        env=env,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # own process group => timeout kills children too
        **kwargs,
    )
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin.encode() if stdin is not None else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - start, 2),
        "stdout": _truncate(out.decode("utf-8", "replace")),
        "stderr": _truncate(err.decode("utf-8", "replace")),
    }


def _clamp_timeout(t: Optional[int]) -> int:
    return max(1, min(int(t or CMD_TIMEOUT_DEFAULT), CMD_TIMEOUT_MAX))


@mcp.tool(description=(
    "Run a bash command in your private sandbox workspace (cwd = workspace). Has internet access; "
    "python3, pip (--user), curl, git, pandas, matplotlib, openpyxl, python-docx, python-pptx, "
    "reportlab, pypdf are available. Returns stdout, stderr, exit_code."
))
async def run_command(command: str, timeout_seconds: int = CMD_TIMEOUT_DEFAULT) -> dict:
    user = _current_user()
    _require_exec(user)
    ws, uid = _workspace(user)
    log.info("run_command user=%s cmd=%r", user.get("email"), command[:200])
    return await asyncio.to_thread(_run_as, ["/bin/bash", "-c", command], ws, uid, _clamp_timeout(timeout_seconds))


@mcp.tool(description=(
    "Run Python 3.11 code in your private sandbox workspace (cwd = workspace). Save any output "
    "files (charts, .docx, .xlsx, .pptx, .pdf) into the workspace, then call publish_file to give "
    "the user a download link. Returns stdout, stderr, exit_code."
))
async def run_python(code: str, timeout_seconds: int = CMD_TIMEOUT_DEFAULT) -> dict:
    user = _current_user()
    _require_exec(user)
    ws, uid = _workspace(user)
    log.info("run_python user=%s bytes=%d", user.get("email"), len(code))
    # Code goes in via stdin: no temp file outside the workspace, nothing left behind.
    return await asyncio.to_thread(_run_as, ["python3", "-"], ws, uid, _clamp_timeout(timeout_seconds), code)


# ---------------------------------------------------------------------------
# Workspace files
# ---------------------------------------------------------------------------
@mcp.tool(description="List files in your sandbox workspace (optionally a subdirectory).")
async def list_workspace(subdir: str = ".") -> dict:
    user = _current_user()
    ws, _ = _workspace(user)
    base = confine(ws, subdir)
    if not base.is_dir():
        return {"error": f"not a directory: {subdir}"}
    entries = []
    for p in sorted(base.rglob("*")):
        if any(part.startswith(".") for part in p.relative_to(ws).parts):
            continue
        if p.is_file():
            entries.append({"path": str(p.relative_to(ws)), "size_bytes": p.stat().st_size})
        if len(entries) >= 500:
            break
    return {"workspace": "/", "files": entries}


@mcp.tool(description="Read a text file from your sandbox workspace.")
async def read_file(path: str, max_chars: int = 12000) -> dict:
    user = _current_user()
    ws, _ = _workspace(user)
    target = confine(ws, path)
    if not target.is_file():
        return {"error": f"no such file: {path}"}
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "content": _truncate(text, max(1, min(max_chars, 200000)))}


@mcp.tool(description="Write (or append) text content to a file in your sandbox workspace.")
async def write_file(path: str, content: str, append: bool = False) -> dict:
    user = _current_user()
    _require_exec(user)
    ws, uid = _workspace(user)
    target = confine(ws, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    for d in [target.parent, *target.parent.parents]:
        if d == ws or ws not in d.parents:
            break
        _chown(d, uid)
    with open(target, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    _chown(target, uid)
    return {"path": str(target.relative_to(ws)), "size_bytes": target.stat().st_size}


# ---------------------------------------------------------------------------
# Open WebUI uploads (files the user attached in chat)
# ---------------------------------------------------------------------------
def _user_token() -> str:
    return get_http_request().state.owui_token


@mcp.tool(description=(
    "List files the user has uploaded/attached in Open WebUI (newest first). Use the returned id "
    "with parse_document or import_uploaded_file."
))
async def list_uploaded_files(limit: int = 20) -> dict:
    user = _current_user()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{OPENWEBUI_URL}/api/v1/files/", params={"content": "false"},
                        headers={"Authorization": f"Bearer {_user_token()}"})
        r.raise_for_status()
    files = [f for f in r.json() if f.get("user_id") == user["id"]]
    files.sort(key=lambda f: f.get("created_at") or 0, reverse=True)
    out = []
    for f in files[: max(1, min(limit, 100))]:
        meta = f.get("meta") or {}
        out.append({
            "id": f["id"],
            "filename": f.get("filename") or meta.get("name"),
            "content_type": meta.get("content_type"),
            "size_bytes": meta.get("size"),
            "uploaded_at": datetime.fromtimestamp(f.get("created_at") or 0, timezone.utc).isoformat(),
        })
    return {"files": out}


async def _download_upload(file_id: str, ws: Path, uid: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", file_id):
        raise ValueError("invalid file id")
    headers = {"Authorization": f"Bearer {_user_token()}"}
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        meta = await c.get(f"{OPENWEBUI_URL}/api/v1/files/{file_id}", headers=headers)
        meta.raise_for_status()
        name = Path(meta.json().get("filename") or file_id).name
        r = await c.get(f"{OPENWEBUI_URL}/api/v1/files/{file_id}/content", headers=headers)
        r.raise_for_status()
    dest_dir = ws / "uploads"
    dest_dir.mkdir(exist_ok=True)
    _chown(dest_dir, uid)
    dest = dest_dir / name
    dest.write_bytes(r.content)
    _chown(dest, uid)
    return dest


@mcp.tool(description="Copy an Open WebUI upload (by file id) into your workspace under uploads/.")
async def import_uploaded_file(file_id: str) -> dict:
    user = _current_user()
    ws, uid = _workspace(user)
    dest = await _download_upload(file_id, ws, uid)
    return {"path": str(dest.relative_to(ws)), "size_bytes": dest.stat().st_size}


# ---------------------------------------------------------------------------
# LlamaParse document extraction
# ---------------------------------------------------------------------------
async def llamaparse(path: Path, result_type: str = "markdown", poll_timeout: int = 600) -> str:
    if not LLAMA_CLOUD_API_KEY:
        raise RuntimeError("LLAMA_CLOUD_API_KEY is not configured")
    headers = {"Authorization": f"Bearer {LLAMA_CLOUD_API_KEY}"}
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    async with httpx.AsyncClient(base_url=LLAMA_CLOUD_BASE, timeout=120, headers=headers) as c:
        with open(path, "rb") as fh:
            r = await c.post("/api/v1/parsing/upload", files={"file": (path.name, fh, ctype)})
        r.raise_for_status()
        job_id = r.json()["id"]
        deadline = time.monotonic() + poll_timeout
        delay = 2.0
        while True:
            s = (await c.get(f"/api/v1/parsing/job/{job_id}")).json()
            status = s.get("status")
            if status == "SUCCESS":
                break
            if status in ("ERROR", "CANCELED", "CANCELLED"):
                raise RuntimeError(f"LlamaParse job {job_id} {status}: {s.get('error_message') or s}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"LlamaParse job {job_id} still {status} after {poll_timeout}s")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10)
        kind = "markdown" if result_type == "markdown" else "text"
        res = await c.get(f"/api/v1/parsing/job/{job_id}/result/{kind}")
        res.raise_for_status()
        return res.json().get(kind, "")


@mcp.tool(description=(
    "Extract text/tables from a PDF, image (png/jpg/...), Word, PowerPoint or other document using "
    "LlamaParse (OCR + layout-aware). `source` = an Open WebUI file id (from list_uploaded_files), "
    "a path in your workspace, or an http(s) URL. Saves the FULL result to parsed/<name>.md and returns "
    "the first max_chars characters; use read_file or run_python on the saved file for the rest."
))
async def parse_document(source: str, result_type: str = "markdown", max_chars: int = 16000) -> dict:
    user = _current_user()
    ws, uid = _workspace(user)
    if source.startswith(("http://", "https://")):
        dest_dir = ws / "downloads"
        dest_dir.mkdir(exist_ok=True)
        _chown(dest_dir, uid)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(source)
            r.raise_for_status()
        name = Path(source.split("?")[0]).name or "download"
        path = dest_dir / name
        path.write_bytes(r.content)
        _chown(path, uid)
    elif (ws / source).exists() or "/" in source or "." in source:
        path = confine(ws, source)
        if not path.is_file():
            return {"error": f"no such file in workspace: {source}"}
    else:
        path = await _download_upload(source, ws, uid)
    log.info("parse_document user=%s file=%s", user.get("email"), path.name)
    text = await llamaparse(path, result_type)
    out_dir = ws / "parsed"
    out_dir.mkdir(exist_ok=True)
    _chown(out_dir, uid)
    out = out_dir / (path.stem + (".md" if result_type == "markdown" else ".txt"))
    out.write_text(text, encoding="utf-8")
    _chown(out, uid)
    return {
        "source_file": str(path.relative_to(ws)),
        "saved_to": str(out.relative_to(ws)),
        "chars": len(text),
        "content": _truncate(text, max(1, min(max_chars, 200000))),
    }


# ---------------------------------------------------------------------------
# Publishing to s3.ereuna.org
# ---------------------------------------------------------------------------
def _s3():
    from minio import Minio  # imported lazily so unit tests don't need it

    if not (S3_ACCESS_KEY and S3_SECRET_KEY):
        raise RuntimeError("S3 credentials are not configured")
    # region is set so presigning never makes a network round-trip for bucket location.
    return Minio(S3_ENDPOINT, access_key=S3_ACCESS_KEY, secret_key=S3_SECRET_KEY,
                 secure=S3_SECURE, region=S3_REGION)


def s3_key(user_id: str, filename: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "file"
    return f"{_safe_user_key(user_id)}/{now:%Y/%m/%d}/{uuid.uuid4().hex[:8]}-{safe}"


@mcp.tool(description=(
    "Publish a file from your workspace to s3.ereuna.org (bucket openwebui-files) and get a "
    "time-limited download URL (default 24h, max 168h). Show the returned `markdown` to the user "
    "verbatim: images render inline, other files appear as a download link."
))
async def publish_file(path: str, expires_hours: int = PRESIGN_DEFAULT_HOURS):
    user = _current_user()
    ws, _ = _workspace(user)
    target = confine(ws, path)
    if not target.is_file():
        return {"error": f"no such file in workspace: {path}"}
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    key = s3_key(user["id"], target.name)
    hours = max(1, min(int(expires_hours or PRESIGN_DEFAULT_HOURS), PRESIGN_MAX_HOURS))
    client = _s3()
    await asyncio.to_thread(client.fput_object, S3_BUCKET, key, str(target), content_type=ctype)
    url = await asyncio.to_thread(
        client.presigned_get_object, S3_BUCKET, key, expires=timedelta(hours=hours),
        response_headers={"response-content-disposition": f"inline; filename=\"{quote(target.name)}\""},
    )
    is_image = ctype.startswith("image/")
    markdown = f"![{target.name}]({url})" if is_image else f"[Download {target.name}]({url})"
    log.info("publish_file user=%s key=%s", user.get("email"), key)
    result = {
        "status": "success",
        "url": url,
        "markdown": markdown,
        "object_key": f"{S3_BUCKET}/{key}",
        "expires_in_hours": hours,
        "content_type": ctype,
        "size_bytes": target.stat().st_size,
    }
    if is_image and target.stat().st_size <= INLINE_IMAGE_MAX_BYTES:
        import base64

        data = base64.b64encode(target.read_bytes()).decode()
        return [TextContent(type="text", text=json.dumps(result)),
                ImageContent(type="image", data=data, mimeType=ctype)]
    return result


# ---------------------------------------------------------------------------
# scrape.do: alternative search + robust page fetch (JS rendering, anti-bot)
# ---------------------------------------------------------------------------
def normalize_scrapedo_results(payload: dict, limit: int) -> list[dict]:
    """Keep organic results; Google 'goto' redirect links are opaque (don't resolve) -> link=None."""
    out = []
    for r in payload.get("organic_results") or []:
        link = r.get("link") or ""
        direct = bool(link) and "google.com/goto" not in link and "google.com/url" not in link
        out.append({
            "title": r.get("title"),
            "link": link if direct else None,
            "source": r.get("source") or r.get("displayed_link"),
            "snippet": r.get("snippet"),
        })
        if len(out) >= limit:
            break
    return out


@mcp.tool(description=(
    "Alternative Google web search via scrape.do (use when the built-in web search is not enough). "
    "Returns title, link, snippet; some results have link=null (Google redirect). Prefer 1-2 searches, "
    "then answer — each extra call uses up context."
))
async def web_search_scrapedo(query: str, num_results: int = 5, country: str = "us") -> dict:
    _current_user()
    if not SCRAPEDO_TOKEN:
        return {"error": "SCRAPEDO_TOKEN is not configured"}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get("https://api.scrape.do/plugin/google/search",
                        params={"token": SCRAPEDO_TOKEN, "q": query, "gl": country, "hl": "en"})
        r.raise_for_status()
        payload = r.json()
    ai = (payload.get("ai_overview") or {}).get("text_blocks") or []
    return {
        "query": query,
        "results": normalize_scrapedo_results(payload, max(1, min(num_results, 20))),
        "ai_overview": " ".join(b.get("snippet", "") for b in ai if isinstance(b, dict))[:2000] or None,
    }


def html_to_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()
    text = "\n".join(line.strip() for line in text.splitlines())
    return title, re.sub(r"\n{3,}", "\n\n", text)


@mcp.tool(description=(
    "Fetch a web page through scrape.do (handles anti-bot blocks; set render_js=true for JS-heavy "
    "sites) and return its readable text (first max_chars characters; raise it only if needed)."
))
async def fetch_url(url: str, render_js: bool = False, max_chars: int = 6000) -> dict:
    _current_user()
    if not url.startswith(("http://", "https://")):
        return {"error": "url must start with http:// or https://"}
    if not SCRAPEDO_TOKEN:
        return {"error": "SCRAPEDO_TOKEN is not configured"}
    params = {"token": SCRAPEDO_TOKEN, "url": url}
    if render_js:
        params["render"] = "true"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get("https://api.scrape.do/", params=params)
    if r.status_code >= 400:
        return {"error": f"scrape.do returned HTTP {r.status_code}", "body": r.text[:500]}
    ctype = r.headers.get("content-type", "")
    if "html" in ctype or r.text.lstrip().startswith("<"):
        title, text = html_to_text(r.text)
    else:
        title, text = "", r.text
    return {"url": url, "title": title, "content": _truncate(text, max(1, min(max_chars, 100000)))}


# ---------------------------------------------------------------------------
# HTTP app + Open WebUI session authentication
# ---------------------------------------------------------------------------
class OpenWebUIAuth:
    """Pure-ASGI middleware: every request must carry a valid Open WebUI session token.

    Verified by calling Open WebUI's own /api/v1/auths/ endpoint with the token (so JWT
    secret, expiry, user deletion and role changes are all enforced by Open WebUI itself).
    Results are cached per token hash for AUTH_CACHE_TTL seconds.
    """

    def __init__(self, app, verifier=None):
        self.app = app
        self.verifier = verifier or self._verify_with_openwebui
        self._cache: dict[str, tuple[float, Optional[dict]]] = {}

    async def _verify_with_openwebui(self, token: str) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{OPENWEBUI_URL}/api/v1/auths/", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("id") or data.get("role") in (None, "pending"):
            return None
        return {"id": data["id"], "email": data.get("email"), "name": data.get("name"), "role": data.get("role")}

    async def verify(self, token: str) -> Optional[dict]:
        key = hashlib.sha256(token.encode()).hexdigest()
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and hit[0] > now:
            return hit[1]
        user = await self.verifier(token)
        self._cache[key] = (now + (AUTH_CACHE_TTL if user else 5), user)
        if len(self._cache) > 1000:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return user

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/healthz":
            return await _plain(send, 200, b"ok")
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        user = await self.verify(token) if token else None
        if not user:
            log.warning("rejected unauthenticated request from %s", (scope.get("client") or ["?"])[0])
            return await _plain(send, 401, b'{"error":"valid Open WebUI session token required"}')
        scope.setdefault("state", {})
        scope["state"]["owui_user"] = user
        scope["state"]["owui_token"] = token
        return await self.app(scope, receive, send)


async def _plain(send, status: int, body: bytes):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def build_app():
    app = mcp.http_app(path="/mcp", stateless_http=True)
    app.add_middleware(OpenWebUIAuth)
    return app


if __name__ == "__main__":
    import uvicorn

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(WORKSPACE_ROOT, 0o711)  # traversable, not listable: users can't enumerate each other
    if not shutil.which("python3"):
        log.warning("python3 not on PATH; run_python will fail")
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
