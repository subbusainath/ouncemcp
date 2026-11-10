"""Search discovery: three stable meta-tools over FastMCP's BM25 search transform.

DISC-7: ranking, the auth-filtered catalog, and the transform plumbing come from
``fastmcp.server.transforms.search``. This module adds what OunceMCP requires on top:
detail levels (DISC-4), a ``describe_tool`` meta-tool, byte-stable meta-tool
definitions (DISC-5), and actionable errors that do not reveal unauthorized tools
(DISC-2).

Every catalog read goes through ``get_tool_catalog``, which FastMCP filters with each
tool's own ``auth`` check, so a tool the caller may not use cannot be found, described
or called through a meta-tool. Hiding a tool from ``tools/list`` is not what protects
it: the server's own call path applies the same check to guessed names (DISC-3).
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any

from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search.base import serialize_tools_for_output_json
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec
from mcp_types import TextContent

from ouncemcp.core.errors import InvalidInput, NotFound
from ouncemcp.discovery.modes import (
    CALL_TOOL,
    DESCRIBE_TOOL,
    META_TOOLS,
    SEARCH_TOOL,
    DetailLevel,
    DiscoveryMode,
)

SUMMARY_CHARS = 160

_NOT_FOUND_HINT = "Use search_tools to find tool names."


def _first_sentence(text: str | None, limit: int = SUMMARY_CHARS) -> str:
    """The first sentence of a description, cut to ``limit`` characters."""
    if not text:
        return ""
    flat = " ".join(text.split())
    end = flat.find(". ")
    sentence = flat if end == -1 else flat[: end + 1]
    if len(sentence) <= limit:
        return sentence
    return sentence[: limit - 1].rstrip() + "\u2026"


def _param_labels(tool: Tool) -> list[str]:
    """Parameter names, with ``?`` after the optional ones."""
    schema = tool.parameters or {}
    required = set(schema.get("required") or [])
    return [name if name in required else f"{name}?" for name in schema.get("properties") or {}]


def render_tool(tool: Tool, detail: DetailLevel) -> dict[str, Any]:
    """One tool at a detail level: ``name``, ``summary``, or ``full`` (as ``tools/list``)."""
    if detail == "name":
        return {"name": tool.name}
    if detail == "summary":
        return {
            "name": tool.name,
            "description": _first_sentence(tool.description),
            "params": _param_labels(tool),
        }
    if detail == "full":
        return serialize_tools_for_output_json([tool])[0]
    raise ValueError(f"Unknown detail level {detail!r}")  # pragma: no cover - schema enforces


def _text(data: Any) -> ToolResult:
    """Compact JSON text only. No structured content, so the meta-tools need no output
    schema and the agent is not sent every result twice."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return ToolResult(content=[TextContent(type="text", text=body)])


class OunceSearchTransform(BM25SearchTransform):
    """Replace the tool list with ``search_tools``, ``describe_tool`` and ``call_tool``.

    The transform only acts while ``await mode()`` returns ``"search"``. Pinned tools stay in
    the list after the meta-tools. The meta-tool definitions are built once and never
    mention the catalog, so they are byte-identical whatever tools the server has.
    """

    def __init__(
        self,
        *,
        mode: Callable[[], Awaitable[DiscoveryMode]],
        pinned: Sequence[str] = (),
        max_results: int = 5,
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=list(pinned),
            search_tool_name=SEARCH_TOOL,
            call_tool_name=CALL_TOOL,
        )
        self._mode = mode
        self._pinned = tuple(pinned)
        self._max = max_results
        self.meta_tools: tuple[Tool, ...] = (
            self._make_search_tool(),
            self._make_describe_tool(),
            self._make_call_tool(),
        )
        self._meta_by_name = {tool.name: tool for tool in self.meta_tools}
        assert tuple(self._meta_by_name) == META_TOOLS

    @property
    def max_results(self) -> int:
        return self._max

    @property
    def pinned(self) -> tuple[str, ...]:
        return self._pinned

    async def rank(self, tools: Sequence[Tool], query: str) -> list[Tool]:
        """Rank ``tools`` for ``query`` with the same index the search meta-tool uses."""
        return list(await self._search(tools, query))

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        if await self._mode() != "search":
            return tools
        by_name = {tool.name: tool for tool in tools}
        pinned = [by_name[name] for name in self._pinned if name in by_name]
        return [*self.meta_tools, *pinned]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name in self._meta_by_name and await self._mode() == "search":
            return self._meta_by_name[name]
        return await call_next(name, version=version)

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            query: Annotated[str, "What you want to do, in plain words"],
            detail: Annotated[
                DetailLevel,
                "name: names only. summary: name, one line, parameters. full: complete schema",
            ] = "summary",
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Find tools by what they do. Ranked by relevance. Call describe_tool for a
            full schema before using a tool."""
            visible = await transform._get_visible_tools(ctx)
            found = await transform.rank(visible, query)
            if not found:
                return _text(
                    {
                        "tools": [],
                        "detail": detail,
                        "hint": "No tool matched. Try different or fewer words.",
                    }
                )
            tools: list[Any] = (
                [tool.name for tool in found]
                if detail == "name"
                else [render_tool(tool, detail) for tool in found]
            )
            return _text({"tools": tools, "detail": detail})

        return Tool.from_function(fn=search_tools, name=SEARCH_TOOL)

    def _make_describe_tool(self) -> Tool:
        transform = self

        async def describe_tool(
            name: Annotated[str, "Exact tool name from search_tools"],
            detail: Annotated[DetailLevel, "name, summary, or full (default)"] = "full",
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Get one tool's definition, including its input schema."""
            catalog = await transform.get_tool_catalog(ctx)
            match = next((tool for tool in catalog if tool.name == name), None)
            if match is None:
                raise NotFound(f"Unknown tool '{name}'.", hint=_NOT_FOUND_HINT)
            return _text(render_tool(match, detail))

        return Tool.from_function(fn=describe_tool, name=DESCRIBE_TOOL)

    def _make_call_tool(self) -> Tool:
        transform = self

        async def call_tool(
            name: Annotated[str, "Exact tool name"],
            arguments: Annotated[dict[str, Any] | None, "Arguments from the tool's schema"] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Call a tool found with search_tools."""
            if name in META_TOOLS:
                raise InvalidInput(
                    f"'{name}' is a discovery tool.",
                    hint=f"Call it directly, not through {CALL_TOOL}.",
                )
            catalog = await transform.get_tool_catalog(ctx)
            if not any(tool.name == name for tool in catalog):
                raise NotFound(f"Unknown tool '{name}'.", hint=_NOT_FOUND_HINT)
            return await ctx.fastmcp.call_tool(name, arguments)

        return Tool.from_function(fn=call_tool, name=CALL_TOOL)
