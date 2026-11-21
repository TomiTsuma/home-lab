# Homelab MCP tool server for Open WebUI (see server.py docstring for the security model).
FROM python:3.11-slim

# Tools available to sandboxed commands. poppler-utils: pdftotext/pdftoppm; fonts for charts/PDFs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates poppler-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/local/bin/python3 /usr/local/bin/python

WORKDIR /app
COPY requirements.txt requirements-sandbox.txt ./
# Server deps + the libraries sandboxed code commonly needs to generate documents/charts.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-sandbox.txt

COPY server.py ./

# The server itself runs as root inside the container ONLY so it can drop each command to a
# per-user UID (subprocess user=/group=). Compose drops every capability except SETUID,
# SETGID, CHOWN, DAC_OVERRIDE, FOWNER and sets no-new-privileges.
ENV WORKSPACE_ROOT=/workspace PORT=8000 PYTHONUNBUFFERED=1
VOLUME ["/workspace"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["python", "server.py"]
