"""Results by reference and background jobs (spec RES-5, proposal 0013).

With ``OunceServer(handles=True)`` the server registers two tools:

- ``handles.read(handle, cursor, limit)`` pages through a large result a tool stored
  with ``await ctx.store_result(items)``, instead of sending it all at once.
- ``jobs.get(job)`` reports a background job started with ``await ctx.start_job(...)``:
  ``running``, ``done`` (with its summary and, for large results, a handle), ``failed``
  or ``interrupted``.

Handles and job ids are signed state tokens bound to the caller and tenant, so they
cannot be guessed or used by anyone else, and they expire. The data lives in the
server's store, so with a shared store (``RedisStore``) any replica can serve them and
they survive a restart. A job runs in the process that started it; if that process
stops, the job's lease runs out.

**Resuming.** A job started from a closure cannot be run again elsewhere, so
``jobs.get`` reports it ``interrupted``. A *named* job (``@server.job("name")`` and
``ctx.start_job("name", args)``) stores its name and JSON arguments, so the next
``jobs.get`` after its lease ran out, on any replica, starts it again from the
beginning, up to ``max_attempts`` runs in all. A claim in the store makes sure only one
replica restarts each attempt. Named jobs must therefore be safe to run more than once
(reports, exports, reads); give writes their own idempotency.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
import typing
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_core import to_jsonable_python

from ouncemcp.core.errors import NotFound, classify
from ouncemcp.core.pagination import Cursor, Paginated
from ouncemcp.execution.store import OunceStore

logger = logging.getLogger("ouncemcp")

HANDLE_NS = "handle"
JOB_NS = "job"
JOB_CLAIM_NS = "job-claim"
HANDLE_KIND = "handle"
JOB_KIND = "job"
DEFAULT_HANDLE_TTL_S = 3600
DEFAULT_JOB_TTL_S = 3600
JOB_LEASE_S = 30
HEARTBEAT_S = 10
MAX_STORED_ITEMS = 100_000

JobState = Literal["running", "done", "failed", "interrupted"]


def new_id() -> str:
    return secrets.token_urlsafe(12)


def binding(tenant: str | None) -> list[Any]:
    """The request part of a handle or job token: bound to the caller's tenant too."""
    return ["ouncemcp-ref", tenant]


async def put_items(store: OunceStore, items: Sequence[Any], *, ttl_s: float) -> str:
    if len(items) > MAX_STORED_ITEMS:
        raise ValueError(f"store_result keeps at most {MAX_STORED_ITEMS} items")
    ref = new_id()
    await store.put(
        HANDLE_NS,
        ref,
        {"items": to_jsonable_python(list(items), fallback=str), "created": time.time()},
        ttl_s=ttl_s,
    )
    return ref


async def get_items(store: OunceStore, ref: str) -> list[Any]:
    record = await store.get(HANDLE_NS, ref)
    if record is None:
        raise NotFound(
            "That result has expired or does not exist.",
            hint="Run the original tool again to get a new handle.",
        )
    return list(record.get("items") or [])


@dataclass(frozen=True)
class JobKind:
    """A named job that can be started again after a restart (``@server.job``)."""

    name: str
    fn: Callable[..., Awaitable[Any]]
    max_attempts: int = 3

    def run(self, args: Mapping[str, Any], attempt: int) -> Awaitable[Any]:
        params = list(inspect.signature(self.fn).parameters.values())
        value: Any = dict(args)
        if params:
            hints = typing.get_type_hints(self.fn)
            annotation = hints.get(params[0].name)
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                value = annotation.model_validate(args)
        return self.fn(value, attempt) if len(params) > 1 else self.fn(value)


def check_job_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """Arguments of a named job are stored as JSON, so they must round-trip."""
    try:
        data = json.loads(json.dumps(dict(args)))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Job arguments must be JSON (they are stored to resume the job): {exc}"
        ) from exc
    return data  # type: ignore[no-any-return]


class JobRunner:
    """Runs jobs in this process and records their state in the store."""

    def __init__(self, store: OunceStore) -> None:
        self.store = store
        self.kinds: dict[str, JobKind] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._meta: dict[str, dict[str, Any]] = {}

    def register(self, kind: JobKind) -> None:
        if kind.name in self.kinds:
            raise ValueError(f"Job '{kind.name}' is already registered")
        if kind.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.kinds[kind.name] = kind

    async def start(
        self,
        work: Callable[[], Awaitable[Any]],
        *,
        ttl_s: float,
        spill: Callable[[Any], Awaitable[dict[str, Any]]],
        kind: str | None = None,
        args: Mapping[str, Any] | None = None,
        attempt: int = 1,
        ref: str | None = None,
    ) -> str:
        ref = ref or new_id()
        meta: dict[str, Any] = {"attempt": attempt}
        if kind is not None:
            meta.update(kind=kind, args=dict(args or {}), ttl_s=ttl_s)
        self._meta[ref] = meta
        await self._write(ref, {"state": "running"}, ttl_s=ttl_s)
        task = asyncio.ensure_future(self._run(ref, work, ttl_s, spill))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return ref

    async def resume(
        self,
        ref: str,
        record: Mapping[str, Any],
        spill: Callable[[Any], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        """Start an interrupted named job again. Returns its new status, or None when it
        cannot be resumed (a closure job, an unknown name, or no attempts left)."""
        kind = self.kinds.get(str(record.get("kind")))
        attempt = int(record.get("attempt", 1))
        if kind is None or attempt >= kind.max_attempts:
            return None
        ttl_s = float(record.get("ttl_s", DEFAULT_JOB_TTL_S))
        claimed = await self.store.put_if_absent(
            JOB_CLAIM_NS, f"{ref}#{attempt + 1}", {"at": time.time()}, ttl_s=ttl_s
        )
        if claimed:
            args = dict(record.get("args") or {})
            await self.start(
                lambda: kind.run(args, attempt + 1),
                ttl_s=ttl_s,
                spill=spill,
                kind=kind.name,
                args=args,
                attempt=attempt + 1,
                ref=ref,
            )
        # Either this replica restarted it, or another one just did.
        return {"state": "running", "attempt": attempt + 1}

    async def _write(self, ref: str, record: dict[str, Any], *, ttl_s: float) -> None:
        record = {**self._meta.get(ref, {}), **record}
        record = {**record, "lease_until": time.time() + JOB_LEASE_S, "updated": time.time()}
        await self.store.put(JOB_NS, ref, record, ttl_s=ttl_s)

    async def _run(
        self,
        ref: str,
        work: Callable[[], Awaitable[Any]],
        ttl_s: float,
        spill: Callable[[Any], Awaitable[dict[str, Any]]],
    ) -> None:
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_S)
                await self._write(ref, {"state": "running"}, ttl_s=ttl_s)

        beat = asyncio.ensure_future(heartbeat())
        try:
            result = await work()
            outcome = await spill(result)
            await self.store.put(
                JOB_NS,
                ref,
                {**self._meta.get(ref, {}), "state": "done", **outcome, "updated": time.time()},
                ttl_s=ttl_s,
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the caller, never raised
            failure = classify(exc, tool="job")
            await self.store.put(
                JOB_NS,
                ref,
                {
                    **self._meta.get(ref, {}),
                    "state": "failed",
                    "error": {
                        "category": failure.category,
                        "code": failure.code,
                        "message": failure.message,
                    },
                    "updated": time.time(),
                },
                ttl_s=ttl_s,
            )
        finally:
            beat.cancel()
            self._meta.pop(ref, None)

    async def status(self, ref: str) -> dict[str, Any]:
        record = await self.store.get(JOB_NS, ref)
        if record is None:
            raise NotFound("That job has expired or does not exist.", hint="Start the job again.")
        if record.get("state") == "running" and float(record.get("lease_until", 0)) < time.time():
            return {**record, "state": "interrupted"}
        return record

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


# ------------------------------------------------------------------ built-in tool models


class HandlePage(BaseModel):
    items: Annotated[list[Any], Paginated]
    total: int
    next_cursor: Cursor = None


@dataclass(frozen=True)
class JobResult:
    """What a job returns: a small ``summary`` and, optionally, many ``items`` that are
    stored behind a handle instead of being sent at once."""

    summary: BaseModel | dict[str, Any] | None = None
    items: Sequence[Any] | None = None


class JobStatus(BaseModel):
    state: JobState
    result: dict[str, Any] | None = Field(None, description="The job's summary, when done")
    handle: str | None = Field(None, description="Read the full result with handles.read")
    items: int | None = Field(None, description="How many items the handle holds")
    error: str | None = None
    retry: str | None = None
    attempt: int | None = Field(None, description="Which run of a resumable job this is")
