"""Refuse to forward the caller's MCP access token upstream (spec SEC-4).

An MCP server must use its own credentials for upstream APIs. Forwarding the token the
client presented lets any upstream that accepts it act as the user (the "confused
deputy" problem in the MCP authorization security considerations).

``check_outbound(headers)`` raises if any header carries the inbound token.
``passthrough_guard()`` is the same check as an httpx request hook::

    client = httpx2.AsyncClient(event_hooks={"request": [passthrough_guard()]})
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ouncemcp.core.errors import ErrorCategory, OunceError
from ouncemcp.security.identity import current_token_value

logger = logging.getLogger("ouncemcp")

# Shorter values are too likely to match by chance.
_MIN_TOKEN_CHARS = 8


class TokenPassthrough(OunceError):
    """An upstream request carried the caller's MCP access token."""

    category: ErrorCategory = "permission"
    code = "token_passthrough"

    def __init__(self) -> None:
        super().__init__(
            "The server refused to send your MCP credentials to another service. "
            "This is a server configuration problem; report it to the server owner."
        )


def _carries(value: Any, token: str) -> bool:
    if isinstance(value, bytes):
        value = value.decode("latin-1", "replace")
    return isinstance(value, str) and token in value


def check_outbound(headers: Mapping[str, Any] | Any, *, token: str | None = None) -> None:
    """Raise ``TokenPassthrough`` if ``headers`` contain the inbound access token.

    ``token`` defaults to the token of the current request. Outside a request, or when
    the request had no token, there is nothing to forward and the check passes.
    """
    inbound = token if token is not None else current_token_value()
    if not inbound or len(inbound) < _MIN_TOKEN_CHARS:
        return
    items: Any = headers.items() if hasattr(headers, "items") else headers
    for name, value in items:
        if _carries(value, inbound):
            logger.error(
                "ouncemcp: blocked an upstream request that carried the caller's MCP access "
                "token in header %r. Use the server's own upstream credentials (spec SEC-4).",
                name,
            )
            raise TokenPassthrough()


def passthrough_guard() -> Callable[[Any], Awaitable[None]]:
    """An httpx ``request`` event hook that applies ``check_outbound``."""

    async def hook(request: Any) -> None:
        check_outbound(request.headers)

    return hook


def guarded_client(**kwargs: Any) -> Any:
    """An ``httpx2.AsyncClient`` with ``passthrough_guard`` installed, for ``ctx.deps``.

    Takes the usual client arguments (``base_url``, ``headers``, ``timeout``...). Any
    ``event_hooks`` you pass are kept; the guard runs first.
    """
    import httpx2

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [passthrough_guard(), *hooks.get("request", [])]
    return httpx2.AsyncClient(event_hooks=hooks, **kwargs)
