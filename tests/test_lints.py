"""DX-3 and SEC-7: policy and safety lints reported by ``ouncemcp check``."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from pydantic import BaseModel

from ouncemcp import Effect, OunceServer, ToolContext
from ouncemcp.cli import main
from ouncemcp.core.lints import lint_server
from ouncemcp.security import require_scopes


class Out(BaseModel):
    ok: bool = True


CONFIRM: dict = {}


def rules(server: OunceServer) -> list[tuple[str, str]]:
    return [(f.rule, f.tool) for f in lint_server(server)]


def test_input_after_effect_is_an_error_unless_replay_safe():
    server = OunceServer("s")

    @server.tool(name="pay.send", effect=Effect.WRITE, policy=require_scopes("pay"))
    async def send(ctx: ToolContext) -> Out:
        """Send a payment."""
        ctx.effect_applied()
        ctx.require_input(CONFIRM)
        return Out()

    @server.tool(name="pay.safe", effect=Effect.WRITE, replay_safe=True)
    async def safe(ctx: ToolContext) -> Out:
        """Send an idempotent payment."""
        ctx.effect_applied()
        ctx.require_input(CONFIRM)
        return Out()

    @server.tool(name="pay.ask_first", effect=Effect.WRITE)
    async def ask_first(ctx: ToolContext) -> Out:
        """Confirm, then pay."""
        ctx.require_input(CONFIRM)
        ctx.effect_applied()
        return Out()

    findings = [f for f in lint_server(server) if f.severity == "error"]
    assert [(f.rule, f.tool, f.severity) for f in findings] == [
        ("input-after-effect", "pay.send", "error")
    ]
    assert findings[0].docs.endswith("docs/rules/input-after-effect.md")


def test_elicit_and_unguarded_destructive_are_warnings():
    server = OunceServer("s")

    @server.tool(name="db.drop", effect=Effect.DESTRUCTIVE)
    async def drop(ctx: ToolContext) -> Out:
        """Drop a table."""
        assert ctx.fastmcp is not None
        await ctx.fastmcp.elicit("Sure?", bool)
        return Out()

    @server.tool(name="db.read", effect=Effect.READ)
    async def read() -> Out:
        """Read rows."""
        return Out()

    safety = {"elicit-in-write", "destructive-without-policy"}
    assert [r for r in rules(server) if r[0] in safety] == [
        ("elicit-in-write", "db.drop"),
        ("destructive-without-policy", "db.drop"),
    ]


def test_check_reports_lints_and_fails_on_errors(tmp_path: Path, capsys):
    path = tmp_path / "server.py"
    path.write_text(
        textwrap.dedent(
            '''
            from pydantic import BaseModel
            from ouncemcp import Effect, OunceServer, ToolContext

            server = OunceServer("bad")

            class Out(BaseModel):
                ok: bool = True

            @server.tool(name="pay.send", effect=Effect.WRITE)
            async def send(ctx: ToolContext) -> Out:
                """Send a payment."""
                ctx.effect_applied()
                ctx.require_input({})
                return Out()
            '''
        )
    )
    assert main(["check", str(path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert "input-after-effect" in [lint["rule"] for lint in data["lints"]]
    assert main(["check", str(path)]) == 1
    assert "ERROR [input-after-effect] pay.send" in capsys.readouterr().out
