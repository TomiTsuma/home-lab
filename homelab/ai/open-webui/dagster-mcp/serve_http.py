"""Serve dagster-mcp over streamable HTTP with a mandatory bearer token and a Host allow-list.

dagster-mcp's own entry point only runs stdio. This wraps the same FastMCP object (`dagster_mcp.server.mcp`) in an ASGI app and
adds the two checks the other MCP containers in this stack have:
  * Authorization: Bearer <DAGSTER_MCP_AUTH_TOKEN>  (constant-time compare; 401 otherwise),
  * Host header must be in DAGSTER_MCP_ALLOWED_HOSTS (comma list; 421 otherwise), which blocks DNS-rebinding.
GET /health is unauthenticated and only says the process is up (it does not touch Dagster).

Env: DAGSTER_MCP_AUTH_TOKEN (required, >= 32 chars), DAGSTER_MCP_ALLOWED_HOSTS (default "dagster-mcp:8000,dagster-mcp"),
DAGSTER_MCP_PORT (default 8000). Dagster settings (DAGSTER_URL, DAGSTER_API_TOKEN, DAGSTER_READ_ONLY, ...) are read by dagster-mcp.
"""
import hmac
import json
import os
import sys

import uvicorn
from starlette.middleware import Middleware

TOKEN = os.environ.get("DAGSTER_MCP_AUTH_TOKEN", "")
if len(TOKEN) < 32:
    sys.exit("DAGSTER_MCP_AUTH_TOKEN is required and must be at least 32 characters (openssl rand -hex 32)")
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("DAGSTER_MCP_ALLOWED_HOSTS", "dagster-mcp:8000,dagster-mcp").split(",") if h.strip()}
PORT = int(os.environ.get("DAGSTER_MCP_PORT", "8000"))


class Guard:
    """Pure ASGI middleware: Host allow-list, then bearer token (except GET /health)."""

    def __init__(self, app):
        self.app = app

    async def _reply(self, send, status, body):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if headers.get("host", "").lower() not in ALLOWED_HOSTS:
            return await self._reply(send, 421, {"error": "Invalid Host header"})
        if scope["method"] == "GET" and scope["path"] == "/health":
            return await self._reply(send, 200, {"status": "ok"})
        presented = headers.get("authorization", "")
        if not (presented.startswith("Bearer ") and hmac.compare_digest(presented[7:].encode(), TOKEN.encode())):
            return await self._reply(send, 401, {"error": "Unauthorized"})
        return await self.app(scope, receive, send)


from dagster_mcp.server import READ_ONLY, mcp  # noqa: E402  (imported after the env check so a bad config fails fast)

app = mcp.http_app(path="/mcp", middleware=[Middleware(Guard)])

if __name__ == "__main__":
    print(f"dagster-mcp serving on :{PORT}/mcp ({'read-only' if READ_ONLY else 'READ-WRITE'}), allowed hosts: {sorted(ALLOWED_HOSTS)}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
