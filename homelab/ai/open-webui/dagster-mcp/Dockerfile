# Dagster MCP server (fabdendev/dagster-mcp, MIT) for Open WebUI.
#
# Community project (single maintainer), so it is pinned AND hash-checked: the build downloads the exact wheel
# and aborts unless its sha256 equals the value reviewed on 2026-11-22 (source read in full: its only outbound call
# is a GraphQL POST to DAGSTER_URL; no telemetry). Bump version and hash together after re-reading the diff.
#   0.12.3, released 2026-09-28, Python >= 3.12, deps: fastmcp, httpx.
#
# It only speaks stdio upstream, so serve_http.py (our code) serves it over streamable HTTP behind a bearer token and a
# Host allow-list. It is READ-ONLY here: with DAGSTER_READ_ONLY=true (compose hard-codes it) the launch/terminate/
# backfill/schedule/sensor tools are never registered at all.
FROM python:3.12-slim

ARG DAGSTER_MCP_VERSION=0.12.3
ARG DAGSTER_MCP_SHA256=f9b9c6eefb2f195b19a41beebc6cc0f2531633926fae35afd707ffe039255e96

RUN pip download --no-deps --no-cache-dir -d /tmp/w "dagster-mcp==${DAGSTER_MCP_VERSION}" \
    && echo "${DAGSTER_MCP_SHA256}  /tmp/w/dagster_mcp-${DAGSTER_MCP_VERSION}-py3-none-any.whl" | sha256sum -c - \
    && pip install --no-cache-dir "/tmp/w/dagster_mcp-${DAGSTER_MCP_VERSION}-py3-none-any.whl" "uvicorn>=0.31" \
    && rm -rf /tmp/w \
    && useradd --create-home --uid 10003 dgmcp

COPY serve_http.py /opt/serve_http.py
USER dgmcp
WORKDIR /home/dgmcp
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["python", "/opt/serve_http.py"]
