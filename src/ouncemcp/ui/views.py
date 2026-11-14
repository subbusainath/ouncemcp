"""Data for the context and audit pages (spec DX-4, AUD-10; proposal 0024).

The page app (``static/report.html``) renders a ``kind`` of data. This module builds the
``context`` kind, every tool definition exactly as MCP clients receive it with its token
breakdown, and the ``audit`` kind, from an ``AuditReport``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from ouncemcp.core.budget import Budget, BudgetOverride
from ouncemcp.core.costs import counting_scope, definition_payload, measure_tool
from ouncemcp.core.effects import is_parallel_safe
from ouncemcp.core.tokens import TokenCounter, compact_json


async def call_result(
    server: Any, tool: str, arguments: Mapping[str, Any], counter: TokenCounter
) -> dict[str, Any]:
    """Call ``tool`` in-process and describe the result as the model receives it."""
    from fastmcp import Client

    async with Client(getattr(server, "fastmcp", server)) as client:
        result = await client.call_tool_mcp(tool, dict(arguments))
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    return {
        "tool": tool,
        "arguments": dict(arguments),
        "ok": not result.is_error,
        "content": text,
        "content_tokens": counter.count(text),
        "structured": result.structured_content,
        "meta": (result.meta or {}).get("ouncemcp"),
    }


def context_data(
    tools: Sequence[Any],
    budget: Budget,
    counter: TokenCounter,
    *,
    title: str,
    overrides: Mapping[str, BudgetOverride] | None = None,
    output_schema_modes: Mapping[str, str] | None = None,
    discovery: Mapping[str, Any] | None = None,
    results: Sequence[Mapping[str, Any]] = (),
    focus: str | None = None,
) -> dict[str, Any]:
    """The ``context`` page: each definition as clients receive it, and what it costs."""
    rows = []
    for tool in tools:
        payload = definition_payload(tool)
        name = str(payload.get("name", "?"))
        cost = measure_tool(
            tool,
            budget,
            counter,
            (output_schema_modes or {}).get(name),
            (overrides or {}).get(name),
        )
        wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        rows.append(
            {
                **{k: v for k, v in asdict(cost).items()},
                "other": cost.name_tokens + cost.structure,
                "counted": payload,
                "counted_json": compact_json(payload),
                "not_counted": {k: v for k, v in wire.items() if k not in payload},
                "parallel_safe": is_parallel_safe(tool),
            }
        )
    rows.sort(key=lambda r: -int(r["total"]))
    return {
        "kind": "context",
        "title": title,
        "tokenizer": counter.name,
        "budget": asdict(budget),
        "counting_scope": counting_scope(),
        "startup_tokens": sum(int(r["total"]) for r in rows),
        "discovery": dict(discovery) if discovery else None,
        "tools": rows,
        "results": [dict(r) for r in results],
        "focus": focus,
    }


def audit_data(report: Mapping[str, Any], *, title: str) -> dict[str, Any]:
    """The ``audit`` page, from ``AuditReport.to_dict()`` (plus ``regressions``)."""
    data = json.loads(json.dumps(dict(report), default=str))
    return {"kind": "audit", "title": title, **data}
