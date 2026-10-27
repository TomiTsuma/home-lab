# Homelab Live Sports Streaming Stack

This stack keeps its compose file, proxy, and persistent application data
inside this `stream/` directory. It builds FruitDeepLinks and DVarr from the
local source repositories in the parent `media/` directory, and includes a
restricted HLS proxy and the `nba-go` CLI.

## Files and directories

```text
stream/
├── .env.example
├── docker-compose.yml
├── DVarr/
│   ├── config/
│   ├── media/
│   └── segments/
├── FruitDeepLinks/
│   ├── data/
│   ├── logs/
│   └── out/
└── proxy/
    ├── Dockerfile
    ├── hls_proxy.py
    └── requirements.txt
```

Compose builds from `../DVarr_Source` and `../FruidDeepLinks_Source`. Keep those
repositories alongside `stream/` under `media/`. DVarr's database/config,
recordings, and temporary segments, plus FruitDeepLinks' database, exports,
and logs, are mounted from directories under `stream/` so they survive
container replacement.

## Configure and start

1. Copy `.env.example` to `.env`.
2. Set `DVARR_AUTH_USER` and `DVARR_AUTH_PASS` to non-default credentials.
   `DVARR_TSDB_API_KEY` is optional; without it, add a TheSportsDB key in
   DVarr's settings.
3. Replace `PROXY_ALLOWED_HOSTS` with the exact hostnames of streams you are
   authorized to access. Use comma-separated hostnames for multiple providers;
   do not use wildcards.
4. Set `FRUIT_SERVER_URL` to an address reachable by the clients using its M3U
   links. For VPN clients, this can be the server's Tailscale or WireGuard IP.
5. From this directory, build and start the services:

   ```sh
   docker compose up -d --build
   ```

The published ports bind to localhost by default. To permit access from other
devices on your LAN, change `STREAM_BIND_IP` to `0.0.0.0` in `.env`, then
restart the stack. Do not expose the services directly to the public internet.

The local source Dockerfiles expose FruitDeepLinks on container port `6655` and
DVarr on `1867`. Compose publishes them on host ports `8080` and `8686`,
respectively, by default.

## Proxy behavior

The proxy allows only HTTP(S) requests to the configured hostnames, rejects
redirects, and rewrites HLS playlist segment and key/map URIs through the
proxy. It does not spoof browser headers or bypass upstream access controls.
If an authorized provider requires authentication or denies proxy access, use
its supported integration rather than attempting to circumvent those controls.

For example, a client can request an authorized playlist through:

```text
http://localhost:5050/stream?url=https%3A%2F%2Fmedia.example.com%2Fpath%2Fstream.m3u8
```

Replace the example host and path with the authorized stream URL, making sure
the hostname is listed in `PROXY_ALLOWED_HOSTS`.

## Operations

View service logs:

```sh
docker compose logs -f stream-proxy
```

Check that the proxy is listening:

```sh
curl -I http://localhost:5050/stream
```

The missing-URL response is expected for this health check; a `400` response
confirms that the route is reachable.
