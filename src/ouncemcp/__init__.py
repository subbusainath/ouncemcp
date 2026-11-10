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
from ouncemcp.discovery import DiscoveryDecision, RecallCase, RecallReport

__version__ = "0.0.1.dev0"

__all__ = [
    "Budget",
    "CallableCounter",
    "Cursor",
    "CursorSigner",
    "Detail",
    "DiscoveryDecision",
    "Effect",
    "InvalidCursor",
    "OunceServer",
    "PageWindow",
    "Paginated",
    "RecallCase",
    "RecallReport",
    "ResultTooLarge",
    "ServerCost",
    "TiktokenCounter",
    "TokenCounter",
    "ToolContext",
    "ToolCost",
    "__version__",
    "get_counter",
    "measure_tool",
    "measure_tools",
    "shape_result",
]
