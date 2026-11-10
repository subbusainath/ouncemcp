"""EXE-1, EXE-2, EXE-3, EXE-6: deadlines, retries, ambiguous writes, and limits."""

import asyncio
import contextvars
import json
import time
from typing import Any

import anyio
import pytest
from fastmcp import Client
from pydantic import BaseModel

from ouncemcp import Budget, Effect, OunceServer, ToolContext
from ouncemcp.core.errors import (
    AmbiguousWrite,
    InvalidInput,
    PermissionDenied,
    RateLimited,
    TransientError,
)
from ouncemcp.execution import (
    ConcurrencyGate,
    Limits,
    RetryPolicy,
    WriteAttempt,
    check_request,
    nesting_depth,
)


class Out(BaseModel):
    ok: bool = True
    note: str = ""


class ReadTimeout(Exception):  # recognized by name, like the HTTP client exception
    pass


def instant_retry(attempts: int = 3, sleeps: list[float] | None = None, **kw: Any) -> RetryPolicy:
    record = sleeps if sleeps is not None else []

    async def sleep(seconds: float) -> None:
        record.append(seconds)

    return RetryPolicy(max_attempts=attempts, jitter=0, sleep=sleep, **kw)


async def call(server: OunceServer, tool: str, args: dict[str, Any] | None = None) -> Any:
    return await server.fastmcp.call_tool(tool, args or {})


def meta(result: Any) -> dict[str, Any]:
    return result.meta["ouncemcp"]


# ------------------------------------------------------------------ EXE-1: deadlines


@pytest.mark.anyio
async def test_a_slow_async_read_hits_its_deadline_and_is_cancelled():
    server = OunceServer("t")
    state = {"cancelled": False, "finished": False}

    @server.tool(name="t.slow", effect=Effect.READ, deadline_ms=50)
    async def slow() -> Out:
        """Wait on a slow upstream."""
        try:
            await anyio.sleep(5)
        except anyio.get_cancelled_exc_class():
            state["cancelled"] = True
            raise
        state["finished"] = True
        return Out()

    started = time.perf_counter()
    result = await call(server, "t.slow")
    assert time.perf_counter() - started < 1.0
    assert result.is_error
    assert meta(result)["error"] == "deadline_exceeded"
    assert meta(result)["category"] == "transient"
    assert "0.05s deadline" in result.content[0].text
    assert state == {"cancelled": True, "finished": False}


@pytest.mark.anyio
async def test_a_slow_sync_handler_does_not_hold_the_call_past_its_deadline():
    server = OunceServer("t")

    @server.tool(name="t.slow", effect=Effect.READ, deadline_ms=50)
    def slow() -> Out:
        """Block a worker thread."""
        time.sleep(0.8)
        return Out()

    started = time.perf_counter()
    result = await call(server, "t.slow")
    assert time.perf_counter() - started < 0.5
    assert meta(result)["error"] == "deadline_exceeded"


@pytest.mark.anyio
async def test_every_tool_gets_the_default_deadline():
    server = OunceServer("t", limits=Limits(default_deadline_ms=50))

    @server.tool(name="t.slow", effect=Effect.READ)
    async def slow() -> Out:
        """Wait."""
        await anyio.sleep(5)
        return Out()

    @server.tool(name="t.patient", effect=Effect.READ, deadline_ms=2_000)
    async def patient() -> Out:
        """Wait a little."""
        await anyio.sleep(0.15)
        return Out()

    assert meta(await call(server, "t.slow"))["error"] == "deadline_exceeded"
    assert (await call(server, "t.patient")).structured_content["ok"] is True


@pytest.mark.anyio
async def test_calls_inside_the_deadline_are_untouched():
    server = OunceServer("t")

    @server.tool(name="t.fast", effect=Effect.READ, deadline_ms=1_000)
    async def fast() -> Out:
        """Quick."""
        return Out(note="done")

    result = await call(server, "t.fast")
    assert not result.is_error
    assert result.structured_content["note"] == "done"


@pytest.mark.anyio
async def test_handlers_can_read_the_time_left():
    server = OunceServer("t")
    seen: list[float] = []

    @server.tool(name="t.left", effect=Effect.READ, deadline_ms=4_000)
    async def left(ctx: ToolContext) -> Out:
        """Report the remaining time."""
        seen.append(ctx.remaining())
        await anyio.sleep(0.05)
        seen.append(ctx.remaining())
        return Out()

    await call(server, "t.left")
    assert 3.5 < seen[1] < seen[0] <= 4.0
    assert ToolContext("t", "concise", 10).remaining() == float("inf")


@pytest.mark.anyio
async def test_cancelling_a_call_cancels_the_upstream_work():
    server = OunceServer("t")
    state = {"started": anyio.Event(), "cancelled": False}

    @server.tool(name="t.hang", effect=Effect.READ)
    async def hang() -> Out:
        """Hang on an upstream call."""
        state["started"].set()
        try:
            await anyio.sleep(60)
        except anyio.get_cancelled_exc_class():
            state["cancelled"] = True
            raise
        return Out()

    task = asyncio.create_task(call(server, "t.hang"))
    await state["started"].wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state["cancelled"] is True


@pytest.mark.parametrize("bad", [0, -5, 1.5, True, "10"])
def test_deadline_must_be_a_positive_integer(bad):
    server = OunceServer("t")
    with pytest.raises(ValueError, match="deadline_ms"):

        @server.tool(name="t.x", effect=Effect.READ, deadline_ms=bad)
        def x() -> Out:
            """Doc."""
            return Out()


# ------------------------------------------------------------------ EXE-2: retries


def flaky_server(
    failures: list[Exception],
    *,
    effect: Effect = Effect.READ,
    retry: RetryPolicy | None = None,
    **kw,
) -> tuple[OunceServer, list[int]]:
    server = OunceServer("t", retry=retry or instant_retry(), **kw)
    attempts: list[int] = []

    @server.tool(name="t.go", effect=effect)
    def go(ctx: ToolContext) -> Out:
        """Sometimes fails."""
        attempts.append(ctx.attempt)
        if len(attempts) <= len(failures):
            raise failures[len(attempts) - 1]
        return Out(note="ok")

    return server, attempts


@pytest.mark.anyio
async def test_a_transient_read_failure_is_retried_until_it_works():
    sleeps: list[float] = []
    server, attempts = flaky_server(
        [TransientError("blip"), ReadTimeout("slow")], retry=instant_retry(3, sleeps)
    )
    result = await call(server, "t.go")
    assert result.structured_content["note"] == "ok"
    assert attempts == [1, 2, 3]
    assert sleeps == [0.1, 0.2]


@pytest.mark.anyio
async def test_retries_stop_at_the_limit_and_report_the_last_failure():
    sleeps: list[float] = []
    server, attempts = flaky_server([TransientError("blip")] * 5, retry=instant_retry(3, sleeps))
    result = await call(server, "t.go")
    assert result.is_error
    assert meta(result)["category"] == "transient"
    assert len(attempts) == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("effect", [Effect.WRITE, Effect.DESTRUCTIVE])
async def test_writes_are_never_retried(effect):
    server, attempts = flaky_server([TransientError("blip")] * 3, effect=effect)
    result = await call(server, "t.go")
    assert result.is_error
    assert attempts == [1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "exc",
    [
        InvalidInput("bad"),
        PermissionDenied("no"),
        AmbiguousWrite("unknown"),
        RuntimeError("boom"),
        ValueError("bad value"),
    ],
)
async def test_only_transient_and_rate_limit_failures_are_retried(exc):
    server, attempts = flaky_server([exc] * 3)
    assert (await call(server, "t.go")).is_error
    assert attempts == [1]


@pytest.mark.anyio
async def test_retry_waits_for_the_upstream_retry_after():
    sleeps: list[float] = []
    server, attempts = flaky_server(
        [RateLimited("slow down", retry_after=0.5)], retry=instant_retry(3, sleeps)
    )
    result = await call(server, "t.go")
    assert not result.is_error
    assert sleeps == [0.5]
    assert attempts == [1, 2]


@pytest.mark.anyio
async def test_a_retry_after_longer_than_the_deadline_is_reported_instead_of_waited_for():
    sleeps: list[float] = []
    server = OunceServer("t", retry=instant_retry(3, sleeps))
    attempts: list[int] = []

    @server.tool(name="t.go", effect=Effect.READ, deadline_ms=100)
    def go(ctx: ToolContext) -> Out:
        """Rate limited."""
        attempts.append(ctx.attempt)
        raise RateLimited("slow down", retry_after=5)

    result = await call(server, "t.go")
    assert result.is_error
    assert meta(result)["category"] == "rate_limit"
    assert meta(result)["retry_after_s"] == 5
    assert sleeps == []
    assert attempts == [1]


@pytest.mark.anyio
async def test_backoff_never_sleeps_past_the_deadline():
    slow = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=1.0, jitter=0)
    server = OunceServer("t", retry=slow)
    attempts: list[int] = []

    @server.tool(name="t.go", effect=Effect.READ, deadline_ms=300)
    def go(ctx: ToolContext) -> Out:
        """Always transient."""
        attempts.append(ctx.attempt)
        raise TransientError("blip")

    started = time.perf_counter()
    result = await call(server, "t.go")
    assert time.perf_counter() - started < 0.6
    assert attempts == [1]
    assert meta(result)["category"] == "transient"


@pytest.mark.anyio
async def test_retries_share_one_deadline():
    policy = RetryPolicy(max_attempts=10, base_delay=0.02, max_delay=0.02, jitter=0)
    server = OunceServer("t", retry=policy)
    attempts: list[int] = []

    @server.tool(name="t.go", effect=Effect.READ, deadline_ms=250)
    async def go(ctx: ToolContext) -> Out:
        """Transient, slowly."""
        attempts.append(ctx.attempt)
        await anyio.sleep(0.08)
        raise TransientError("blip")

    started = time.perf_counter()
    result = await call(server, "t.go")
    assert time.perf_counter() - started < 0.6
    assert result.is_error
    assert 2 <= len(attempts) < 10


@pytest.mark.anyio
async def test_retries_can_be_turned_off():
    server, attempts = flaky_server([TransientError("blip")] * 3, retry=instant_retry(1))
    assert (await call(server, "t.go")).is_error
    assert attempts == [1]


def test_backoff_doubles_up_to_the_cap():
    policy = RetryPolicy(max_attempts=9, base_delay=0.1, max_delay=2.0, jitter=0)
    assert [policy.delay(n) for n in (1, 2, 3, 4, 5, 6)] == [0.1, 0.2, 0.4, 0.8, 1.6, 2.0]
    assert policy.delay(20) == 2.0


def test_jitter_stays_inside_its_band_and_retry_after_wins():
    policy = RetryPolicy(base_delay=0.1, max_delay=2.0, jitter=0.2)
    delays = {policy.delay(1) for _ in range(200)}
    assert all(0.08 <= d <= 0.12 for d in delays)
    assert len(delays) > 1
    assert policy.delay(1, retry_after=9.0) == 9.0


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"base_delay": -1}, {"base_delay": 5, "max_delay": 1}, {"jitter": 2}],
)
def test_retry_policy_is_validated(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


# ------------------------------------------------------------------ EXE-3: ambiguous writes


class FakeResolver:
    def __init__(self, outcome: Any = None, *, error: Exception | None = None, delay: float = 0):
        self.outcome = outcome
        self.error = error
        self.delay = delay
        self.calls: list[WriteAttempt] = []

    async def resolve(self, attempt: WriteAttempt) -> Any:
        self.calls.append(attempt)
        if self.delay:
            await anyio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.outcome


def write_server(
    resolver: FakeResolver | None = None, effect: Effect = Effect.WRITE, **kwargs: Any
) -> tuple[OunceServer, list[str]]:
    server = OunceServer("t", retry=instant_retry(), write_resolver=resolver, **kwargs)
    effects: list[str] = []

    @server.tool(name="t.create", effect=effect, deadline_ms=50)
    async def create(name: str = "x") -> Out:
        """Create a record, then wait on a slow confirmation."""
        effects.append(name)
        await anyio.sleep(5)
        return Out()

    return server, effects


@pytest.mark.anyio
@pytest.mark.parametrize("effect", [Effect.WRITE, Effect.DESTRUCTIVE])
async def test_a_write_timeout_is_reported_as_ambiguous_and_not_repeated(effect):
    server, effects = write_server(effect=effect)
    result = await call(server, "t.create", {"name": "invoice-9"})
    assert result.is_error
    assert meta(result)["category"] == "ambiguous_write"
    assert meta(result)["error"] == "write_status_unknown"
    assert meta(result)["retryable"] is False
    assert "may or may not have been applied" in result.content[0].text
    assert effects == ["invoice-9"]


@pytest.mark.anyio
async def test_a_read_timeout_is_not_ambiguous():
    server, _ = write_server(effect=Effect.READ)
    assert meta(await call(server, "t.create"))["category"] == "transient"


@pytest.mark.anyio
async def test_a_resolver_that_knows_the_outcome_turns_the_timeout_into_a_result():
    resolver = FakeResolver(Out(ok=True, note="applied"))
    server, effects = write_server(resolver)
    result = await call(server, "t.create", {"name": "invoice-9"})
    assert not result.is_error
    assert result.structured_content["note"] == "applied"
    assert meta(result)["resolved_by"] == "write_resolver"
    assert effects == ["invoice-9"]
    (attempt,) = resolver.calls
    assert attempt.tool == "t.create"
    assert attempt.principal == "anonymous"
    assert dict(attempt.arguments) == {"name": "invoice-9"}
    assert attempt.reason == "write_status_unknown"


@pytest.mark.anyio
async def test_the_write_resolver_sees_the_principal_the_server_resolves():
    resolver = FakeResolver(Out(ok=True, note="applied"))
    server, _ = write_server(resolver, principal=lambda ctx: "alice")
    await call(server, "t.create", {"name": "invoice-9"})
    assert resolver.calls[0].principal == "alice"


@pytest.mark.anyio
async def test_a_resolver_can_answer_with_a_plain_dict():
    server, _ = write_server(FakeResolver({"ok": True, "note": "from dict"}))
    assert (await call(server, "t.create")).structured_content["note"] == "from dict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "resolver",
    [
        FakeResolver(None),
        FakeResolver(error=RuntimeError("store down at 10.0.0.9")),
        FakeResolver({"ok": "not a bool"}),
        FakeResolver(Out(), delay=5),
    ],
    ids=["unknown", "raises", "invalid", "slow"],
)
async def test_a_resolver_that_cannot_tell_leaves_the_write_ambiguous(resolver, monkeypatch):
    monkeypatch.setattr("ouncemcp.core.server.RESOLVE_TIMEOUT_S", 0.05)
    server, _ = write_server(resolver)
    result = await call(server, "t.create")
    assert meta(result)["category"] == "ambiguous_write"
    assert "10.0.0.9" not in result.content[0].text
    assert len(resolver.calls) == 1


@pytest.mark.anyio
async def test_the_resolver_is_only_asked_about_ambiguous_writes():
    resolver = FakeResolver(Out(note="should not be used"))
    server = OunceServer("t", retry=instant_retry(), write_resolver=resolver)

    @server.tool(name="t.ok", effect=Effect.WRITE)
    def ok() -> Out:
        """A write that works."""
        return Out(note="real")

    @server.tool(name="t.denied", effect=Effect.WRITE)
    def denied() -> Out:
        """A write that is refused."""
        raise PermissionDenied("no")

    @server.tool(name="t.slow_read", effect=Effect.READ, deadline_ms=50)
    async def slow_read() -> Out:
        """A read that times out."""
        await anyio.sleep(5)
        return Out()

    assert (await call(server, "t.ok")).structured_content["note"] == "real"
    assert meta(await call(server, "t.denied"))["category"] == "permission"
    assert meta(await call(server, "t.slow_read"))["category"] == "transient"
    assert resolver.calls == []


@pytest.mark.anyio
async def test_a_write_that_fails_after_sending_is_ambiguous_and_one_that_never_sent_is_not():
    class ConnectError(Exception):
        pass

    server = OunceServer("t", retry=instant_retry())

    @server.tool(name="t.after", effect=Effect.WRITE)
    def after() -> Out:
        """Fails after the request left."""
        raise ReadTimeout("read timed out")

    @server.tool(name="t.before", effect=Effect.WRITE)
    def before() -> Out:
        """Fails before the request left."""
        raise ConnectError("refused")

    assert meta(await call(server, "t.after"))["category"] == "ambiguous_write"
    assert meta(await call(server, "t.before"))["category"] == "transient"


# ------------------------------------------------------------------ EXE-6: limits


def echo_server(**kw: Any) -> tuple[OunceServer, list[Any]]:
    server = OunceServer("t", **kw)
    seen: list[Any] = []

    @server.tool(name="t.echo", effect=Effect.READ)
    def echo(note: str = "", data: dict[str, Any] | None = None) -> Out:
        """Echo."""
        seen.append((note, data))
        return Out(note=note[:10])

    return server, seen


def nested(depth: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth - 1):
        value = {"k": value}
    return value


@pytest.mark.anyio
async def test_oversized_requests_are_refused_before_the_handler_runs():
    server, seen = echo_server(limits=Limits(max_request_bytes=200))
    result = await call(server, "t.echo", {"note": "x" * 500})
    assert result.is_error
    assert meta(result)["error"] == "request_too_large"
    assert meta(result)["category"] == "validation"
    assert "limit is 200" in result.content[0].text
    assert seen == []


@pytest.mark.anyio
async def test_the_request_size_limit_is_inclusive():
    arguments = {"note": "x" * 100}
    size = len(json.dumps(arguments, separators=(",", ":")).encode())
    ok, _ = echo_server(limits=Limits(max_request_bytes=size))
    tight, _ = echo_server(limits=Limits(max_request_bytes=size - 1))
    assert not (await call(ok, "t.echo", arguments)).is_error
    assert (await call(tight, "t.echo", arguments)).is_error


@pytest.mark.anyio
async def test_over_nested_requests_are_refused():
    server, seen = echo_server(limits=Limits(max_depth=5))
    ok = await call(server, "t.echo", {"data": nested(4)})
    assert not ok.is_error
    result = await call(server, "t.echo", {"data": nested(5)})
    assert meta(result)["error"] == "request_too_deep"
    assert meta(result)["category"] == "validation"
    assert "limit is 5" in result.content[0].text
    assert len(seen) == 1


@pytest.mark.anyio
async def test_hostile_nesting_cannot_crash_the_check():
    server, seen = echo_server()
    result = await call(server, "t.echo", {"data": nested(50_000)})
    assert meta(result)["error"] == "request_too_deep"
    assert seen == []


@pytest.mark.parametrize(
    ("value", "depth"),
    [
        (1, 0),
        ("text", 0),
        ({}, 1),
        ([], 1),
        ({"a": 1}, 1),
        ([[1]], 2),
        ({"a": [{"b": 1}]}, 3),
        ({"a": 1, "b": {"c": {"d": []}}}, 4),
    ],
)
def test_nesting_depth(value, depth):
    assert nesting_depth(value, 100) == depth


def test_depth_counting_stops_early():
    assert nesting_depth(nested(10_000), 8) == 9


def test_check_request_ignores_empty_arguments():
    check_request(None, Limits())
    check_request({}, Limits())


WHO: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="alice")


async def wait_until(predicate: Any) -> None:
    with anyio.fail_after(2):
        while not predicate():
            await anyio.sleep(0.005)


def blocking_server(limit: int) -> tuple[OunceServer, anyio.Event, list[str]]:
    server = OunceServer("t", limits=Limits(max_concurrency=limit), principal=lambda ctx: WHO.get())
    release = anyio.Event()
    started: list[str] = []

    @server.tool(name="t.block", effect=Effect.READ)
    async def block() -> Out:
        """Wait to be released."""
        started.append(WHO.get())
        await release.wait()
        return Out()

    @server.tool(name="t.fail", effect=Effect.READ)
    def fail() -> Out:
        """Fail."""
        raise RuntimeError("boom")

    return server, release, started


@pytest.mark.anyio
async def test_one_principal_cannot_exceed_its_concurrency_limit():
    server, release, started = blocking_server(2)
    WHO.set("alice")
    first = asyncio.create_task(call(server, "t.block"))
    second = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: len(started) == 2)

    third = await call(server, "t.block")
    assert third.is_error
    assert meta(third)["error"] == "too_many_concurrent_calls"
    assert meta(third)["category"] == "rate_limit"
    assert meta(third)["retryable"] is True
    assert meta(third)["retry_after_s"] == 1
    assert started == ["alice", "alice"]

    release.set()
    assert not (await first).is_error
    assert not (await second).is_error
    assert not (await call(server, "t.block")).is_error


@pytest.mark.anyio
async def test_principals_do_not_share_a_concurrency_limit():
    server, release, started = blocking_server(1)
    WHO.set("alice")
    alice = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: started == ["alice"])
    WHO.set("bob")
    bob = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: started == ["alice", "bob"])
    WHO.set("alice")
    assert meta(await call(server, "t.block"))["category"] == "rate_limit"
    release.set()
    await asyncio.gather(alice, bob)


@pytest.mark.anyio
async def test_slots_are_released_after_failures_and_cancellations():
    server, release, started = blocking_server(1)
    WHO.set("carol")
    for _ in range(3):
        assert meta(await call(server, "t.fail"))["category"] == "internal"
    task = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: started == ["carol"])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert not (await call(server, "t.block")).is_error


class FakeToken:
    def __init__(self, subject: str | None, client_id: str = "client-1") -> None:
        self.subject = subject
        self.claims: dict[str, Any] = {}
        self.client_id = client_id


@pytest.mark.anyio
async def test_the_gate_uses_the_same_default_principal_as_cursors(monkeypatch):
    """Both read the access token through one resolver, so a token's subject counts as
    one caller whichever way it is carried, and different subjects do not share slots."""
    token = {"value": FakeToken("alice")}
    monkeypatch.setattr("ouncemcp.core.server.get_access_token", lambda: token["value"])
    server = OunceServer("t", limits=Limits(max_concurrency=1))
    release = anyio.Event()
    started: list[str] = []

    @server.tool(name="t.block", effect=Effect.READ)
    async def block() -> Out:
        """Wait to be released."""
        started.append(server._principal_of("t.block", None))
        await release.wait()
        return Out()

    first = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: started == ["alice"])
    assert meta(await call(server, "t.block"))["category"] == "rate_limit"
    token["value"] = FakeToken("bob")
    other = asyncio.create_task(call(server, "t.block"))
    await wait_until(lambda: started == ["alice", "bob"])
    release.set()
    await asyncio.gather(first, other)


def test_concurrency_gate_counts_and_cleans_up():
    gate = ConcurrencyGate(2)
    with gate.slot("a"), gate.slot("a"):
        assert gate.active("a") == 2
        with pytest.raises(Exception, match="2 calls in flight"), gate.slot("a"):
            pass
        with gate.slot("b"):
            assert gate.active("b") == 1
    assert gate.active("a") == 0
    assert gate._active == {}
    with pytest.raises(RuntimeError), gate.slot("a"):
        raise RuntimeError
    assert gate._active == {}


@pytest.mark.anyio
async def test_limits_apply_over_a_real_client_connection():
    server, _ = echo_server(limits=Limits(max_request_bytes=200))
    async with Client(server.fastmcp) as client:
        result = await client.call_tool("t.echo", {"note": "x" * 500}, raise_on_error=False)
        assert result.is_error
        assert result.meta["ouncemcp"]["error"] == "request_too_large"


@pytest.mark.anyio
async def test_output_size_stays_under_the_result_ceiling():
    server = OunceServer("t", budget=Budget(result_tokens=500, result_ceiling=1_000))

    class Big(BaseModel):
        text: str

    @server.tool(name="t.big", effect=Effect.READ)
    def big() -> Big:
        """Return far too much."""
        return Big(text="word " * 200_000)

    result = await call(server, "t.big")
    assert not result.is_error
    assert meta(result)["result_tokens"] <= 500
    assert len(result.content[0].text) < 5_000


def test_limits_are_validated_and_have_safe_defaults():
    limits = Limits()
    assert limits.default_deadline_ms == 30_000
    assert limits.max_request_bytes == 65_536
    assert limits.max_depth == 16
    assert limits.max_concurrency == 8
    for field in (
        "default_deadline_ms",
        "max_request_bytes",
        "max_depth",
        "max_concurrency",
        "max_error_chars",
    ):
        for bad in (0, -1, True, 1.5):
            with pytest.raises(ValueError, match=field):
                Limits(**{field: bad})
