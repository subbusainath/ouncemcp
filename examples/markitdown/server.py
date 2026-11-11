"""Migration done: markitdown-mcp rebuilt with OunceMCP (pilot 4, milestone M2).

The tool keeps its name, so hosts and prompts that use it keep working. What changed:

- **Bounded results.** The original returns the whole document, whatever its size. Now
  the result is a title, the document's size, an opening preview and a handle; the
  agent pages through the rest with ``handles.read`` (RES-5).
- **Safe fetching.** URLs go through ``check_url``: no loopback, private, link-local or
  cloud-metadata addresses (SEC-9). ``file:`` URIs are refused unless the server is
  started with ``MARKITDOWN_ALLOW_FILES=1``, and then only under ``MARKITDOWN_ROOT``.
- **Typed output and declared effect**, so the result matches an output schema and
  policies can rely on it being a read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import anyio
from converter import LazyConverter
from pydantic import BaseModel, Field

from ouncemcp import Effect, InvalidInput, OunceServer, ToolContext
from ouncemcp.security import check_url

PREVIEW_CHARS = 1_200
CHUNK_CHARS = 2_000


class Converted(BaseModel):
    title: str | None = None
    characters: int
    preview: str = Field(description="The start of the document")
    handle: str | None = Field(
        None, description="Read the full document in chunks with handles.read"
    )


def _check_file(uri: str) -> None:
    if os.environ.get("MARKITDOWN_ALLOW_FILES") != "1":
        raise InvalidInput(
            "file: URIs are disabled on this server.", hint="Use an http or https URL."
        )
    root = Path(os.environ.get("MARKITDOWN_ROOT", Path.cwd())).resolve()
    path = Path(unquote(urlsplit(uri).path)).resolve()
    if not path.is_relative_to(root):
        raise InvalidInput("That file is outside the folder this server may read.")


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    parts, current = [], ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > size:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
        while len(current) > size:
            parts.append(current[:size])
            current = current[size:]
    if current:
        parts.append(current)
    return parts


def build(converter: Any = None) -> OunceServer:
    server = OunceServer("markitdown", handles=True, deps=converter or LazyConverter())

    @server.tool(name="convert_to_markdown", effect=Effect.READ, deadline_ms=60_000)
    async def convert_to_markdown(uri: str, ctx: ToolContext) -> Converted:
        """Convert a resource described by an http:, https:, file: or data: URI to markdown.
        Returns a preview and a handle; read the rest with handles.read."""
        scheme = urlsplit(uri).scheme.lower()
        if scheme == "file":
            _check_file(uri)
        else:
            check_url(uri, schemes=("https", "http", "data"))
        document = await anyio.to_thread.run_sync(ctx.deps.convert_uri, uri)
        text = document.markdown or ""
        handle = await ctx.store_result(chunks(text)) if len(text) > PREVIEW_CHARS else None
        return Converted(
            title=getattr(document, "title", None),
            characters=len(text),
            preview=text[:PREVIEW_CHARS],
            handle=handle,
        )

    return server


server = build()

if __name__ == "__main__":
    server.run()
