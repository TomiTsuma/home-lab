# Live sports streaming stack

Runtime data and Compose configuration are relative to this directory. The
build contexts point to `../DVarr_Source` and `../FruidDeepLinks_Source`, which
should remain alongside `stream/` in the `media/` folder.

Before starting, copy `.env.example` to `.env`, set unique `DVARR_AUTH_USER`
and `DVARR_AUTH_PASS` credentials, and replace `PROXY_ALLOWED_HOSTS` with the
exact hostnames of streams you are authorized to access. Separate multiple
hostnames with commas; wildcards are not supported. `DVARR_TSDB_API_KEY` is
optional and can also be configured in DVarr's settings.

Set `FRUIT_SERVER_URL` to the URL clients should use to reach FruitDeepLinks.
For VPN access, use the server's reachable Tailscale or WireGuard IP and the
configured `FRUIT_HOST_PORT`.

Start the services from this directory:

```sh
docker compose up -d --build
```

The published ports bind to localhost by default. To allow other devices on
your LAN to connect, set `STREAM_BIND_IP=0.0.0.0` in `.env` and restart the
stack. Do not expose these services directly to the public internet.

`stream-proxy` accepts only HTTP(S) URLs on the configured host allowlist,
does not follow redirects, and does not spoof browser headers or bypass
upstream access controls. HLS playlists are rewritten so their relative
segments and key/map URIs continue through the same restricted proxy.

Configuration/state directories for the aggregator and DVR are stored in
`FruitDeepLinks/data`, `FruitDeepLinks/out`, `FruitDeepLinks/logs`, and
`DVarr/config`, `DVarr/media`, `DVarr/segments`. The actual applications build
from their local source repositories rather than external image tags.
