"""Verified caller identity (spec SEC-1).

Identity comes only from a credential the transport verified: the FastMCP access token.
Nothing the client says about itself (``clientInfo``, a user agent, an argument) is read
here, so a client cannot become someone else by renaming itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastmcp.server.dependencies import get_access_token

ANONYMOUS = "anonymous"
DEFAULT_TENANT_CLAIMS: tuple[str, ...] = ("tenant", "tenant_id", "tid", "org_id")


@dataclass(frozen=True)
class Identity:
    """The verified caller of one request.

    Attributes:
        principal: A stable id for the caller. ``issuer|subject`` when the token names
            an issuer, else the subject, else the OAuth client id. Subjects are only
            unique per issuer, so the issuer is part of the id.
        subject: The token's ``sub``, if any.
        issuer: The token's ``iss``, if any.
        client_id: The OAuth client the token was issued to.
        tenant: The tenant claim, if the token carries one (see ``tenant_claims``).
        scopes: Granted scopes.
        audiences: The token's audiences (``aud`` claim and RFC 8707 ``resource``).
        claims: All verified claims. The raw token is never kept here, so it cannot be
            forwarded by accident (SEC-4).
    """

    principal: str
    subject: str | None = None
    issuer: str | None = None
    client_id: str | None = None
    tenant: str | None = None
    scopes: frozenset[str] = frozenset()
    audiences: frozenset[str] = frozenset()
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)
    authenticated: bool = True

    def has_scopes(self, *scopes: str) -> bool:
        return set(scopes) <= self.scopes


ANONYMOUS_IDENTITY = Identity(principal=ANONYMOUS, authenticated=False)


def _audiences(token: Any, claims: Mapping[str, Any]) -> frozenset[str]:
    found: set[str] = set()
    aud = claims.get("aud")
    if isinstance(aud, str):
        found.add(aud)
    elif isinstance(aud, Sequence):
        found.update(str(a) for a in aud)
    resource = getattr(token, "resource", None)
    if resource:
        found.add(str(resource))
    return frozenset(found)


def identity_from_token(
    token: Any, *, tenant_claims: Sequence[str] = DEFAULT_TENANT_CLAIMS
) -> Identity:
    """Build an ``Identity`` from a verified access token."""
    claims: Mapping[str, Any] = dict(getattr(token, "claims", None) or {})
    subject = getattr(token, "subject", None) or claims.get("sub") or None
    issuer = claims.get("iss") or None
    client_id = getattr(token, "client_id", None) or None
    if subject:
        principal = f"{issuer}|{subject}" if issuer else str(subject)
    else:
        principal = str(client_id or ANONYMOUS)
    tenant = next((str(claims[c]) for c in tenant_claims if claims.get(c)), None)
    return Identity(
        principal=principal,
        subject=str(subject) if subject else None,
        issuer=str(issuer) if issuer else None,
        client_id=str(client_id) if client_id else None,
        tenant=tenant,
        scopes=frozenset(getattr(token, "scopes", None) or ()),
        audiences=_audiences(token, claims),
        claims=claims,
    )


def current_identity(*, tenant_claims: Sequence[str] = DEFAULT_TENANT_CLAIMS) -> Identity:
    """The verified caller of the current request, or the anonymous identity.

    Anonymous means the server has no authentication configured (a single local caller
    over stdio) or the request carried no verified token.
    """
    token = get_access_token()
    if token is None:
        return ANONYMOUS_IDENTITY
    return identity_from_token(token, tenant_claims=tenant_claims)


def current_token_value() -> str | None:
    """The raw inbound bearer token, for the passthrough guard only (SEC-4)."""
    token = get_access_token()
    return getattr(token, "token", None) if token is not None else None
