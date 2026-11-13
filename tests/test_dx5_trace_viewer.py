"""DX-4 and DX-5: OpenTelemetry export, the trace report and the context results view."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import EXAMPLE
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from ouncemcp import Budget, Effect, OunceServer
from ouncemcp.cli import main
from ouncemcp.trace import JsonlSink, MemorySink
from ouncemcp.trace.otel import OTelSink
from ouncemcp.trace.report import load_traces, render_html, summarize


class Row(BaseModel):
    id: str
    note: str = ""


class Rows(BaseModel):
    items: list[Row]


def build(*sinks: object) -> OunceServer:
    server = OunceServer("t", trace=list(sinks), budget=Budget(result_tokens=300))

    @server.tool(name="rows.list", effect=Effect.READ)
    async def rows(count: int = 3) -> Rows:
        """List rows."""
        return Rows(items=[Row(id=f"r{i}", note="x" * 40) for i in range(count)])

    @server.tool(name="rows.fail", effect=Effect.READ)
    async def fail() -> Rows:
        """Always fail."""
        raise RuntimeError("secret at /etc/app")

    return server


@pytest.mark.anyio
async def test_traces_say_what_shaping_removed_without_values():
    sink = MemorySink()
    server = build(sink)
    await server.call_tool("rows.list", {"count": 200})
    (trace,) = sink.traces
    assert trace.truncated
    assert trace.truncation["lists"]["items"]["total"] == 200
    assert trace.truncation["lists"]["items"]["kept"] < 200
    assert "xxxx" not in json.dumps(trace.to_dict())


@pytest.mark.anyio
async def test_otel_spans_carry_the_call():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    server = build(OTelSink(provider.get_tracer("test")))
    await server.call_tool("rows.list", {"count": 2})
    await server.call_tool("rows.fail")
    ok, failed = exporter.get_finished_spans()
    assert ok.name == "tools/call rows.list"
    assert ok.attributes["ouncemcp.result_tokens"] > 0
    assert ok.attributes["ouncemcp.effect"] == "read"
    assert ok.end_time > ok.start_time
    assert failed.status.status_code == StatusCode.ERROR
    assert failed.attributes["ouncemcp.error.category"] == "internal"
    assert "/etc/app" not in json.dumps(dict(failed.attributes))


@pytest.mark.anyio
async def test_trace_report_from_jsonl(tmp_path: Path, capsys):
    path = tmp_path / "calls.jsonl"
    server = build(JsonlSink(path))
    for count in (1, 2, 200):
        await server.call_tool("rows.list", {"count": count})
    await server.call_tool("rows.fail")
    traces = load_traces(path)
    summary = summarize(traces)
    assert summary["calls"] == 4 and summary["errors"] == 1 and summary["truncated"] == 1
    assert summary["tools"]["rows.list"]["calls"] == 3
    page = render_html(traces, title="t</script><b>x")
    assert page.startswith("<!DOCTYPE html>")
    assert "<b>x" not in page and "t&lt;/script&gt;" in page
    assert '"tool": "rows.fail"' in page


def test_trace_command(tmp_path: Path, capsys):
    path = tmp_path / "calls.jsonl"
    path.write_text(
        json.dumps(
            {
                "tool": "a.b",
                "outcome": "ok",
                "started_at": 1.0,
                "duration_ms": 2.5,
                "result_tokens": 40,
                "tokenizer": "o200k_base",
                "budget": 2000,
            }
        )
        + "\nnot json\n"
    )
    assert main(["trace", str(path)]) == 0
    out = capsys.readouterr()
    assert "1 calls, 0 errors" in out.out and "a.b" in out.out
    assert (tmp_path / "calls.jsonl.html").is_file()
    assert main(["trace", str(path), "--json", "--no-html"]) == 0
    assert json.loads(capsys.readouterr().out)["calls"] == 1
    assert main(["trace", str(tmp_path / "missing.jsonl")]) == 2


def test_context_call_shows_the_result_as_the_model_receives_it(capsys):
    args = json.dumps({"region": "ap-south-1", "kind": "vm", "status": "running"})
    code = main(["context", str(EXAMPLE / "server.py"), "inventory.search", "--call", args])
    out = capsys.readouterr()
    assert code == 0
    assert "--- result content (what the model reads) ---" in out.out
    assert "vm-00040" in out.out
    assert "result: ok" in out.err and "counted with o200k_base" in out.err
    assert main(["context", str(EXAMPLE / "server.py"), "inventory.get", "--call", "{bad"]) == 2
