# Deploying over HTTP

## Defaults

`server.run("http")` binds to `127.0.0.1:8000` and validates `Host` and `Origin` (SEC-8), which blocks DNS-rebinding attacks from web pages against a local server. For a public deployment, name the hostnames clients use:

```python
server.run("http", host="0.0.0.0", port=8080,
           allowed_hosts=["mcp.example.com"], allowed_origins=["https://app.example.com"])
```

Binding a non-loopback address without `allowed_hosts` raises an error. Behind a reverse proxy that already checks `Host` and `Origin`, pass `host_origin_protection=False`. For ASGI servers, use `server.http_app()`, which has the guard on.

## Running more than one replica

Anything that must survive a restart or be seen by another replica lives in the store: idempotency records, single-use approval receipts, handles and jobs. Give every replica the same store and signing keys:

```python
import redis.asyncio as redis
from ouncemcp import OunceServer, RedisStore

server = OunceServer("tickets", store=RedisStore(redis.Redis.from_url(REDIS_URL)))
```

and set `OUNCEMCP_CURSOR_KEYS` (comma-separated, newest first) to the same value everywhere. `MemoryStore`, the default, is for one process only.

## Health, readiness and metrics

| Route | Answers |
| --- | --- |
| `GET /healthz` | `200 {"status": "ok"}` while the process serves requests |
| `GET /readyz` | `200` when the store answers and every `readiness=[...]` check returns `True`, else `503` with the failing names |
| `GET /metrics` | With `OunceServer(metrics=True)`: calls, errors by category, truncations, result tokens and latency per tool, in Prometheus format |

Probes send the `Host` header too, so include the name or address they use in `allowed_hosts`.

## Configuration file

Keep budgets, limits, discovery and security settings in `ouncemcp.toml` and build the server with `OunceServer.from_config("tickets", "ouncemcp.toml")`; keyword arguments override the file. See `ouncemcp.core.config` for every key.

## Container

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir . redis
ENV OUNCEMCP_CURSOR_KEYS=change-me-to-a-32-byte-secret
EXPOSE 8080
CMD ["python", "-c", "from server import server; server.run('http', host='0.0.0.0', port=8080, allowed_hosts=['mcp.example.com', 'localhost'])"]
```

Set the real `OUNCEMCP_CURSOR_KEYS` and Redis URL from your secret store, not the image. Point the liveness probe at `/healthz` and the readiness probe at `/readyz`.
