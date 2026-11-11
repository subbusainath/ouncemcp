"""EVAL-1: the conformance target behaves as the official suite's fixtures require.

The suite itself runs in CI (``.github/workflows/conformance.yml``). These tests drive
the same fixtures through FastMCP's client, which answers ``input_required`` rounds
with the handlers below, so the multi-round flows are checked end to end in-process.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from mcp_types import CreateMessageResult, TextContent

from ouncemcp.eval.conformance import build


async def elicit(message: str, response_type: Any, params: Any, context: Any) -> dict[str, Any]:
    props = params.requested_schema.get("properties", {})
    if "name" in props:
        return {"name": "Ada"}
    if "color" in props:
        return {"color": "teal"}
    return {"ok": True}


async def sample(messages: Any, params: Any, context: Any) -> CreateMessageResult:
    return CreateMessageResult(
        role="assistant", content=TextContent(type="text", text="Paris"), model="fake"
    )


def client(server: Any) -> Client:
    return Client(
        server.fastmcp,
        elicitation_handler=elicit,
        sampling_handler=sample,
        roots=["file:///workspace"],
    )


@pytest.fixture
def server():
    return build()


@pytest.mark.anyio
async def test_simple_text_and_error(server):
    async with client(server) as c:
        ok = await c.call_tool("test_simple_text", {})
        bad = await c.call_tool("test_error_handling", {}, raise_on_error=False)
    assert "This is a simple text response for testing." in ok.content[0].text
    assert bad.is_error
    assert "intentionally returns an error" in bad.content[0].text


@pytest.mark.anyio
async def test_progress_notifications(server):
    seen: list[tuple[float, float | None]] = []

    async def progress(value: float, total: float | None, message: str | None) -> None:
        seen.append((value, total))

    async with client(server) as c:
        await c.call_tool("test_tool_with_progress", {}, progress_handler=progress)
    assert seen == [(0, 100), (50, 100), (100, 100)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("test_input_required_result_elicitation", "Hello, Ada!"),
        ("test_input_required_result_request_state", "state-ok"),
        ("test_input_required_result_multi_round", "Ada likes teal."),
        ("test_input_required_result_multiple_inputs", "Received all inputs."),
        ("test_input_required_result_sampling", "Paris"),
        ("test_input_required_result_list_roots", "file:///workspace"),
    ],
)
async def test_input_required_flows_complete(server, tool, expected):
    async with client(server) as c:
        result = await c.call_tool(tool, {})
    assert not result.is_error
    assert expected in result.structured_content["text"]


@pytest.mark.anyio
async def test_first_round_returns_input_required_with_signed_state(server):
    result = await server.fastmcp.call_tool("test_input_required_result_request_state", {})
    assert result.input_required.input_requests["confirm"]
    assert result.input_required.request_state


@pytest.mark.anyio
async def test_tool_names_and_order_are_stable(server):
    async with client(server) as c:
        first = [t.name for t in await c.list_tools()]
        second = [t.name for t in await c.list_tools()]
    assert first == second
    assert "test_simple_text" in first


@pytest.mark.anyio
async def test_resource_and_prompt_fixtures(server):
    async with client(server) as c:
        (text,) = await c.read_resource("test://static-text")
        (blob,) = await c.read_resource("test://static-binary")
        (data,) = await c.read_resource("test://template/123/data")
        simple = await c.get_prompt("test_simple_prompt")
        args = await c.get_prompt("test_prompt_with_arguments", {"arg1": "hello", "arg2": "world"})
        embedded = await c.get_prompt(
            "test_prompt_with_embedded_resource", {"resourceUri": "test://x"}
        )
        image = await c.get_prompt("test_prompt_with_image")
        listed = await c.list_resources()
    assert text.text == "This is the content of the static text resource."
    assert text.mime_type == "text/plain"
    assert blob.mime_type == "image/png" and blob.blob
    assert data.text == '{"id":"123","templateTest":true,"data":"Data for ID: 123"}'
    assert data.mime_type == "application/json"
    assert simple.messages[0].content.text == "This is a simple prompt for testing."
    assert args.messages[0].content.text == "Prompt with arguments: arg1='hello', arg2='world'"
    assert embedded.messages[0].content.type == "resource"
    assert image.messages[0].content.type == "image"
    assert all(r.description for r in listed)
