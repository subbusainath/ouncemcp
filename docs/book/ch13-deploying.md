# 13. Deploying

So far Shelf has run locally. This chapter runs it over HTTP for many users, on more than one replica.

## HTTP

```python
server.run("http", host="0.0.0.0", port=8080,
           allowed_hosts=["shelf.example.com"], allowed_origins=["https://app.example.com"])
```

Binding a public address without `allowed_hosts` is an error: `Host` and `Origin` checks stop DNS rebinding attacks from a browser on the user's machine. Behind a reverse proxy that already checks them, pass `host_origin_protection=False`. For an ASGI server such as uvicorn, use `server.http_app()`.

Add authentication as in chapter 7 (`auth=` and `audience=`); over HTTP, a server without it is open to anyone who can reach it.

## More than one replica

Everything that must survive a restart or be seen by another replica lives in the **store**: idempotency records (chapter 6), approval receipts, handles and jobs (chapter 8). The default `MemoryStore` is for one process. Share Redis:

```python
import redis.asyncio as redis
from ouncemcp import OunceServer, RedisStore

server = OunceServer("shelf", store=RedisStore(redis.Redis.from_url(REDIS_URL)), handles=True)
```

and set the same signing keys everywhere, newest first, so a cursor issued by one replica works on another:

```bash
export OUNCEMCP_CURSOR_KEYS="key-2026-11,key-2026-08"
```

To rotate, add the new key at the front, deploy, and remove the old one once its cursors have expired.

## Health and metrics

| Route | Answers |
| --- | --- |
| `GET /healthz` | `200` while the process serves requests |
| `GET /readyz` | `200` when the store answers and every `readiness=[...]` check passes, else `503` |
| `GET /metrics` | With `metrics=True`: calls, errors by category, truncations, result tokens and latency per tool, for Prometheus |

Probes send a `Host` header too, so include the name they use in `allowed_hosts`.

## Configuration in a file

Keep budgets, limits, discovery and security settings out of code:

```python
server = OunceServer.from_config("shelf", "ouncemcp.toml")
```

Keyword arguments override the file.

## A checklist

- [ ] `auth=` and `audience=` set; `allowed_hosts` lists the public name and the probe names.
- [ ] A shared store and the same `OUNCEMCP_CURSOR_KEYS` on every replica.
- [ ] `ouncemcp check` and the task suite pass in CI.
- [ ] Traces or metrics go somewhere you look.
- [ ] Every write has `idempotency=True`; every destructive tool has a policy and an approval.

The [deployment guide](../guides/deploying-http.md) has the details.
