"""SEC-1 to SEC-8: identity, policy, audience, passthrough, caches, bound state, replay
safety and HTTP defaults (spec section 8.2, proposal 0008)."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest
from fastmcp import Client
from fastmcp.tools.base import InputRequiredToolResult
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer, ToolContext
from ouncemcp.discovery import CALL_TOOL, SEARCH_TOOL
from ouncemcp.security import (
    ANONYMOUS_IDENTITY,
    InvalidState,
    PartitionedCache,
    PolicyRequest,
    StateSigner,
    TokenPassthrough,
    all_of,
    any_of,
    authenticated,
    check_outbound,
    identity_from_token,
    passthrough_guard,
    require_scopes,
    require_tenant,
    resolve_http_options,
    writes_require,
)
from ouncemcp.testing import as_caller, make_token


class Out(BaseModel):
    ok: bool = True
    value: str = ""


def add(server: OunceServer, name: str, effect: Effect = Effect.READ, **kwargs: Any) -> None:
    async def handler(query: str = "") -> Out:
        return Out(value=name)

    server.tool(name=name, effect=effect, description=f"Tool {name}.", **kwargs)(handler)


async def names(server: OunceServer) -> list[str]:
    async with Client(server.fastmcp) as client:
        return sorted(t.name for t in await client.list_tools())


async def call(server: OunceServer, tool: str, args: dict[str, Any] | None = None) -> Any:
    async with Client(server.fastmcp) as client:
        return await client.call_tool(tool, args or {}, raise_on_error=False)


def meta(result: Any) -> dict[str, Any]:
    return result.meta["ouncemcp"]


# ------------------------------------------------------------------ SEC-1 identity


def test_identity_comes_from_verified_claims_and_includes_the_issuer():
    token = make_token(subject="alice", issuer="https://idp.example", tenant="acme", scopes=["a"])
    identity = identity_from_token(token)
    assert identity.principal == "https://idp.example|alice"
    assert identity.tenant == "acme"
    assert identity.scopes == {"a"}
    assert identity.authenticated
    assert "test-inbound-token" not in repr(identity)


def test_same_subject_from_two_issuers_is_two_callers():
    one = identity_from_token(make_token(subject="42", issuer="https://a.example"))
    two = identity_from_token(make_token(subject="42", issuer="https://b.example"))
    assert one.principal != two.principal


@pytest.mark.anyio
async def test_spoofed_client_info_has_no_effect():
    seen: list[str] = []

    def only_admin(request: PolicyRequest) -> bool:
        seen.append(request.identity.principal)
        return request.identity.principal == "admin"

    server = OunceServer("s", policy=only_admin)
    add(server, "admin.panel")

    @server.tool(name="whoami", effect=Effect.READ, policy=lambda r: True)
    async def whoami(ctx: ToolContext) -> Out:
        """Return the caller."""
        return Out(value=ctx.principal)

    from mcp_types import Implementation

    spoof = Implementation(name="admin", version="1.0")
    async with Client(server.fastmcp, client_info=spoof) as client:
        listed = [t.name for t in await client.list_tools()]
        result = await client.call_tool("admin.panel", {}, raise_on_error=False)
    assert listed == []
    assert result.is_error
    assert set(seen) == {"anonymous"}


@pytest.mark.anyio
async def test_context_identity_is_the_verified_caller():
    server = OunceServer("s")

    @server.tool(name="me", effect=Effect.READ)
    async def me(ctx: ToolContext) -> Out:
        """Return the caller's tenant."""
        return Out(value=f"{ctx.identity.principal}/{ctx.identity.tenant}")

    with as_caller(subject="bob", tenant="globex"):
        result = await server.call_tool("me")
    assert result.structured_content["value"] == "bob/globex"
    assert (await server.call_tool("me")).structured_content["value"] == "anonymous/None"


# ------------------------------------------------------------------ SEC-2 policy on lists and calls


def guarded() -> OunceServer:
    server = OunceServer("guarded", discovery="search")
    add(server, "invoice.find")
    add(server, "payroll.export", policy=require_scopes("payroll"))
    return server


@pytest.mark.anyio
async def test_policy_hides_the_tool_from_search_and_blocks_every_call_path():
    server = guarded()
    with as_caller(subject="u", scopes=["other"]):
        found = await call(server, SEARCH_TOOL, {"query": "payroll export", "detail": "name"})
        direct = await call(server, "payroll.export")
        proxied = await call(server, CALL_TOOL, {"name": "payroll.export", "arguments": {}})
    assert "payroll" not in found.content[0].text
    assert direct.is_error and proxied.is_error
    with as_caller(subject="u", scopes=["payroll"]):
        found = await call(server, SEARCH_TOOL, {"query": "payroll export", "detail": "name"})
        allowed = await call(server, "payroll.export")
    assert "payroll.export" in found.content[0].text
    assert not allowed.is_error


@pytest.mark.anyio
async def test_policy_filters_direct_listing():
    server = OunceServer("s", discovery="direct", policy=writes_require(authenticated))
    add(server, "a.read")
    add(server, "a.write", Effect.WRITE)
    assert await names(server) == ["a.read"]
    with as_caller(subject="alice"):
        assert await names(server) == ["a.read", "a.write"]


@pytest.mark.anyio
async def test_server_and_tool_policies_must_both_allow():
    server = OunceServer("s", policy=authenticated)
    add(server, "t.scoped", policy=require_tenant("acme"))
    with as_caller(subject="a", tenant="globex"):
        assert (await call(server, "t.scoped")).is_error
    with as_caller(subject="a", tenant="acme"):
        assert not (await call(server, "t.scoped")).is_error
    assert (await call(server, "t.scoped")).is_error


@pytest.mark.anyio
async def test_a_policy_that_raises_or_returns_non_true_denies():
    def broken(request: PolicyRequest) -> bool:
        raise RuntimeError("bug")

    server = OunceServer("s")
    add(server, "t.broken", policy=broken)
    add(server, "t.truthy", policy=lambda r: "yes")  # type: ignore[arg-type,return-value]
    assert await names(server) == []


@pytest.mark.anyio
async def test_combinators():
    server = OunceServer("s")
    add(server, "t.any", policy=any_of(require_scopes("x"), require_tenant("acme")))
    add(server, "t.all", policy=all_of(require_scopes("x"), require_tenant("acme")))
    with as_caller(subject="a", tenant="acme"):
        assert await names(server) == ["t.any"]
    with as_caller(subject="a", tenant="acme", scopes=["x"]):
        assert await names(server) == ["t.all", "t.any"]


def test_policy_must_be_callable():
    with pytest.raises(TypeError, match="policy"):
        OunceServer("s", policy=["not callable"])  # type: ignore[list-item]


# ------------------------------------------------------------------ SEC-3 annotations are hints


@pytest.mark.anyio
async def test_policy_uses_the_declared_effect_not_annotations():
    server = OunceServer("s", policy=writes_require(require_scopes("admin")))
    add(server, "db.drop", Effect.DESTRUCTIVE)
    # A proxy or a buggy client could relabel the tool as read-only. Access must not change.
    server._registered["db.drop"].annotations = ToolAnnotations(
        read_only_hint=True, destructive_hint=False
    )
    with as_caller(subject="u"):
        assert (await call(server, "db.drop")).is_error
    with as_caller(subject="u", scopes=["admin"]):
        assert not (await call(server, "db.drop")).is_error


# ------------------------------------------------------------------ SEC-4 audience and passthrough


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("audience", "allowed"),
    [
        ("https://mcp.example.com", True),
        (["https://other.example", "https://mcp.example.com/"], True),
        ("https://other.example", False),
        (None, False),
    ],
)
async def test_token_audience_is_validated(audience, allowed):
    server = OunceServer("s", audience="https://mcp.example.com")
    add(server, "t.read")
    with as_caller(subject="u", audience=audience):
        assert (await names(server) == ["t.read"]) is allowed
        assert (await call(server, "t.read")).is_error is not allowed


@pytest.mark.anyio
async def test_audience_refuses_anonymous_callers():
    server = OunceServer("s", audience="https://mcp.example.com")
    add(server, "t.read")
    assert await names(server) == []


def test_empty_audience_is_rejected():
    with pytest.raises(ValueError, match="audience"):
        OunceServer("s", audience="")


@pytest.mark.anyio
async def test_forwarding_the_inbound_token_is_refused():
    server = OunceServer("s")

    @server.tool(name="upstream.bad", effect=Effect.READ)
    async def bad(ctx: ToolContext) -> Out:
        """Call upstream with the caller's token (wrong)."""
        check_outbound({"Authorization": "Bearer test-inbound-token-0123456789"})
        return Out()

    @server.tool(name="upstream.good", effect=Effect.READ)
    async def good(ctx: ToolContext) -> Out:
        """Call upstream with the server's own key."""
        check_outbound({"Authorization": "Bearer server-own-upstream-key"})
        return Out()

    with as_caller(subject="u"):
        refused = await call(server, "upstream.bad")
        fine = await call(server, "upstream.good")
    assert refused.is_error
    assert meta(refused)["category"] == "permission"
    assert meta(refused)["error"] == "token_passthrough"
    assert "0123456789" not in refused.content[0].text
    assert not fine.is_error


@pytest.mark.anyio
async def test_httpx_hook_blocks_passthrough_before_sending():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, event_hooks={"request": [passthrough_guard()]}
    ) as client:
        with as_caller(subject="u", token="inbound-secret-value-xyz"):
            with pytest.raises(TokenPassthrough):
                await client.get(
                    "https://api.example", headers={"X-Api-Key": "inbound-secret-value-xyz"}
                )
            await client.get("https://api.example", headers={"X-Api-Key": "own-key-abcdef"})
        await client.get("https://api.example", headers={"X-Api-Key": "anything"})
    assert len(sent) == 2


def test_check_outbound_without_a_request_passes():
    check_outbound({"Authorization": "Bearer whatever-1234567"})


# ------------------------------------------------------------------ SEC-5 partitioned caches


@pytest.mark.anyio
async def test_cache_entries_never_cross_tenants_or_principals():
    server = OunceServer("s")
    loads: list[str] = []

    @server.tool(name="report.get", effect=Effect.READ)
    async def report(ctx: ToolContext) -> Out:
        """Load the caller's report, cached."""

        async def load() -> str:
            loads.append(f"{ctx.principal}/{ctx.identity.tenant}")
            return f"report for {ctx.identity.tenant}"

        return Out(value=await ctx.cache.get_or_set("report", load))

    async def get(**caller: Any) -> str:
        with as_caller(**caller):
            return (await server.call_tool("report.get")).structured_content["value"]

    assert await get(subject="a", tenant="acme") == "report for acme"
    assert await get(subject="a", tenant="acme") == "report for acme"
    assert await get(subject="a", tenant="globex") == "report for globex"
    assert await get(subject="b", tenant="acme") == "report for acme"
    assert await get(subject="a", tenant="acme", scopes=["more"]) == "report for acme"
    assert loads == ["a/acme", "a/globex", "b/acme", "a/acme"]


def test_partitioned_cache_expires_and_evicts():
    now = [0.0]
    cache = PartitionedCache(ttl_s=10, max_entries=2, clock=lambda: now[0])
    alice = identity_from_token(make_token(subject="alice"))
    cache.set(alice, "a", 1)
    cache.set(alice, "b", 2)
    cache.set(alice, "c", 3)
    assert cache.get(alice, "a") is None
    assert cache.get(alice, "c") == 3
    now[0] = 11
    assert cache.get(alice, "c") is None
    cache.set(alice, "d", 4)
    cache.set(ANONYMOUS_IDENTITY, "d", 5)
    cache.clear(alice)
    assert cache.get(alice, "d") is None
    assert cache.get(ANONYMOUS_IDENTITY, "d") == 5


# ------------------------------------------------------------------ SEC-6 bound state


def signer(now: list[float]) -> StateSigner:
    from ouncemcp import CursorSigner

    return StateSigner(CursorSigner(["k" * 32]), ttl_s=60, clock=lambda: now[0])


def test_state_round_trip_and_rejections():
    now = [1_000.0]
    s = signer(now)
    token = s.issue("approval", principal="alice", request={"op": "delete", "id": 7}, data={"x": 1})
    assert s.verify(
        token, kind="approval", principal="alice", request={"id": 7, "op": "delete"}
    ) == {"x": 1}
    cases = {
        "principal": {"kind": "approval", "principal": "bob", "request": {"op": "delete", "id": 7}},
        "request": {"kind": "approval", "principal": "alice", "request": {"op": "delete", "id": 8}},
        "kind": {"kind": "handle", "principal": "alice", "request": {"op": "delete", "id": 7}},
    }
    for reason, kwargs in cases.items():
        with pytest.raises(InvalidState) as err:
            s.verify(token, **kwargs)
        assert err.value.reason == reason
    body, mac = token[3:].split(".")
    flipped = token[:3] + body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1] + "." + mac
    for bad, reason in [("nope", "malformed"), ("b1.x.y", "malformed"), (flipped, "signature")]:
        with pytest.raises(InvalidState) as err:
            s.verify(bad, kind="approval", principal="alice")
        assert err.value.reason in (reason, "malformed")
    now[0] += 61
    with pytest.raises(InvalidState) as err:
        s.verify(token, kind="approval", principal="alice", request={"op": "delete", "id": 7})
    assert err.value.reason == "expired"


def test_consumed_state_cannot_be_replayed():
    now = [1_000.0]
    s = signer(now)
    token = s.issue("approval", principal="alice")
    s.verify(token, kind="approval", principal="alice", consume=True)
    with pytest.raises(InvalidState) as err:
        s.verify(token, kind="approval", principal="alice", consume=True)
    assert err.value.reason == "replayed"


def test_a_cursor_is_not_accepted_as_state():
    from ouncemcp import CursorSigner

    keys = CursorSigner(["k" * 32])
    cursor = keys.bind(tool="t", principal="alice").encode(5)
    with pytest.raises(InvalidState):
        StateSigner(keys).verify(cursor, kind="approval", principal="alice")


def test_state_size_is_bounded():
    s = signer([0.0])
    with pytest.raises(ValueError, match="under"):
        s.issue("handle", principal="a", data={"blob": "x" * 5000})


@pytest.mark.anyio
async def test_handles_from_one_caller_fail_for_another_through_the_server():
    server = OunceServer("s")

    @server.tool(name="report.start", effect=Effect.READ)
    async def start(ctx: ToolContext) -> Out:
        """Start a report and return a handle."""
        return Out(value=ctx.issue_state("report", {"id": "r1"}, request="report"))

    @server.tool(name="report.read", effect=Effect.READ)
    async def read(handle: str, ctx: ToolContext) -> Out:
        """Read a report by handle."""
        return Out(value=ctx.verify_state(handle, "report", request="report")["id"])

    with as_caller(subject="alice"):
        handle = (await server.call_tool("report.start")).structured_content["value"]
        mine = await server.call_tool("report.read", {"handle": handle})
    with as_caller(subject="mallory"):
        theirs = await call(server, "report.read", {"handle": handle})
    assert mine.structured_content["value"] == "r1"
    assert theirs.is_error
    assert meta(theirs)["category"] == "validation"
    assert meta(theirs)["error"] == "invalid_state"
    assert "different caller" in theirs.content[0].text


# ------------------------------------------------------------------ SEC-7 replay-safe effects

CONFIRM = {
    "confirm": {
        "method": "elicitation/create",
        "params": {
            "message": "Are you sure?",
            "requestedSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        },
    }
}


def write_server(**tool_kwargs: Any) -> tuple[OunceServer, list[str]]:
    server = OunceServer("s")
    effects: list[str] = []

    @server.tool(name="ticket.close", effect=Effect.WRITE, **tool_kwargs)
    async def close(ticket_id: str, ctx: ToolContext) -> Out:
        """Close a ticket after confirming."""
        effects.append(ticket_id)
        ctx.effect_applied()
        ctx.require_input(CONFIRM)
        return Out()

    return server, effects


@pytest.mark.anyio
async def test_asking_for_input_after_an_effect_is_refused():
    server, effects = write_server()
    result = await server.call_tool("ticket.close", {"ticket_id": "T-1"})
    assert effects == ["T-1"]
    assert result.is_error
    assert meta(result)["category"] == "internal"
    assert "SEC-7" not in result.content[0].text  # details go to the log only


@pytest.mark.anyio
async def test_replay_safe_tools_may_ask_after_an_effect():
    server, _ = write_server(replay_safe=True)
    result = await server.fastmcp.call_tool("ticket.close", {"ticket_id": "T-1"})
    assert isinstance(result, InputRequiredToolResult)


@pytest.mark.anyio
async def test_asking_before_the_effect_returns_input_required_with_bound_state():
    server = OunceServer("s")

    @server.tool(name="ticket.delete", effect=Effect.DESTRUCTIVE)
    async def delete(ticket_id: str, ctx: ToolContext) -> Out:
        """Delete a ticket after confirming."""
        ctx.require_input(CONFIRM, state={"step": 1})
        return Out()

    with as_caller(subject="alice"):
        result = await server.fastmcp.call_tool("ticket.delete", {"ticket_id": "T-1"})
    assert isinstance(result, InputRequiredToolResult)
    state = result.input_required.request_state
    assert state and state.startswith("b1.")

    class Fake:
        request_state = state
        input_responses = None

    def ctx_for(principal: str, args: dict[str, Any]) -> ToolContext:
        return ToolContext(
            tool="ticket.delete",
            response_format="concise",
            result_budget=100,
            fastmcp=Fake(),  # type: ignore[arg-type]
            _signer=server.cursors,
            _state=server.state,
            _arguments=lambda: args,
            _resolve=lambda c: principal,
        )

    with as_caller(subject="alice"):
        assert ctx_for("alice", {"ticket_id": "T-1"}).resume_state() == {"step": 1}
    with pytest.raises(InvalidState):
        ctx_for("alice", {"ticket_id": "T-2"}).resume_state()
    with pytest.raises(InvalidState):
        ctx_for("bob", {"ticket_id": "T-1"}).resume_state()


# ------------------------------------------------------------------ SEC-8 HTTP defaults


def test_http_defaults_bind_loopback_and_validate_origin():
    options = resolve_http_options({})
    assert options["host"] == "127.0.0.1"
    assert options["host_origin_protection"] is True


def test_public_bind_needs_allowed_hosts():
    with pytest.raises(ValueError, match="allowed_hosts"):
        resolve_http_options({"host": "0.0.0.0"})
    ok = resolve_http_options({"host": "0.0.0.0", "allowed_hosts": ["mcp.example.com"]})
    assert ok["allowed_hosts"] == ["mcp.example.com"]
    assert resolve_http_options({"host": "0.0.0.0", "host_origin_protection": False})


@pytest.mark.anyio
async def test_http_app_rejects_foreign_origins_and_hosts():
    server = OunceServer("s")
    add(server, "t.read")
    app = server.http_app()
    transport = httpx.ASGITransport(app=app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as http:
        evil = await http.post("/mcp", json=body, headers={"Origin": "https://evil.example"})
        rebound = await http.post("/mcp", json=body, headers={"Host": "evil.example"})
    assert evil.status_code == 403
    assert rebound.status_code == 421


def test_policy_request_is_json_free_of_the_raw_token():
    identity = identity_from_token(make_token(subject="a"))
    assert "test-inbound-token" not in json.dumps(
        {"p": identity.principal, "c": dict(identity.claims)}
    )
