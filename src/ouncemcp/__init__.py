"""OunceMCP: build MCP servers and clients that are fast, low-noise, and safe for agents.

Pre-alpha (milestone M0). See docs/spec.md for the full specification.
"""

from ouncemcp.core import (
    Budget,
    CallableCounter,
    Cursor,
    CursorSigner,
    Detail,
    Effect,
    InvalidCursor,
    OunceServer,
    PageWindow,
    Paginated,
    ResultTooLarge,
    ServerCost,
    TiktokenCounter,
    TokenCounter,
    ToolContext,
    ToolCost,
    get_counter,
    measure_tool,
    measure_tools,
    shape_result,
)
from ouncemcp.core.errors import (
    AmbiguousWrite,
    AuthenticationRequired,
    InvalidInput,
    NotFound,
    OunceError,
    PermissionDenied,
    RateLimited,
    TransientError,
)
from ouncemcp.discovery import DiscoveryDecision, RecallCase, RecallReport
from ouncemcp.execution import Limits, RetryPolicy, WriteAttempt, WriteResolver
from ouncemcp.execution.idempotency import Idempotency
from ouncemcp.execution.store import KeyValueStore, MemoryStore, OunceStore, RedisStore

__version__ = "0.0.1.dev0"

__all__ = [
    "AmbiguousWrite",
    "AuthenticationRequired",
    "Budget",
    "CallableCounter",
    "Cursor",
    "CursorSigner",
    "Detail",
    "DiscoveryDecision",
    "Effect",
    "Idempotency",
    "InvalidCursor",
    "InvalidInput",
    "KeyValueStore",
    "Limits",
    "MemoryStore",
    "NotFound",
    "OunceError",
    "OunceServer",
    "OunceStore",
    "PageWindow",
    "Paginated",
    "PermissionDenied",
    "RateLimited",
    "RecallCase",
    "RecallReport",
    "RedisStore",
    "ResultTooLarge",
    "RetryPolicy",
    "ServerCost",
    "TiktokenCounter",
    "TokenCounter",
    "ToolContext",
    "ToolCost",
    "TransientError",
    "WriteAttempt",
    "WriteResolver",
    "__version__",
    "get_counter",
    "measure_tool",
    "measure_tools",
    "shape_result",
]
