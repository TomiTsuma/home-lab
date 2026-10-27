# Homelab Live Sports Streaming Stack

This stack keeps its compose file, proxy, and persistent configuration paths
inside this `stream/` directory. It combines the FruitDeepLinks aggregator,
DVarr schedule/DVR service, a restricted HLS proxy, and the `nba-go` CLI.

## Files and directories

```text
stream/
├── .env.example
├── docker-compose.yml
├── DVarr/
│   └── config/
├── FruitDeepLinks/
│   └── config/
└── proxy/
    ├── Dockerfile
    ├── hls_proxy.py
    └── requirements.txt
```

The two `config/` directories are mounted into their respective containers so
their state survives container replacement.

## Configure and start

1. Copy `.env.example` to `.env`.
2. Set `SPORTSDB_API_KEY` and replace `PROXY_ALLOWED_HOSTS` with the exact
   hostnames of streams you are authorized to access. Use comma-separated
   hostnames for multiple providers; do not use wildcards.
3. From this directory, build and start the services:

   ```sh
   docker compose up -d --build
   ```

The published ports bind to localhost by default. To permit access from other
devices on your LAN, change `STREAM_BIND_IP` to `0.0.0.0` in `.env`, then
restart the stack. Do not expose the services directly to the public internet.

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
