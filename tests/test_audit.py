"""AUD-1 to AUD-13: ``ouncemcp audit`` measures any MCP server over the protocol."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from conftest import EXAMPLE
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.audit import audit, compare
from ouncemcp.cli import main

INVENTORY = EXAMPLE / "server.py"


@pytest.mark.anyio
async def test_in_process_audit_matches_check_exactly(capsys):
    assert await anyio.to_thread.run_sync(main, ["check", str(INVENTORY), "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    report = await audit(str(INVENTORY))
    assert report.cost.total == checked["startup_tokens"]
    assert report.transport == "in-process"
    assert report.recommendation["mode"] == "direct"


@pytest.mark.anyio
async def test_stdio_audit_of_a_command_matches_check_within_one_percent(capfd):
    assert await anyio.to_thread.run_sync(main, ["check", str(INVENTORY), "--json"]) == 0
    checked = json.loads(capfd.readouterr().out)["startup_tokens"]
    report = await audit(f"{sys.executable} {INVENTORY}", inherit_env=True)
    assert report.transport == "stdio"
    assert abs(report.cost.total - checked) <= checked * 0.01
    assert report.protocol_version
    assert report.list_ms_p50 >= 0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Out(BaseModel):
    ok: bool = True


@pytest.mark.anyio
async def test_audits_an_authenticated_http_server():
    verifier = StaticTokenVerifier({"secret-token-123": {"client_id": "ci", "scopes": []}})
    server = OunceServer("secured", auth=verifier)

    @server.tool(name="ping", effect=Effect.READ)
    async def ping() -> Out:
        """Check the server."""
        return Out()

    port = free_port()
    async with anyio.create_task_group() as group:
        group.start_soon(
            lambda: server.fastmcp.run_http_async(
                host="127.0.0.1", port=port, show_banner=False, log_level="error"
            )
        )
        await anyio.sleep(1.0)
        url = f"http://127.0.0.1:{port}/mcp"
        with pytest.raises((RuntimeError, MCPError)):  # refused with 401
            await audit(url)
        report = await audit(url, bearer="secret-token-123")
        group.cancel_scope.cancel()
    assert [t.name for t in report.cost.tools] == ["ping"]
    assert report.transport == "http"


def bloated() -> FastMCP:
    mcp = FastMCP("bloated")
    shared = "This tool talks to the billing system and respects the rate limits of the API."

    @mcp.tool()
    def report(
        a: str = "",
        b: str = "",
        c: str = "",
        d: str = "",
        e: str = "",
        f: str = "",
        g: str = "",
        h: str = "",
        i: str = "",
        j: str = "",
        k: str = "",
        l: str = "",
        m: str = "",
    ) -> str:
        """Run a report. This tool talks to the billing system and respects the rate limits
        of the API."""
        return "ok"

    @mcp.tool()
    def lookup(country: str) -> str:
        return "ok"

    lookup.__doc__ = f"Look up a country. {shared}"
    mcp.tool(lambda region: "ok", name="regions", description=f"List regions. {shared}")
    mcp.tool(lambda x: "ok", name="essay", description="word " * 400)
    return mcp


@pytest.mark.anyio
async def test_bloat_findings(tmp_path: Path):
    path = tmp_path / "bloated.py"
    path.write_text("from tests_support import bloated\nmcp = bloated()\n")
    sys.modules["tests_support"] = sys.modules[__name__]
    report = await audit(f"{path}:mcp")
    rules = {(f.rule, f.tool) for f in report.findings}
    assert ("many-optional-fields", "report") in rules
    assert ("long-description", "essay") in rules


@pytest.mark.anyio
async def test_tools_are_only_called_with_explicit_samples(tmp_path: Path):
    calls: list[str] = []
    server = OunceServer("counted")

    @server.tool(name="rows.list", effect=Effect.READ)
    async def rows(n: int = 3) -> Out:
        """List rows."""
        calls.append("x")
        return Out()

    sys.modules["counted_support"] = type(sys)("counted_support")
    sys.modules["counted_support"].server = server  # type: ignore[attr-defined]
    path = tmp_path / "counted.py"
    path.write_text("from counted_support import server\n")
    await audit(f"{path}:server")
    assert calls == []
    report = await audit(f"{path}:server", samples={"rows.list": {"n": 2}}, sample_runs=4)
    assert len(calls) == 4
    assert report.samples["rows.list"]["runs"] == 4 and report.samples["rows.list"]["median"] > 0


def test_cli_reports_thresholds_and_regressions(tmp_path: Path, capsys):
    out = tmp_path / "old.json"
    assert main(["audit", str(INVENTORY), "--output", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Startup context" in text and "recommend direct" in text
    assert main(["audit", str(INVENTORY), "--fail-startup", "100"]) == 1
    old = json.loads(out.read_text())
    old["startup_tokens"] = 100
    old["tools"][0]["total"] = 10
    out.write_text(json.dumps(old))
    assert main(["audit", str(INVENTORY), "--compare", str(out), "--fail-on-regression"]) == 1
    assert "REGRESSION startup tokens grew" in capsys.readouterr().out
    assert main(["audit", str(INVENTORY), "--markdown"]) == 0
    assert "| Tool | Total |" in capsys.readouterr().out
    assert main(["audit", "definitely-not-a-command-xyz"]) == 2


def test_compare_ignores_small_changes():
    old: dict[str, Any] = {
        "startup_tokens": 1000,
        "tools": [{"name": "a", "total": 100}],
        "findings": [],
    }
    new: dict[str, Any] = {
        "startup_tokens": 1030,
        "tools": [{"name": "a", "total": 104}],
        "findings": [],
    }
    assert compare(old, new) == []
