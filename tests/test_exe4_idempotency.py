"""EXE-4: idempotent writes, the shared store and ctx.deps (proposal 0011)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from fastmcp import Client
from key_value.aio.stores.memory import MemoryStore as KVMemory
from pydantic import BaseModel

from ouncemcp import CursorSigner, Effect, InvalidInput, OunceServer, ToolContext
from ouncemcp.execution.idempotency import Idempotency
from ouncemcp.execution.store import KeyValueStore, MemoryStore, RedisStore
from ouncemcp.testing import as_caller


class Ticket(BaseModel):
    id: str
    title: str


def server_with(
    effects: list[str],
    *,
    delay: float = 0.0,
    fail: Exception | None = None,
    deadline_ms: int | None = None,
    **kwargs: Any,
) -> OunceServer:
    server = OunceServer("t", **kwargs)

    @server.tool(
        name="tickets.create",
        effect=Effect.WRITE,
        idempotency=Idempotency(grace_s=5, wait_s=2),
        deadline_ms=deadline_ms,
    )
    async def create(title: str) -> Ticket:
        """Create a ticket."""
        if fail is not None:
            raise fail
        await anyio.sleep(delay)
        effects.append(title)
        return Ticket(id=f"T-{len(effects)}", title=title)

    return server


def meta(result: Any) -> dict[str, Any]:
    return result.meta["ouncemcp"]


@pytest.mark.anyio
async def test_a_repeated_write_runs_once_and_replays_the_result():
    effects: list[str] = []
    server = server_with(effects)
    first = await server.call_tool("tickets.create", {"title": "printer"})
    again = await server.call_tool("tickets.create", {"title": "printer"})
    other = await server.call_tool("tickets.create", {"title": "laptop"})
    assert effects == ["printer", "laptop"]
    assert again.structured_content == first.structured_content == {"id": "T-1", "title": "printer"}
    assert meta(again)["replayed"] is True and "replayed" not in meta(first)
    assert other.structured_content["id"] == "T-2"


@pytest.mark.anyio
async def test_keys_are_per_caller_and_tenant():
    effects: list[str] = []
    server = server_with(effects)
    for caller in (
        {"subject": "a", "tenant": "acme"},
        {"subject": "a", "tenant": "globex"},
        {"subject": "b", "tenant": "acme"},
        {"subject": "a", "tenant": "acme"},
    ):
        with as_caller(**caller):
            await server.call_tool("tickets.create", {"title": "x"})
    assert len(effects) == 3


@pytest.mark.anyio
async def test_concurrent_duplicates_run_the_handler_once():
    effects: list[str] = []
    server = server_with(effects, delay=0.2)
    results = await asyncio.gather(
        *(server.call_tool("tickets.create", {"title": "dup"}) for _ in range(5))
    )
    assert effects == ["dup"]
    assert {r.structured_content["id"] for r in results} == {"T-1"}


@pytest.mark.anyio
async def test_timeout_after_the_upstream_write_produces_no_duplicate():
    effects: list[str] = []
    server = server_with(effects, delay=0.4, deadline_ms=100)
    first = await server.call_tool("tickets.create", {"title": "slow"})
    assert first.is_error
    assert meta(first)["category"] == "ambiguous_write"
    assert meta(first)["error"] == "write_still_running"
    assert "will not run twice" in first.content[0].text
    await server.idempotency.drain()
    retry = await server.call_tool("tickets.create", {"title": "slow"})
    assert effects == ["slow"]
    assert retry.structured_content == {"id": "T-1", "title": "slow"}
    assert meta(retry)["replayed"] is True


@pytest.mark.anyio
async def test_a_failure_that_changed_nothing_can_be_retried():
    effects: list[str] = []
    failures = [InvalidInput("Title is taken.")]
    server = OunceServer("t")

    @server.tool(name="t.create", effect=Effect.WRITE, idempotency=True)
    async def create(title: str) -> Ticket:
        """Create."""
        if failures:
            raise failures.pop()
        effects.append(title)
        return Ticket(id="T-1", title=title)

    assert (await server.call_tool("t.create", {"title": "x"})).is_error
    assert not (await server.call_tool("t.create", {"title": "x"})).is_error
    assert effects == ["x"]


@pytest.mark.anyio
async def test_an_unknown_failure_is_never_rerun_blindly():
    effects: list[str] = []
    server = server_with(effects, fail=RuntimeError("connection reset mid-write"))
    first = await server.call_tool("tickets.create", {"title": "x"})
    again = await server.call_tool("tickets.create", {"title": "x"})
    assert meta(first)["category"] == "internal"
    assert meta(again)["category"] == "ambiguous_write"
    assert "Check the current state" in again.content[0].text


@pytest.mark.anyio
async def test_client_supplied_keys():
    effects: list[str] = []
    server = server_with(effects)
    async with Client(server.fastmcp) as client:
        one = await client.call_tool(
            "tickets.create", {"title": "a"}, meta={"idempotencyKey": "k1"}
        )
        same = await client.call_tool(
            "tickets.create", {"title": "a"}, meta={"idempotencyKey": "k1"}
        )
        reused = await client.call_tool(
            "tickets.create", {"title": "b"}, meta={"idempotencyKey": "k1"}, raise_on_error=False
        )
        fresh = await client.call_tool(
            "tickets.create", {"title": "a"}, meta={"idempotencyKey": "k2"}
        )
    assert one.structured_content == same.structured_content
    assert reused.is_error and reused.meta["ouncemcp"]["error"] == "idempotency_key_reused"
    assert fresh.structured_content["id"] == "T-2"
    assert effects == ["a", "a"]


def test_idempotency_is_only_for_writes():
    server = OunceServer("t")
    with pytest.raises(ValueError, match="write and destructive"):

        @server.tool(name="t.read", effect=Effect.READ, idempotency=True)
        async def read() -> Ticket:
            """Read."""
            return Ticket(id="1", title="x")


@pytest.mark.anyio
async def test_two_replicas_sharing_a_store_write_once():
    store = MemoryStore()
    effects: list[str] = []
    a = server_with(effects, store=store)
    b = server_with(effects, store=store)
    await a.call_tool("tickets.create", {"title": "shared"})
    replay = await b.call_tool("tickets.create", {"title": "shared"})
    assert effects == ["shared"] and meta(replay)["replayed"] is True


# ------------------------------------------------------------------ stores


@pytest.mark.anyio
async def test_memory_store_expiry_and_claims():
    now = [0.0]
    store = MemoryStore(clock=lambda: now[0])
    assert await store.put_if_absent("ns", "k", {"v": 1}, ttl_s=10)
    assert not await store.put_if_absent("ns", "k", {"v": 2}, ttl_s=10)
    assert await store.get("ns", "k") == {"v": 1}
    now[0] = 11
    assert await store.get("ns", "k") is None
    assert await store.put_if_absent("ns", "k", {"v": 3}, ttl_s=10)
    assert await store.delete("ns", "k")


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    async def get(self, name: str) -> str | None:
        return self.data.get(name)

    async def set(self, name: str, value: str, px: int | None = None, nx: bool = False) -> bool:
        self.calls.append({"name": name, "px": px, "nx": nx})
        if nx and name in self.data:
            return False
        self.data[name] = value
        return True

    async def delete(self, name: str) -> int:
        return 1 if self.data.pop(name, None) is not None else 0


@pytest.mark.anyio
async def test_redis_store_uses_set_nx_with_expiry():
    redis = FakeRedis()
    store = RedisStore(redis, prefix="app")
    assert store.shared
    assert await store.put_if_absent("idempotency", "k", {"state": "running"}, ttl_s=1.5)
    assert not await store.put_if_absent("idempotency", "k", {"state": "running"}, ttl_s=1.5)
    assert redis.calls[0] == {"name": "app:idempotency:k", "px": 1500, "nx": True}
    assert await store.get("idempotency", "k") == {"state": "running"}
    assert await store.delete("idempotency", "k")


@pytest.mark.anyio
async def test_key_value_store_adapter():
    store = KeyValueStore(KVMemory())
    assert await store.put_if_absent("ns", "k", {"a": 1}, ttl_s=60)
    assert not await store.put_if_absent("ns", "k", {"a": 2}, ttl_s=60)
    assert await store.get("ns", "k") == {"a": 1}
    assert await store.delete("ns", "k")


def test_store_must_implement_the_protocol():
    with pytest.raises(TypeError, match="OunceStore"):
        OunceServer("t", store=object())  # type: ignore[arg-type]


# ------------------------------------------------------------------ shared single use, deps


@pytest.mark.anyio
async def test_consumed_state_is_refused_on_another_replica():
    store = MemoryStore()
    keys = CursorSigner(["k" * 32])

    def replica() -> OunceServer:
        server = OunceServer("r", store=store, cursors=keys)

        @server.tool(name="approval.issue", effect=Effect.READ)
        async def issue(ctx: ToolContext) -> Ticket:
            """Issue."""
            return Ticket(id=ctx.issue_state("approval", request="op"), title="")

        @server.tool(name="approval.use", effect=Effect.WRITE)
        async def use(receipt: str, ctx: ToolContext) -> Ticket:
            """Use once."""
            await ctx.consume_state(receipt, "approval", request="op")
            return Ticket(id="ok", title="")

        return server

    a, b = replica(), replica()
    token = (await a.call_tool("approval.issue")).structured_content["id"]
    assert not (await a.call_tool("approval.use", {"receipt": token})).is_error
    replayed = await b.call_tool("approval.use", {"receipt": token})
    assert replayed.is_error and "already used" in replayed.content[0].text


@pytest.mark.anyio
async def test_ctx_deps_reach_handlers():
    deps = SimpleNamespace(tickets={"T-9": "fixture"})
    server = OunceServer("d", deps=deps)

    @server.tool(name="t.get", effect=Effect.READ)
    async def get(id: str, ctx: ToolContext) -> Ticket:
        """Get."""
        return Ticket(id=id, title=ctx.deps.tickets[id])

    assert (await server.call_tool("t.get", {"id": "T-9"})).structured_content["title"] == "fixture"


@pytest.mark.anyio
async def test_guarded_client_blocks_the_inbound_token():
    import httpx2

    from ouncemcp.security import TokenPassthrough, guarded_client

    sent: list[Any] = []
    client = guarded_client(
        transport=httpx2.MockTransport(lambda r: sent.append(r) or httpx2.Response(200))
    )
    async with client:
        with as_caller(subject="u", token="inbound-secret-abcdef12"):
            with pytest.raises(TokenPassthrough):
                await client.get(
                    "https://api.example",
                    headers={"Authorization": "Bearer inbound-secret-abcdef12"},
                )
            await client.get(
                "https://api.example", headers={"Authorization": "Bearer own-key-123456"}
            )
    assert len(sent) == 1
