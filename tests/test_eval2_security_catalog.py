"""EVAL-2: the security catalog passes on safe tools and catches unsafe ones."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlparse

import pytest
from fastmcp.server.dependencies import get_access_token
from pydantic import BaseModel

from ouncemcp import Cursor, Effect, InvalidInput, OunceServer, Paginated, ToolContext
from ouncemcp.eval import (
    CrossTenant,
    CursorReuse,
    GuessedName,
    HandleReplay,
    SSRFProbe,
    TokenPassthrough,
    run_security_suite,
)
from ouncemcp.security import check_outbound, require_scopes

ACME = {"subject": "alice", "tenant": "acme"}
GLOBEX = {"subject": "mallory", "tenant": "globex"}
INVOICES = {"acme": ["acme-1", "acme-2", "acme-3"], "globex": ["globex-1"]}


class Invoices(BaseModel):
    items: Annotated[list[str], Paginated]
    next_cursor: Cursor = None


class Handle(BaseModel):
    handle: str


class Text(BaseModel):
    text: str


def build() -> tuple[OunceServer, list[dict[str, str]]]:
    server = OunceServer("catalog")
    upstream: list[dict[str, str]] = []
    leaky_cache: dict[str, Any] = {}

    @server.tool(name="admin.export", effect=Effect.READ, policy=require_scopes("admin"))
    async def export() -> Text:
        """Export everything."""
        return Text(text="all")

    @server.tool(name="invoices.list", effect=Effect.READ)
    async def safe_list(ctx: ToolContext, limit: int = 2, cursor: str | None = None) -> Invoices:
        """List the caller's invoices."""
        rows = INVOICES.get(ctx.identity.tenant or "", [])
        page = ctx.page(rows, cursor=cursor, limit=limit)
        return Invoices(items=page.items, next_cursor=page.next_cursor)

    @server.tool(name="invoices.leaky", effect=Effect.READ)
    async def leaky(ctx: ToolContext, period: str = "2026-10") -> Invoices:
        """List invoices, cached by period only (wrong)."""
        if period not in leaky_cache:
            leaky_cache[period] = INVOICES.get(ctx.identity.tenant or "", [])
        return Invoices(items=leaky_cache[period])

    @server.tool(name="report.start", effect=Effect.READ)
    async def start(ctx: ToolContext) -> Handle:
        """Start a report."""
        return Handle(handle=ctx.issue_state("report", {"id": "r1"}, request="report"))

    @server.tool(name="report.approve", effect=Effect.WRITE)
    async def approve(handle: str, ctx: ToolContext) -> Text:
        """Approve a report, once."""
        ctx.verify_state(handle, "report", request="report", consume=True)
        return Text(text="approved")

    @server.tool(name="report.start_plain", effect=Effect.READ)
    async def start_plain() -> Handle:
        """Start a report with a guessable handle (wrong)."""
        return Handle(handle="report-1")

    @server.tool(name="report.read_plain", effect=Effect.READ)
    async def read_plain(handle: str) -> Text:
        """Read a report by plain handle (wrong)."""
        return Text(text=f"contents of {handle}")

    def send(headers: dict[str, str]) -> None:
        check_outbound(headers)
        upstream.append(headers)

    @server.tool(name="crm.sync_safe", effect=Effect.READ)
    async def sync_safe() -> Text:
        """Call the CRM with the server's own key."""
        send({"Authorization": "Bearer server-key-123456"})
        return Text(text="ok")

    @server.tool(name="crm.sync_forward", effect=Effect.READ)
    async def sync_forward() -> Text:
        """Call the CRM with the caller's token, unguarded (wrong)."""
        token = get_access_token()
        upstream.append({"Authorization": f"Bearer {token.token if token else ''}"})
        return Text(text="ok")

    @server.tool(name="web.fetch_safe", effect=Effect.READ)
    async def fetch_safe(url: str) -> Text:
        """Fetch a page from an allowed host."""
        if urlparse(url).hostname not in {"docs.example.com"}:
            raise InvalidInput("Only docs.example.com can be fetched.")
        return Text(text="page")

    @server.tool(name="web.fetch_any", effect=Effect.READ)
    async def fetch_any(url: str) -> Text:
        """Fetch any URL (wrong)."""
        return Text(text="fetched")

    return server, upstream


def by_target(report: Any) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for r in report.results:
        out[f"{r.case}:{r.target}"] = out.get(f"{r.case}:{r.target}", True) and r.passed
    return out


@pytest.mark.anyio
async def test_catalog_passes_safe_tools_and_flags_unsafe_ones():
    server, upstream = build()
    cases = [
        GuessedName("admin.export", intruder=GLOBEX),
        CrossTenant("invoices.list", {}, owner=ACME, intruder=GLOBEX),
        CrossTenant("invoices.leaky", {}, owner=ACME, intruder=GLOBEX),
        CursorReuse("invoices.list", {"limit": 1}, owner=ACME, intruder=GLOBEX),
        HandleReplay(
            "report.start", {}, "report.approve", "handle", "handle", ACME, GLOBEX, single_use=True
        ),
        HandleReplay(
            "report.start_plain", {}, "report.read_plain", "handle", "handle", ACME, GLOBEX
        ),
        TokenPassthrough("crm.sync_safe", {}, recorded=lambda: upstream),
        TokenPassthrough("crm.sync_forward", {}, recorded=lambda: upstream),
        SSRFProbe("web.fetch_safe", "url"),
        SSRFProbe("web.fetch_any", "url", targets=["http://169.254.169.254/"]),
    ]
    report = await run_security_suite(server, cases)
    result = by_target(report)
    assert result == {
        "guessed-name:admin.export": True,
        "cross-tenant:invoices.list": True,
        "cross-tenant:invoices.leaky": False,
        "cursor-reuse:invoices.list": True,
        "handle-cross-principal:report.approve": True,
        "handle-replay:report.approve": True,
        "handle-cross-principal:report.read_plain": False,
        "token-passthrough:crm.sync_safe": True,
        "token-passthrough:crm.sync_forward": False,
        **{f"ssrf:web.fetch_safe {u}": True for u in SSRFProbe("x", "u").targets},
        "ssrf:web.fetch_any http://169.254.169.254/": False,
    }
    assert not report.passed
    text = report.render_text()
    assert "FAIL cross-tenant invoices.leaky" in text


@pytest.mark.anyio
async def test_automatic_probes_cover_every_tool_with_a_policy():
    server, _ = build()
    report = await run_security_suite(server)
    assert [(r.case, r.target, r.passed) for r in report.results] == [
        ("guessed-name", "admin.export", True)
    ]
    assert report.to_dict()["passed"] is True
