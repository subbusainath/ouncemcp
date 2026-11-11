"""M2 additions: ouncemcp.toml, health and readiness, per-tool metrics, description lints."""

from __future__ import annotations

from pathlib import Path

import httpx2 as httpx
import pytest
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.core.config import ConfigError, load_config
from ouncemcp.core.lints import lint_description
from ouncemcp.execution.store import MemoryStore


class Out(BaseModel):
    ok: bool = True


CONFIG = """
[budget]
tool_definition_warn = 400
startup_warn = 3000
result_default = 1500

[limits]
max_concurrency = 4

[discovery]
mode = "search"
pinned = ["notes.read"]

[tokenizer]
default = "cl100k_base"

[output]
schemas = "auto"

[security]
http_bind = "0.0.0.0"
stdio_policy = "enforce"
audience = "https://mcp.example.com"
"""


def test_config_file_sets_server_options(tmp_path: Path):
    path = tmp_path / "ouncemcp.toml"
    path.write_text(CONFIG)
    kwargs = load_config(path)
    assert kwargs["budget"].startup_tokens == 3000 and kwargs["budget"].result_tokens == 1500
    assert kwargs["limits"].max_concurrency == 4
    server = OunceServer.from_config("notes", path, output_schemas="on")

    @server.tool(name="notes.read", effect=Effect.READ)
    async def read() -> Out:
        """Read notes."""
        return Out()

    assert server.discovery_setting == "search"
    assert server.counter.name == "cl100k_base"
    assert server.output_schemas == "on"  # the keyword argument wins
    assert server.stdio_policy == "enforce" and server.audiences == ("https://mcp.example.com",)
    assert server.http_host == "0.0.0.0"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[budgets]\nx = 1\n", "unknown section"),
        ("[budget]\nresult_tokenz = 1\n", "unknown key"),
        ("[budget\n", "Expected"),
    ],
)
def test_config_errors_name_the_problem(tmp_path: Path, text: str, match: str):
    path = tmp_path / "ouncemcp.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=match):
        load_config(path)
    with pytest.raises(ConfigError, match="no such file"):
        load_config(tmp_path / "missing.toml")


async def get(server: OunceServer, path: str) -> httpx.Response:
    app = server.http_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_health_and_readiness_routes():
    ready = {"db": True}

    def database() -> bool:
        return ready["db"]

    server = OunceServer("svc", readiness=[database])
    health = await get(server, "/healthz")
    assert health.status_code == 200 and health.json() == {"status": "ok", "server": "svc"}
    assert (await get(server, "/readyz")).status_code == 200
    ready["db"] = False
    failing = await get(server, "/readyz")
    assert failing.status_code == 503 and failing.json()["failing"] == ["database"]


@pytest.mark.anyio
async def test_readiness_fails_when_the_store_does():
    class Broken(MemoryStore):
        async def put(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise ConnectionError("redis down")

    server = OunceServer("svc", store=Broken())
    assert await server.check_ready() == ["store"]


@pytest.mark.anyio
async def test_metrics_per_tool_and_prometheus_route():
    server = OunceServer("svc", metrics=True)

    @server.tool(name="rows.list", effect=Effect.READ)
    async def rows() -> Out:
        """List rows."""
        return Out()

    @server.tool(name="rows.fail", effect=Effect.READ)
    async def fail() -> Out:
        """Always fail."""
        raise RuntimeError("boom")

    for _ in range(3):
        await server.call_tool("rows.list")
    await server.call_tool("rows.fail")
    snap = server.metrics.snapshot()  # type: ignore[union-attr]
    assert snap["rows.list"]["calls"] == 3 and snap["rows.list"]["result_tokens_total"] > 0
    assert snap["rows.fail"]["errors"] == {"internal": 1}
    text = (await get(server, "/metrics")).text
    assert 'ouncemcp_tool_calls_total{tool="rows.list"} 3' in text
    assert 'ouncemcp_tool_errors_total{tool="rows.fail",category="internal"} 1' in text
    assert (await get(OunceServer("plain"), "/metrics")).status_code == 404


@pytest.mark.parametrize(
    ("description", "effect", "siblings", "rules"),
    [
        (
            "Drop it.",
            Effect.DESTRUCTIVE,
            1,
            ["description-too-short", "write-without-side-effects"],
        ),
        ("Find tickets by status and assignee.", Effect.READ, 3, ["missing-usage-guidance"]),
        ("Find tickets by status. Use tickets.get for details.", Effect.READ, 3, []),
        ("Close a ticket. Side effect: notifies the requester.", Effect.WRITE, 1, []),
    ],
)
def test_description_lints(description, effect, siblings, rules):
    found = [f.rule for f in lint_description("t", description, effect=effect, siblings=siblings)]
    assert found == rules
