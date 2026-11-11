"""Reference client broker: many servers as one, without context bloat (spec section 8.3).

See ``ouncemcp.client.broker`` and proposal 0016.
"""

from ouncemcp.client.broker import (
    META_DEFINITIONS,
    Broker,
    BrokerError,
    BrokerResult,
    BrokerTool,
    CrossServerCall,
)

__all__ = [
    "META_DEFINITIONS",
    "Broker",
    "BrokerError",
    "BrokerResult",
    "BrokerTool",
    "CrossServerCall",
]
