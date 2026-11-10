"""Test helpers for servers built with OunceMCP.

``as_caller`` makes the code inside it run as if the transport had verified an access
token, so policies, cursors, caches and state tokens can be tested in-process::

    with as_caller(subject="alice", tenant="acme", scopes=["tickets:read"]):
        result = await server.call_tool("tickets.search", {"query": "refund"})
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from fastmcp.server.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser


def make_token(
    *,
    subject: str | None = "user",
    issuer: str | None = None,
    client_id: str = "test-client",
    tenant: str | None = None,
    scopes: Sequence[str] = (),
    audience: str | Sequence[str] | None = None,
    token: str = "test-inbound-token-0123456789",
    claims: dict[str, Any] | None = None,
) -> AccessToken:
    """A FastMCP ``AccessToken`` as a token verifier would produce it."""
    all_claims: dict[str, Any] = dict(claims or {})
    if subject is not None:
        all_claims.setdefault("sub", subject)
    if issuer is not None:
        all_claims.setdefault("iss", issuer)
    if tenant is not None:
        all_claims.setdefault("tenant", tenant)
    if audience is not None:
        all_claims.setdefault("aud", audience)
    return AccessToken(
        token=token,
        client_id=client_id,
        scopes=list(scopes),
        subject=subject,
        claims=all_claims,
    )


@contextmanager
def as_caller(access_token: AccessToken | None = None, **fields: Any) -> Iterator[AccessToken]:
    """Run the enclosed code as the caller of ``access_token`` (else ``make_token(**fields)``)."""
    access = access_token or make_token(**fields)
    reset = auth_context_var.set(AuthenticatedUser(access))
    try:
        yield access
    finally:
        auth_context_var.reset(reset)
