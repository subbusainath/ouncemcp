"""Access policy for tools (spec SEC-2, SEC-3 and the audience half of SEC-4).

A policy is a function from a ``PolicyRequest`` to ``True`` (allow) or ``False`` (deny).
OunceMCP turns it into a FastMCP authorization check on the tool, so the same decision
hides the tool from ``tools/list`` and search and refuses a call by a guessed name
(SEC-2). The request carries the effect the tool declared at registration, never its
MCP annotations, which are hints a client or proxy can change (SEC-3).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastmcp.server.auth import AuthCheck, AuthContext

from ouncemcp.core.effects import Effect
from ouncemcp.security.identity import ANONYMOUS_IDENTITY, Identity, identity_from_token


@dataclass(frozen=True)
class PolicyRequest:
    """What a policy decides on.

    Attributes:
        tool: The tool name.
        effect: The effect declared with ``@server.tool(effect=...)``.
        tags: The tool's tags.
        identity: The verified caller (anonymous when there is no verified token).
    """

    tool: str
    effect: Effect
    tags: frozenset[str]
    identity: Identity


Policy = Callable[[PolicyRequest], bool | Awaitable[bool]]


async def evaluate(policy: Policy, request: PolicyRequest) -> bool:
    """Run ``policy``. Anything other than ``True`` (including an exception) denies."""
    try:
        outcome: Any = policy(request)
        if inspect.isawaitable(outcome):
            outcome = await outcome
    except Exception:  # noqa: BLE001 - a failing policy must fail closed
        return False
    return outcome is True


# ------------------------------------------------------------------ building blocks


def allow_all(request: PolicyRequest) -> bool:
    """Allow every caller. The default when no policy is set."""
    return True


def authenticated(request: PolicyRequest) -> bool:
    """Allow only callers with a verified token."""
    return request.identity.authenticated


def require_scopes(*scopes: str) -> Policy:
    """Allow callers whose token grants every scope in ``scopes``."""
    needed = frozenset(scopes)

    def check(request: PolicyRequest) -> bool:
        return request.identity.authenticated and needed <= request.identity.scopes

    check.__name__ = f"require_scopes({', '.join(sorted(needed))})"
    return check


def require_tenant(*tenants: str) -> Policy:
    """Allow callers whose verified tenant claim is one of ``tenants``."""
    allowed = frozenset(tenants)

    def check(request: PolicyRequest) -> bool:
        return request.identity.tenant is not None and request.identity.tenant in allowed

    return check


def writes_require(policy: Policy) -> Policy:
    """Allow reads to everyone, and writes and destructive calls only when ``policy`` allows."""

    async def check(request: PolicyRequest) -> bool:
        if request.effect is Effect.READ:
            return True
        return await evaluate(policy, request)

    return check


def all_of(*policies: Policy) -> Policy:
    """Allow only when every policy allows."""

    async def check(request: PolicyRequest) -> bool:
        for policy in policies:
            if not await evaluate(policy, request):
                return False
        return True

    return check


def any_of(*policies: Policy) -> Policy:
    """Allow when at least one policy allows."""

    async def check(request: PolicyRequest) -> bool:
        for policy in policies:
            if await evaluate(policy, request):
                return True
        return False

    return check


# ------------------------------------------------------------------ audience (SEC-4)


def audience_matches(identity: Identity, audiences: Iterable[str]) -> bool:
    """True when the verified token was issued for one of ``audiences``.

    A token without an audience is refused: it could have been meant for any server.
    """
    wanted = {a.rstrip("/") for a in audiences}
    return any(a.rstrip("/") in wanted for a in identity.audiences)


# ------------------------------------------------------------------ FastMCP adapter


@dataclass(frozen=True)
class ToolFacts:
    """The registration facts a policy may rely on, looked up by tool name."""

    effect: Effect
    tags: frozenset[str]


def policy_check(
    policies: Iterable[Policy],
    *,
    facts: Callable[[str], ToolFacts | None],
    audiences: tuple[str, ...] = (),
    tenant_claims: tuple[str, ...] = (),
) -> AuthCheck:
    """A FastMCP ``AuthCheck`` that applies ``policies`` and the audience check.

    The tool's effect and tags come from ``facts`` (what was declared at registration),
    not from the component FastMCP hands over, so annotations never decide access.
    """
    chain = tuple(policies)

    async def check(ctx: AuthContext) -> bool:
        name = getattr(ctx.component, "name", "")
        known = facts(name)
        if known is None:
            return False
        token = ctx.token
        if token is None:
            identity = ANONYMOUS_IDENTITY
        elif tenant_claims:
            identity = identity_from_token(token, tenant_claims=tenant_claims)
        else:
            identity = identity_from_token(token)
        if audiences and not (identity.authenticated and audience_matches(identity, audiences)):
            return False
        request = PolicyRequest(tool=name, effect=known.effect, tags=known.tags, identity=identity)
        for policy in chain:
            if not await evaluate(policy, request):
                return False
        return True

    check.__name__ = "ouncemcp_policy"
    return check
