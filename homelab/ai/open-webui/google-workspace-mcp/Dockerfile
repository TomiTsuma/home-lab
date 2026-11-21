# Self-hosted Google Workspace MCP server (taylorwilsdon/google_workspace_mcp, MIT) for Open WebUI.
#
# Why self-hosted: Google's hosted *mcp.googleapis.com servers reject tool calls with
# "The caller does not have permission" unless the Cloud project is enrolled in the Workspace
# Developer Preview (2026-11-21). This server calls the regular Drive/Docs/Sheets/Slides REST
# APIs instead, which work with a normal OAuth client.
#
# Version pinned; bump deliberately after reviewing the changelog.
FROM python:3.11-slim

RUN pip install --no-cache-dir "workspace-mcp==2.6.0" \
    && useradd --create-home --uid 10001 gws \
    && mkdir -p /data/credentials /data/attachments \
    && chown -R gws:gws /data

USER gws
WORKDIR /home/gws
ENV WORKSPACE_MCP_CREDENTIALS_DIR=/data/credentials \
    WORKSPACE_ATTACHMENT_DIR=/data/attachments \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
# Tool scope is set in docker-compose.yml (command:) so it is visible next to the rest of the stack.
ENTRYPOINT ["workspace-mcp"]
