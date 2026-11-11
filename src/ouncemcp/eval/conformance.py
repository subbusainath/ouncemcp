"""A conformance target server for the official MCP conformance suite (spec EVAL-1).

The suite (``@modelcontextprotocol/conformance``) calls fixture tools with fixed names
and checks the protocol answers. This module builds an ``OunceServer`` with the tool
fixtures that M1 supports, so the suite exercises OunceMCP's real call path: typed
outputs, shaping, error mapping, signed request state, Host and Origin checks.

Resource and prompt fixtures use ``server.resource`` and ``server.prompt`` (CAP-2).
Completions and tools that return image or audio content are not supported, so their
scenarios stay in ``conformance/expected-failures.yml``.

Run it with::

    python -m ouncemcp.eval.conformance --port 3001
    npx @modelcontextprotocol/conformance@0.2.0-alpha.12 server \\
        --url http://127.0.0.1:3001/mcp --spec-version 2026-07-28 \\
        --expected-failures conformance/expected-failures.yml
"""

from __future__ import annotations

import argparse
import base64
from typing import Any

import anyio
from fastmcp.prompts import Message
from mcp_types import EmbeddedResource, ImageContent, TextResourceContents
from pydantic import BaseModel

from ouncemcp.core.effects import Effect
from ouncemcp.core.server import OunceServer, ToolContext


class Text(BaseModel):
    text: str


def _ask(message: str, field: str) -> dict[str, Any]:
    return {
        "method": "elicitation/create",
        "params": {
            "message": message,
            "requestedSchema": {
                "type": "object",
                "properties": {field: {"type": "string"}},
                "required": [field],
            },
        },
    }


_CONFIRM = {
    "method": "elicitation/create",
    "params": {
        "message": "Please confirm",
        "requestedSchema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}
_ROOTS: dict[str, Any] = {"method": "roots/list", "params": {}}


def _sample(text: str, max_tokens: int) -> dict[str, Any]:
    return {
        "method": "sampling/createMessage",
        "params": {
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            "maxTokens": max_tokens,
        },
    }


def responses(ctx: ToolContext) -> dict[str, Any]:
    """The client's input responses as plain JSON, keyed like the requests."""
    raw = ctx.input_responses
    if not raw:
        return {}
    items = raw.items() if hasattr(raw, "items") else []
    out: dict[str, Any] = {}
    for key, value in items:
        if hasattr(value, "model_dump"):
            value = value.model_dump(by_alias=True, exclude_none=True)
        out[str(key)] = value
    return out


def _elicited(response: Any, field: str) -> str | None:
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, dict) and content.get(field) is not None:
            return str(content[field])
    return None


def build() -> OunceServer:
    """The conformance target. Every fixture is an ordinary OunceMCP tool."""
    server = OunceServer(
        "ouncemcp-conformance",
        instructions="Conformance fixtures for the MCP conformance suite.",
        discovery="direct",
    )

    @server.tool(name="test_simple_text", effect=Effect.READ)
    async def simple_text() -> Text:
        """Return a simple text response."""
        return Text(text="This is a simple text response for testing.")

    @server.tool(name="test_error_handling", effect=Effect.READ)
    async def error_handling() -> Text:
        """Always fail, to test error reporting."""
        from ouncemcp.core.errors import InvalidInput

        raise InvalidInput("This tool intentionally returns an error for testing")

    @server.tool(name="test_tool_with_progress", effect=Effect.READ)
    async def with_progress(ctx: ToolContext) -> Text:
        """Report progress 0, 50 and 100 of 100."""
        for step in (0, 50, 100):
            if ctx.fastmcp is not None:
                await ctx.fastmcp.report_progress(step, 100)
            if step < 100:
                await anyio.sleep(0.05)
        return Text(text="Progress reported.")

    @server.tool(name="greet", effect=Effect.READ)
    async def greet(name: str) -> Text:
        """Greet someone by name."""
        return Text(text=f"Hello, {name}!")

    @server.tool(name="test_input_required_result_elicitation", effect=Effect.READ)
    async def ask_name(ctx: ToolContext) -> Text:
        """Ask for the caller's name, then greet them."""
        name = _elicited(responses(ctx).get("user_name"), "name")
        if name is None:
            ctx.require_input({"user_name": _ask("What is your name?", "name")})
        return Text(text=f"Hello, {name}!")

    @server.tool(name="test_input_required_result_request_state", effect=Effect.READ)
    async def with_state(ctx: ToolContext) -> Text:
        """Ask for confirmation and round-trip signed request state."""
        state = ctx.resume_state()
        if state is None or "confirm" not in responses(ctx):
            ctx.require_input({"confirm": _CONFIRM}, state={"round": 1})
        return Text(text="state-ok: request state was signed, returned and verified.")

    @server.tool(name="test_input_required_result_tampered_state", effect=Effect.READ)
    async def tampered(ctx: ToolContext) -> Text:
        """Like the request-state fixture; a modified state is rejected."""
        state = ctx.resume_state()
        if state is None:
            ctx.require_input({"confirm": _CONFIRM}, state={"round": 1})
        return Text(text="state-ok")

    @server.tool(name="test_input_required_result_multi_round", effect=Effect.READ)
    async def multi_round(ctx: ToolContext) -> Text:
        """Ask for a name, then a colour, carrying state between rounds."""
        state = ctx.resume_state() or {}
        answers = responses(ctx)
        if state.get("round") is None:
            ctx.require_input(
                {"step1": _ask("Step 1: What is your name?", "name")}, state={"round": 1}
            )
        if state.get("round") == 1:
            name = _elicited(answers.get("step1"), "name")
            if name is None:
                ctx.require_input(
                    {"step1": _ask("Step 1: What is your name?", "name")}, state={"round": 1}
                )
            ctx.require_input(
                {"step2": _ask("Step 2: What is your favorite color?", "color")},
                state={"round": 2, "name": name},
            )
        color = _elicited(answers.get("step2"), "color")
        if color is None:
            ctx.require_input(
                {"step2": _ask("Step 2: What is your favorite color?", "color")}, state=state
            )
        return Text(text=f"{state.get('name')} likes {color}.")

    @server.tool(name="test_input_required_result_multiple_inputs", effect=Effect.READ)
    async def multiple(ctx: ToolContext) -> Text:
        """Ask for an elicitation, a sampling result and the roots at once."""
        wanted = {
            "user_name": _ask("What is your name?", "name"),
            "greeting": _sample("Generate a greeting", 50),
            "client_roots": _ROOTS,
        }
        answers = responses(ctx)
        missing = {k: v for k, v in wanted.items() if k not in answers}
        if ctx.resume_state() is None or missing:
            ctx.require_input(missing or wanted, state={"round": 1})
        return Text(text="Received all inputs.")

    @server.tool(name="test_input_required_result_sampling", effect=Effect.READ)
    async def sampling(ctx: ToolContext) -> Text:
        """Ask the client's model a question."""
        answer = responses(ctx).get("capital_question")
        if answer is None:
            ctx.require_input({"capital_question": _sample("What is the capital of France?", 100)})
        content = answer.get("content") if isinstance(answer, dict) else None
        text = content.get("text") if isinstance(content, dict) else str(content)
        return Text(text=f"The model said: {text}")

    @server.tool(name="test_input_required_result_list_roots", effect=Effect.READ)
    async def list_roots(ctx: ToolContext) -> Text:
        """Ask for the client's roots."""
        answer = responses(ctx).get("client_roots")
        if answer is None:
            ctx.require_input({"client_roots": _ROOTS})
        roots = answer.get("roots", []) if isinstance(answer, dict) else []
        uris = ", ".join(str(r.get("uri")) for r in roots if isinstance(r, dict))
        return Text(text=f"Roots: {uris or 'none'}")

    _resources_and_prompts(server)
    return server


# A 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class TemplateData(BaseModel):
    id: str
    templateTest: bool = True
    data: str


def _resources_and_prompts(server: OunceServer) -> None:
    @server.resource("test://static-text", mime_type="text/plain")
    def static_text() -> str:
        """A static text resource."""
        return "This is the content of the static text resource."

    @server.resource("test://static-binary", mime_type="image/png")
    def static_binary() -> bytes:
        """A static binary resource."""
        return PNG

    @server.resource("test://template/{id}/data", mime_type="application/json")
    def template_data(id: str) -> TemplateData:
        """Data for one id, from a template."""
        return TemplateData(id=id, data=f"Data for ID: {id}")

    @server.prompt("test_simple_prompt")
    def simple_prompt() -> str:
        """A simple prompt without arguments."""
        return "This is a simple prompt for testing."

    @server.prompt("test_prompt_with_arguments")
    def with_arguments(arg1: str, arg2: str) -> str:
        """A prompt with two required arguments."""
        return f"Prompt with arguments: arg1='{arg1}', arg2='{arg2}'"

    @server.prompt("test_prompt_with_embedded_resource")
    def with_resource(resourceUri: str) -> list[Message]:
        """A prompt that embeds a resource."""
        embedded = EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=resourceUri,
                mime_type="text/plain",
                text="Embedded resource content for testing.",
            ),
        )
        return [Message(embedded), Message("Please process the embedded resource above.")]

    @server.prompt("test_prompt_with_image")
    def with_image() -> list[Message]:
        """A prompt with an image."""
        image = ImageContent(
            type="image", data=base64.b64encode(PNG).decode(), mime_type="image/png"
        )
        return [Message(image), Message("Please analyze the image above.")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ouncemcp.eval.conformance")
    parser.add_argument("--port", type=int, default=3001)
    ns = parser.parse_args(argv)
    build().run("http", port=ns.port, check_budget=False, show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
