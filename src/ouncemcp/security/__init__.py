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

from ouncemcp.security.cache import CacheView, PartitionedCache, partition_key
from ouncemcp.security.http import resolve_http_options
from ouncemcp.security.identity import (
    ANONYMOUS_IDENTITY,
    Identity,
    current_identity,
    identity_from_token,
)
from ouncemcp.security.passthrough import TokenPassthrough, check_outbound, passthrough_guard
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

__all__ = [
    "ANONYMOUS_IDENTITY",
    "CacheView",
    "Identity",
    "InvalidState",
    "PartitionedCache",
    "Policy",
    "PolicyRequest",
    "StateSigner",
    "TokenPassthrough",
    "UnsafeInputRequest",
    "all_of",
    "allow_all",
    "any_of",
    "audience_matches",
    "authenticated",
    "check_outbound",
    "current_identity",
    "identity_from_token",
    "partition_key",
    "passthrough_guard",
    "require_scopes",
    "require_tenant",
    "resolve_http_options",
    "writes_require",
]
