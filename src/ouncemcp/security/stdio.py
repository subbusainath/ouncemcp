"""Policies over stdio (proposal 0010, decision 3).

FastMCP skips component authorization on stdio: the server runs as the local user who
started it, so FastMCP trusts that user. ``OunceServer(stdio_policy=...)`` chooses:

- ``"trust"`` (default): FastMCP's behavior. Policies apply over HTTP and to in-process
  calls; over stdio every tool is visible and callable. ``ouncemcp check`` warns when a
  server has policies, so nobody assumes they apply locally.
- ``"enforce"``: over stdio, every tool's checks run with the anonymous caller (stdio
  carries no verified token). Denied tools are hidden from lists and search and refused
  by name exactly as over HTTP.

Approvals for safe writes are enforced on every transport in both modes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fastmcp.server.auth import AuthContext
from fastmcp.server.context import _current_transport
from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.authorization import run_auth_checks
from fastmcp.utilities.versions import VersionSpec

StdioPolicy = Literal["trust", "enforce"]
STDIO_POLICIES: tuple[StdioPolicy, ...] = ("trust", "enforce")


def on_stdio() -> bool:
    return _current_transport.get() == "stdio"


async def allowed_on_stdio(tool: Tool) -> bool:
    """Run the tool's checks as the anonymous caller. Any failure denies."""
    if tool.auth is None:
        return True
    try:
        return await run_auth_checks(tool.auth, AuthContext(token=None, component=tool))
    except Exception:  # noqa: BLE001 - a failing check must fail closed
        return False


class StdioPolicyFilter(Transform):
    """Applies tool checks on stdio, where FastMCP would skip them (``"enforce"``)."""

    def __init__(self, mode: StdioPolicy) -> None:
        if mode not in STDIO_POLICIES:
            raise ValueError(f"stdio_policy must be 'trust' or 'enforce', got {mode!r}")
        self.mode: StdioPolicy = mode

    def _active(self) -> bool:
        return self.mode == "enforce" and on_stdio()

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if not self._active():
            return tools
        return [tool for tool in tools if await allowed_on_stdio(tool)]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        if tool is None or not self._active():
            return tool
        return tool if await allowed_on_stdio(tool) else None
