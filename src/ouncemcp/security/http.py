"""Safe defaults for HTTP transports (spec SEC-8).

The MCP Streamable HTTP transport requires servers to validate ``Origin`` (to stop DNS
rebinding from a web page) and recommends binding local servers to loopback only.
FastMCP has the guard but leaves it off by default. OunceMCP turns it on, binds to
``127.0.0.1``, and refuses to listen on any other address unless the deployment names
the hostnames it serves (``allowed_hosts``) or explicitly turns the guard off.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

LOOPBACK_HOST = "127.0.0.1"


def is_loopback(host: str) -> bool:
    name = host.strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def resolve_http_options(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Fill in ``host`` and ``host_origin_protection`` and check they are safe together."""
    options = dict(kwargs)
    host = options.setdefault("host", LOOPBACK_HOST)
    protection = options.setdefault("host_origin_protection", True)
    if protection is False:
        return options
    if not is_loopback(str(host)) and not options.get("allowed_hosts"):
        raise ValueError(
            f"Refusing to serve HTTP on {host!r} without allowed_hosts. Name the hostnames "
            "clients use (allowed_hosts=['mcp.example.com']) and, for browser clients, "
            "allowed_origins, so Host and Origin can be checked (spec SEC-8). Pass "
            "host_origin_protection=False only behind a proxy that checks them."
        )
    return options
