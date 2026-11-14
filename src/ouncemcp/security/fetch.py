"""Outbound HTTP that only reaches checked addresses (spec SEC-9).

``check_url`` checks a URL before a fetch. ``safe_client`` makes the fetch itself safe:

- **Pinned connections.** Each request's host is resolved once, every address is
  checked (public only, unless ``allow_private=True``), and the connection goes to the
  checked address. A DNS answer that changes after the check (DNS rebinding) cannot
  redirect the connection. TLS still verifies the certificate for the host name: the
  name is sent as SNI and the ``Host`` header is unchanged.
- **Redirects checked again.** Redirects are followed by the client, and every hop goes
  through the same check, so ``https://public.example`` cannot bounce the fetch to
  ``http://169.254.169.254/``. ``max_redirects`` (default 5) bounds the chain.
- **One connection pool per host name.** Two names on one address never share a
  connection, so a connection verified for one certificate name is never reused for
  another.
- **No proxies from the environment.** ``HTTP(S)_PROXY`` would bypass the pinned
  connection, so the client ignores them (``trust_env=False``).
- **No token passthrough.** The ``passthrough_guard`` (SEC-4) runs on every request.

Failures raise ``URLNotAllowed``, a validation error the model can act on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

import anyio.to_thread
import httpx2

from ouncemcp.security.passthrough import passthrough_guard
from ouncemcp.security.urls import Resolver, checked_addresses

TransportFactory = Callable[[], httpx2.AsyncBaseTransport]


class PinnedTransport(httpx2.AsyncBaseTransport):
    """An httpx2 transport that connects only to addresses ``checked_addresses`` allows."""

    def __init__(
        self,
        *,
        schemes: Sequence[str] = ("https", "http"),
        allow_hosts: Sequence[str] | None = None,
        allow_private: bool = False,
        resolve: Resolver | None = None,
        transport_factory: TransportFactory | None = None,
        **transport_kwargs: Any,
    ) -> None:
        lowered = tuple(s.lower() for s in schemes)
        if not lowered or any(s not in ("http", "https") for s in lowered):
            raise ValueError("PinnedTransport fetches http and https URLs only")
        self._check = partial(
            checked_addresses,
            schemes=lowered,
            allow_hosts=allow_hosts,
            allow_private=allow_private,
            resolve=resolve,
        )
        self._factory = transport_factory or partial(httpx2.AsyncHTTPTransport, **transport_kwargs)
        self._pools: dict[tuple[str, str, int | None], httpx2.AsyncBaseTransport] = {}

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        host, addresses = await anyio.to_thread.run_sync(self._check, str(request.url))
        address = addresses[0]
        request.url = request.url.copy_with(host=address)
        request.extensions = {**request.extensions, "sni_hostname": host}
        key = (request.url.scheme, host, request.url.port)
        pool = self._pools.get(key)
        if pool is None:
            pool = self._pools[key] = self._factory()
        return await pool.handle_async_request(request)

    async def aclose(self) -> None:
        pools, self._pools = list(self._pools.values()), {}
        for pool in pools:
            await pool.aclose()


def safe_client(
    *,
    allow_hosts: Sequence[str] | None = None,
    allow_private: bool = False,
    schemes: Sequence[str] = ("https", "http"),
    resolve: Resolver | None = None,
    max_redirects: int = 5,
    transport_factory: TransportFactory | None = None,
    **kwargs: Any,
) -> httpx2.AsyncClient:
    """An ``httpx2.AsyncClient`` for URLs the model chose (SEC-9).

    ``allow_hosts`` limits fetches to these hosts (``.example.com`` for subdomains);
    ``allow_private=True`` permits private and loopback addresses (still pinned, still
    allowlisted). Other arguments go to the client (``timeout``, ``headers``...).
    """
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [passthrough_guard(), *hooks.get("request", [])]
    transport_kwargs = {k: kwargs.pop(k) for k in ("verify", "http2", "limits") if k in kwargs}
    transport = PinnedTransport(
        schemes=schemes,
        allow_hosts=allow_hosts,
        allow_private=allow_private,
        resolve=resolve,
        transport_factory=transport_factory,
        **transport_kwargs,
    )
    return httpx2.AsyncClient(
        transport=transport,
        follow_redirects=True,
        max_redirects=max_redirects,
        trust_env=False,
        event_hooks=hooks,
        **kwargs,
    )
