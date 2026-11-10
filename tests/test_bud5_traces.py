"""BUD-5 and SEC-11: per-call traces with token usage, redacted by default."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from pydantic import BaseModel

from ouncemcp import Budget, Effect, OunceServer, ToolContext, TransientError
from ouncemcp.discovery import CALL_TOOL
from ouncemcp.testing import as_caller
from ouncemcp.trace import CallbackSink, JsonlSink, MemorySink, Tracer, redact_value


class Row(BaseModel):
    id: str
    note: str = ""


class Rows(BaseModel):
    items: list[Row]


def build(sink: Any, **kwargs: Any) -> OunceServer:
    server = OunceServer("t", trace=sink, budget=Budget(result_tokens=300), **kwargs)
    failures = {"n": 0}

    @server.tool(name="rows.list", effect=Effect.READ)
    async def rows(count: int = 3, email: str = "", session_cookie: str = "") -> Rows:
        """List rows."""
        return Rows(items=[Row(id=f"r{i}", note="x" * 40) for i in range(count)])

    @server.tool(name="rows.flaky", effect=Effect.READ)
    async def flaky(ctx: ToolContext) -> Rows:
        """Fail once, then succeed."""
        failures["n"] += 1
        if failures["n"] == 1:
            raise TransientError("blip")
        return Rows(items=[])

    @server.tool(name="rows.broken", effect=Effect.WRITE)
    async def broken() -> Rows:
        """Always fails."""
        raise RuntimeError("db at 10.0.0.5 password=hunter2")

    return server


@pytest.mark.anyio
async def test_each_call_records_result_tokens_as_the_model_receives_them():
    sink = MemorySink()
    server = build(sink)
    result = await server.call_tool("rows.list", {"count": 2})
    (trace,) = sink.traces
    assert trace.tool == "rows.list"
    assert trace.outcome == "ok"
    assert trace.result_tokens == result.meta["ouncemcp"]["result_tokens"] > 0
    assert trace.tokenizer == "o200k_base"
    assert trace.budget == 300
    assert trace.truncated is False
    assert trace.effect == "read"
    assert trace.duration_ms >= 0


@pytest.mark.anyio
async def test_truncation_retries_and_errors_are_recorded():
    sink = MemorySink()
    server = build(sink)
    await server.call_tool("rows.list", {"count": 200})
    await server.call_tool("rows.flaky")
    await server.call_tool("rows.broken")
    big, flaky, broken = sink.traces
    assert big.truncated and big.result_tokens <= 300
    assert flaky.outcome == "ok" and flaky.attempts == 2
    assert broken.outcome == "error"
    assert broken.error_category == "internal"
    assert broken.error_code == "internal_error"
    assert broken.result_tokens > 0


@pytest.mark.anyio
async def test_proxied_calls_are_traced_with_their_parent():
    sink = MemorySink()
    server = build(sink, discovery="search")
    async with Client(server.fastmcp) as client:
        await client.call_tool(CALL_TOOL, {"name": "rows.list", "arguments": {"count": 1}})
    inner, outer = sink.traces
    assert inner.tool == "rows.list" and inner.parent == CALL_TOOL
    assert outer.tool == CALL_TOOL and outer.parent is None


@pytest.mark.anyio
async def test_traces_hold_no_raw_principal_arguments_or_messages_by_default():
    lines: list[dict[str, Any]] = []
    server = build(CallbackSink(lambda t: lines.append(t.to_dict())))
    with as_caller(subject="alice@example.com"):
        await server.call_tool(
            "rows.list", {"count": 1, "email": "alice@example.com", "session_cookie": "sk-abc"}
        )
        await server.call_tool("rows.broken")
    text = json.dumps(lines)
    assert "alice" not in text
    assert "sk-abc" not in text
    assert "hunter2" not in text and "10.0.0.5" not in text
    assert "arguments" not in lines[0]
    assert lines[0]["principal"].startswith("p:")


@pytest.mark.anyio
async def test_arguments_are_redacted_when_recorded():
    sink = MemorySink()
    server = build(Tracer(sink, arguments=True, principals="omit"))
    await server.call_tool(
        "rows.list", {"count": 1, "email": "bob@corp.example", "session_cookie": "sk-live-123"}
    )
    (trace,) = sink.traces
    assert trace.arguments == {"count": 1, "email": "<email>", "session_cookie": "<redacted>"}
    assert trace.principal is None


def test_redact_value_scrubs_nested_data_and_bounds_size():
    value = {
        "user": {"phone": "+1 (415) 555-0100", "password": "pw", "note": "see http://x.internal/a"},
        "list": list(range(30)),
        "deep": {"a": {"b": {"c": {"d": 1}}}},
    }
    out = redact_value(value)
    assert out["user"] == {"phone": "<phone>", "password": "<redacted>", "note": "see <url>"}
    assert len(out["list"]) == 21
    assert out["deep"]["a"]["b"]["c"] == "<nested>"


@pytest.mark.anyio
async def test_hashed_principals_are_stable_and_distinct():
    sink = MemorySink()
    server = build(sink)
    for subject in ("a", "a", "b"):
        with as_caller(subject=subject):
            await server.call_tool("rows.list", {"count": 1})
    a1, a2, b = (t.principal for t in sink.traces)
    assert a1 == a2 != b


@pytest.mark.anyio
async def test_jsonl_sink_and_failing_sinks(tmp_path: Path):
    path = tmp_path / "trace.jsonl"

    class Boom:
        def emit(self, trace: Any) -> None:
            raise OSError("disk full")

    server = build([Boom(), JsonlSink(path)])
    result = await server.call_tool("rows.list", {"count": 1})
    assert not result.is_error
    (line,) = path.read_text().splitlines()
    assert json.loads(line)["tool"] == "rows.list"


def test_bad_sinks_are_rejected():
    with pytest.raises(TypeError, match="emit"):
        OunceServer("t", trace=[object()])  # type: ignore[list-item]


@pytest.mark.anyio
async def test_no_tracer_means_no_overhead_path():
    server = build(None)
    assert server.tracer is None
    assert not (await server.call_tool("rows.list", {"count": 1})).is_error
