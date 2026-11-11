"""The markitdown-mcp server as published, re-created for the migration pilot.

Upstream (https://github.com/microsoft/markitdown, MIT, markitdown-mcp 0.0.1a8) defines
one tool with the MCP Python SDK's FastMCP-style ``MCPServer`` decorator:

    @mcp.tool()
    async def convert_to_markdown(uri: str) -> str:
        "Convert a resource described by an http:, https:, file: or data: URI to markdown"
        return MarkItDown(...).convert_uri(uri).markdown

This file re-creates that tool on FastMCP with the same name, description and
behavior (it returns the whole document and fetches any URI it is given), so the
migration steps can be measured without depending on the upstream package.
"""

from __future__ import annotations

from typing import Any

from converter import LazyConverter
from fastmcp import FastMCP


def build(converter: Any = None) -> FastMCP:
    mcp = FastMCP("markitdown")
    convert = converter or LazyConverter()

    @mcp.tool()
    async def convert_to_markdown(uri: str) -> str:
        """Convert a resource described by an http:, https:, file: or data: URI to markdown"""
        return convert.convert_uri(uri).markdown

    return mcp


mcp = build()

if __name__ == "__main__":
    mcp.run()
