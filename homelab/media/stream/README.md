# Live sports streaming stack

All paths in this stack are relative to this directory. Before starting it, copy
`.env.example` to `.env`, set `SPORTSDB_API_KEY`, and replace
`PROXY_ALLOWED_HOSTS` with the exact hostnames of streams you are authorized to
access. Separate multiple hostnames with commas; wildcards are not supported.

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
`FruitDeepLinks/config` and `DVarr/config`.
