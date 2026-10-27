import logging
import os
import re
from urllib.parse import quote, urljoin, urlsplit

import requests
from flask import Flask, Response, abort, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

ALLOWED_HOSTS = {
    host.strip().lower().rstrip(".")
    for host in os.environ.get("PROXY_ALLOWED_HOSTS", "").split(",")
    if host.strip()
}
URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"')

if not ALLOWED_HOSTS:
    raise RuntimeError("Set PROXY_ALLOWED_HOSTS to one or more authorized hostnames.")


def validate_stream_url(stream_url):
    try:
        parsed = urlsplit(stream_url)
        port = parsed.port
    except ValueError:
        abort(400, description="Invalid stream URL.")

    hostname = parsed.hostname
    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname.lower().rstrip(".") not in ALLOWED_HOSTS
        or (port is not None and port not in (80, 443))
    ):
        abort(403, description="Stream URL host or scheme is not allowed.")

    return parsed


def proxy_url(stream_url):
    return f"{request.host_url.rstrip('/')}/stream?url={quote(stream_url, safe='')}"


def rewrite_playlist(playlist, base_url):
    rewritten_lines = []
    for line in playlist.splitlines():
        stripped_line = line.strip()
        if stripped_line.startswith("#"):
            line = URI_ATTRIBUTE.sub(
                lambda match: f'URI="{proxy_url(urljoin(base_url, match.group(1)))}"',
                line,
            )
        elif stripped_line:
            line = proxy_url(urljoin(base_url, stripped_line))
        rewritten_lines.append(line)
    return "\n".join(rewritten_lines) + "\n"


@app.get("/stream")
def proxy_stream():
    stream_url = request.args.get("url", "")
    if not stream_url:
        abort(400, description="Missing 'url' parameter.")

    parsed_url = validate_stream_url(stream_url)
    forwarded_headers = {}
    if "Range" in request.headers:
        forwarded_headers["Range"] = request.headers["Range"]
    if "If-Range" in request.headers:
        forwarded_headers["If-Range"] = request.headers["If-Range"]

    try:
        upstream = requests.get(
            stream_url,
            headers=forwarded_headers,
            stream=True,
            allow_redirects=False,
            timeout=(5, 30),
        )
    except requests.RequestException:
        app.logger.exception("Unable to fetch an authorized stream from %s", parsed_url.hostname)
        abort(502, description="Unable to fetch the upstream stream.")

    if 300 <= upstream.status_code < 400:
        upstream.close()
        abort(502, description="Upstream redirects are not supported.")

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    is_playlist = parsed_url.path.lower().endswith(".m3u8") or "mpegurl" in content_type.lower()
    if is_playlist and upstream.ok:
        try:
            playlist = rewrite_playlist(upstream.text, stream_url)
        finally:
            upstream.close()
        return Response(playlist, status=upstream.status_code, content_type=content_type)

    response_headers = {
        name: upstream.headers[name]
        for name in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified")
        if name in upstream.headers
    }

    def stream_content():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()

    return Response(
        stream_content(),
        status=upstream.status_code,
        headers=response_headers,
        content_type=content_type,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
