# Deploying over HTTP

`server.run("http")` binds to `127.0.0.1:8000` and validates `Host` and `Origin` (SEC-8), which blocks DNS-rebinding attacks from web pages against a local server.

For a public deployment, name the hostnames clients use:

```python
server.run("http", host="0.0.0.0", port=8080,
           allowed_hosts=["mcp.example.com"], allowed_origins=["https://app.example.com"])
```

Binding a non-loopback address without `allowed_hosts` raises an error. Behind a reverse proxy that already checks `Host` and `Origin`, pass `host_origin_protection=False`.

For ASGI servers, use `server.http_app()`, which has the guard on. Also set `OUNCEMCP_CURSOR_KEYS` on every replica.
