# ClickHouse MCP server (ClickHouse/mcp-clickhouse, Apache-2.0) for Open WebUI.
#
# Talks to ClickHouse over its HTTP interface (8123, or 8443 with TLS) using clickhouse-connect; it does NOT
# use the native port 9000. Read-only by default (queries run with readonly=1 on the server side).
#
# Version pinned; bump deliberately after reading the release notes (0.7.0 = PyPI latest on 2026-11-22).
# Python 3.12 is the version the project recommends for local launches.
FROM python:3.12-slim

RUN pip install --no-cache-dir "mcp-clickhouse==0.7.0" \
    && useradd --create-home --uid 10002 chmcp

USER chmcp
WORKDIR /home/chmcp
# Streamable HTTP on 8000 inside the container. Bearer-token auth, Host-header allow-list and every
# ClickHouse setting come from docker-compose.yml / .env (see the README section "ClickHouse MCP").
ENV CLICKHOUSE_MCP_SERVER_TRANSPORT=http \
    CLICKHOUSE_MCP_BIND_HOST=0.0.0.0 \
    CLICKHOUSE_MCP_BIND_PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["mcp-clickhouse"]
