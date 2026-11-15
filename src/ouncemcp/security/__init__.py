"""Security and policy (spec section 8.2, proposal 0008).

- ``Identity`` and ``current_identity``: the verified caller, from credentials only (SEC-1).
- ``Policy`` and helpers: one decision for listing, search and calls (SEC-2), made on the
  declared effect, never on annotations (SEC-3). ``audience=`` on the server checks the
  token audience (SEC-4).
- ``check_outbound`` and ``passthrough_guard``: never forward the inbound token (SEC-4).
- ``PartitionedCache``: private caches keyed by authorization context (SEC-5).
- ``StateSigner``: handles, approvals and request state bound to caller, request and
  expiry, optionally single-use (SEC-6).
- ``ToolContext.require_input`` refuses to ask after an effect unless replay-safe (SEC-7).
- HTTP transports validate ``Host`` and ``Origin`` and bind to loopback by default (SEC-8).
- Trace redaction (SEC-11) lives in ``ouncemcp.trace``.
"""

import functools
import warnings
from collections.abc import Callable
from typing import Any, TypeVar

from ouncemcp.security import cache as _cache
from ouncemcp.security import http as _http
from ouncemcp.security.cache import CacheView, PartitionedCache
from ouncemcp.security.fetch import PinnedTransport, safe_client
from ouncemcp.security.identity import (
    ANONYMOUS_IDENTITY,
    Identity,
    current_identity,
    identity_from_token,
)
from ouncemcp.security.passthrough import (
    TokenPassthrough,
    check_outbound,
    guarded_client,
    passthrough_guard,
)
from ouncemcp.security.policy import (
    Policy,
    PolicyRequest,
    all_of,
    allow_all,
    any_of,
    audience_matches,
    authenticated,
    require_scopes,
    require_tenant,
    writes_require,
)
from ouncemcp.security.replay import UnsafeInputRequest
from ouncemcp.security.state import InvalidState, StateSigner
from ouncemcp.security.urls import URLNotAllowed, check_url, checked_addresses

_F = TypeVar("_F", bound=Callable[..., Any])


def _internal(func: _F, reason: str) -> _F:
    """Keep an internal helper importable from here for one release, with a warning
    (proposal 0029): it was public only by accident."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(
            f"ouncemcp.security.{func.__name__} is internal and will be removed in 1.1; {reason}",
            DeprecationWarning,
            stacklevel=2,
        )
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


resolve_http_options = _internal(
    _http.resolve_http_options,
    "OunceServer.run applies it for you; pass host and allowed_hosts to run() instead.",
)
partition_key = _internal(
    _cache.partition_key,
    "PartitionedCache uses it for you; use ctx.cache or PartitionedCache instead.",
)

__all__ = [
    "ANONYMOUS_IDENTITY",
    "CacheView",
    "Identity",
    "InvalidState",
    "PartitionedCache",
    "PinnedTransport",
    "Policy",
    "PolicyRequest",
    "StateSigner",
    "TokenPassthrough",
    "URLNotAllowed",
    "UnsafeInputRequest",
    "all_of",
    "allow_all",
    "any_of",
    "audience_matches",
    "authenticated",
    "check_outbound",
    "check_url",
    "checked_addresses",
    "current_identity",
    "guarded_client",
    "identity_from_token",
    "partition_key",
    "passthrough_guard",
    "require_scopes",
    "require_tenant",
    "resolve_http_options",
    "safe_client",
    "writes_require",
]
