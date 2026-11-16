"""Regression tests for the November 2026 security audit.

Each test is an attack that worked, or a limit that was missing, before the fix.
"""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel, Field

from ouncemcp import Effect, MemoryStore, NotFound, OunceServer, ToolContext
from ouncemcp.audit.runner import display_target
from ouncemcp.core.errors import MAX_REDACT_CHARS, redact
from ouncemcp.execution.middleware import UNKNOWN_TOOL
from ouncemcp.security.urls import URLNotAllowed, check_url
from ouncemcp.trace import CallTrace, redact_text
from ouncemcp.trace.metrics import OVERFLOW, MetricsSink


class Out(BaseModel):
    ok: bool = True


# ------------------------------------------------- redaction cannot stall the server

HOSTILE = ["a.", "1.", "a-.", "a@", "1-", "/a", "a://", "a+", "x.com "]


@pytest.mark.parametrize("unit", HOSTILE)
def test_redaction_is_fast_on_long_hostile_text(unit: str):
    text = unit * (200_000 // len(unit))
    started = time.perf_counter()
    redact(text)
    redact_text(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.anyio
async def test_an_echoed_64kb_argument_does_not_block_the_server():
    server = OunceServer("s")

    @server.tool(name="books.get", effect=Effect.READ)
    def get(id: str = Field(description="Book id from books.search")) -> Out:
        """Get one book by its id. Use books.search to find ids."""
        raise NotFound(f"No book with id '{id}'.")

    started = time.perf_counter()
    result = await server.call_tool("books.get", {"id": "a." * 30_000})
    assert result.is_error and time.perf_counter() - started < 2.0


def test_redaction_still_removes_secrets_after_the_scan_limit_is_applied():
    padding = "word " * (MAX_REDACT_CHARS // 5 - 40)
    text = f"{padding}password=hunter2 at db1.prod.internal " + "tail " * 5_000
    cleaned = redact(text)
    assert "hunter2" not in cleaned and "prod.internal" not in cleaned
    # A secret that would straddle the limit is dropped, never left half visible.
    straddling = "x " * (MAX_REDACT_CHARS // 2 - 6) + "sk-" + "A" * 400 + " tail"
    assert "sk-" not in redact(straddling)


def test_redaction_patterns_still_match_what_they_did():
    assert redact("see db1.prod.internal:5432 and https://u:p@x.io/a") == "see <host> and <url>"
    assert redact_text("mail a.b@x.org or +1 415 555 0100") == "mail <email> or <phone>"


# ------------------------------------------------- SSRF: IPv4 inside IPv6


@pytest.mark.parametrize(
    "url",
    [
        "http://[::127.0.0.1]/",  # IPv4-compatible loopback
        "http://[64:ff9b::7f00:1]/",  # NAT64 to 127.0.0.1
        "http://[64:ff9b::a9fe:a9fe]/",  # NAT64 to the metadata address
        "http://[64:ff9b::a00:1]/",  # NAT64 to 10.0.0.1
        "http://[64:ff9b:1::c0a8:1]/",  # local-use NAT64 to 192.168.0.1
        "http://[2002:7f00:1::]/",  # 6to4 to 127.0.0.1
    ],
)
def test_ipv6_addresses_that_carry_a_private_ipv4_address_are_refused(url: str):
    with pytest.raises(URLNotAllowed):
        check_url(url)


def test_public_addresses_are_still_allowed():
    assert check_url("http://[2606:4700:4700::1111]/")
    assert check_url("http://[64:ff9b::808:808]/")  # NAT64 to a public address
    assert check_url("http://8.8.8.8/")


# ------------------------------------------------- callers cannot mint metric series


@pytest.mark.anyio
async def test_made_up_tool_names_share_one_metric_series():
    server = OunceServer("s", metrics=True)

    @server.tool(name="notes.count", effect=Effect.READ)
    def count() -> Out:
        """Count the notes. Returns ok when the count worked."""
        return Out()

    await server.call_tool("notes.count", {})
    for i in range(200):
        await server.call_tool(f"made-up-{i}", {})
    snapshot = server.metrics.snapshot()
    assert set(snapshot) == {"notes.count", UNKNOWN_TOOL}
    assert snapshot[UNKNOWN_TOOL]["calls"] == 200


def test_the_metrics_sink_is_bounded():
    sink = MetricsSink(max_tools=3)
    for i in range(50):
        sink.emit(
            CallTrace(
                tool=f"t{i}",
                outcome="ok",
                started_at=0.0,
                duration_ms=1.0,
                result_tokens=1,
                tokenizer="o200k_base",
            )
        )
    assert len(sink.snapshot()) == 4 and sink.snapshot()[OVERFLOW]["calls"] == 47


# ------------------------------------------------- the in-memory store is bounded


@pytest.mark.anyio
async def test_memory_store_drops_expired_records_that_are_never_read_again():
    now = [0.0]
    store = MemoryStore(clock=lambda: now[0])
    for i in range(100):
        await store.put("ns", f"k{i}", {"v": i}, ttl_s=10)
    now[0] = 100.0
    await store.put("ns", "fresh", {"v": 1}, ttl_s=10)
    assert len(store._data) == 1


@pytest.mark.anyio
async def test_memory_store_never_grows_past_its_limit():
    store = MemoryStore(max_entries=10)
    for i in range(500):
        await store.put("ns", f"k{i}", {"v": i}, ttl_s=1_000 + i)
    assert len(store._data) == 10
    assert await store.get("ns", "k499") == {"v": 499}  # the longest-lived are kept


# ------------------------------------------------- an approval is used once


@pytest.mark.anyio
async def test_an_approved_continuation_token_works_once():
    from ouncemcp.security import InvalidState

    server = OunceServer("s")
    ctx = ToolContext(
        tool="t",
        response_format="concise",
        result_budget=100,
        _signer=server.cursors,
        _state=server.state,
        _store=server.store,
        _arguments=dict,
        _resolve=lambda c: "alice",
    )
    token = ctx.issue_state("input_required", {"ouncemcp_approved": True}, request=["t", {}])
    data = await ctx.consume_state(token, "input_required", request=["t", {}])
    assert data["ouncemcp_approved"] is True
    with pytest.raises(InvalidState):
        await ctx.consume_state(token, "input_required", request=["t", {}])


# ------------------------------------------------- reports do not keep URL credentials


def test_audit_reports_leave_out_url_credentials_and_query_strings():
    shown = display_target("https://user:s3cret@mcp.example.com:8443/mcp?token=abc123#x")
    assert shown == "https://mcp.example.com:8443/mcp"
    assert display_target("examples/inventory/server.py") == "examples/inventory/server.py"
