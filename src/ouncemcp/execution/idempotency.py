"""Idempotent writes (spec EXE-4, proposal 0011).

A tool registered with ``idempotency=True`` (or ``Idempotency(...)``) runs at most once
per key. The key is the caller's principal and tenant, the tool, and a digest of the
normalized arguments; a client can send its own key in ``_meta`` instead
(``ouncemcp/idempotency_key`` or ``idempotencyKey``).

Each key has one record in the store:

- ``running``: claimed with ``put_if_absent``. A duplicate waits briefly for the result,
  then gets a retryable ``write_in_progress`` error. It never runs the handler.
- ``done``: holds the result. A duplicate gets the same result back, marked
  ``replayed``, for ``ttl_s`` (default 24 hours).
- ``unknown``: the handler failed in a way that may have changed something. A duplicate
  gets ``ambiguous_write`` and must check the state, or use a new key.

A failure that cannot have changed anything (validation, permission, not found, rate
limit, or a request that was never sent) clears the record, so a corrected retry runs.

Deadlines: the call's deadline bounds how long the caller waits, not the write. If it
passes, the handler keeps running in the background for ``grace_s`` (default 60 s), and
the caller is told to call again with the same arguments. When the write finishes, its
result is stored, so the retry gets it instead of writing twice. This is what makes a
timeout after an upstream write produce no duplicate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import anyio
from pydantic import BaseModel

from ouncemcp.core.errors import (
    AmbiguousWrite,
    ErrorCategory,
    InvalidInput,
    OunceError,
    TransientError,
    classify,
)
from ouncemcp.execution.store import OunceStore, store_key

logger = logging.getLogger("ouncemcp")

NAMESPACE = "idempotency"
CLIENT_KEY_FIELDS = ("ouncemcp/idempotency_key", "idempotencyKey")
_SAFE_TO_FORGET: frozenset[ErrorCategory] = frozenset(
    {"validation", "authentication", "permission", "not_found", "rate_limit"}
)
POLL_S = 0.05


@dataclass(frozen=True)
class Idempotency:
    """Settings for one tool.

    Attributes:
        ttl_s: How long a finished write's result is replayed to duplicates.
        grace_s: How long a write may keep running after the caller's deadline.
        wait_s: How long a duplicate waits for a running original before it is told to
            retry (never longer than its own deadline).
    """

    ttl_s: float = 86_400
    grace_s: float = 60
    wait_s: float = 5

    def __post_init__(self) -> None:
        if self.ttl_s <= 0 or self.grace_s <= 0 or self.wait_s < 0:
            raise ValueError("Idempotency ttl_s and grace_s must be positive, wait_s >= 0")


def as_idempotency(value: bool | Idempotency | None) -> Idempotency | None:
    if value is None or value is False:
        return None
    if value is True:
        return Idempotency()
    if isinstance(value, Idempotency):
        return value
    raise TypeError("idempotency must be True, False or an Idempotency(...)")


class WriteInProgress(TransientError):
    code = "write_in_progress"


class WriteStillRunning(AmbiguousWrite):
    code = "write_still_running"


class WriteOutcomeUnknown(AmbiguousWrite):
    code = "write_status_unknown"


class KeyReused(InvalidInput):
    code = "idempotency_key_reused"


def client_key(meta: Mapping[str, Any] | None) -> str | None:
    if not meta:
        return None
    for name in CLIENT_KEY_FIELDS:
        value = meta.get(name)
        if isinstance(value, str) and value:
            return value[:200]
    return None


@dataclass(frozen=True)
class Claim:
    key: str
    digest: str


def make_claim(
    *,
    tool: str,
    principal: str,
    tenant: str | None,
    arguments: Mapping[str, Any],
    supplied: str | None,
) -> Claim:
    digest = store_key("args", arguments)
    if supplied is not None:
        return Claim(store_key("client", principal, tenant, tool, supplied), digest)
    return Claim(store_key("args", principal, tenant, tool, digest), digest)


class IdempotencyGuard:
    """Runs idempotent writes against an ``OunceStore``."""

    def __init__(self, store: OunceStore) -> None:
        self.store = store
        self._background: set[asyncio.Task[Any]] = set()

    async def run(
        self,
        claim: Claim,
        settings: Idempotency,
        execute: Callable[[], Awaitable[BaseModel]],
        *,
        deadline_at: float,
    ) -> tuple[Any, bool]:
        """Return ``(result, replayed)``. ``result`` is a model, or a JSON dict on replay."""
        for _ in range(3):
            record = await self.store.get(NAMESPACE, claim.key)
            if record is not None:
                outcome = await self._existing(claim, settings, record, deadline_at)
                if outcome is not None:
                    return outcome, True
                continue
            lease = settings.grace_s + max(0.0, deadline_at - anyio.current_time()) + 5
            running = {"state": "running", "args": claim.digest, "since": time.time()}
            if await self.store.put_if_absent(NAMESPACE, claim.key, running, ttl_s=lease):
                return await self._first(claim, settings, execute, deadline_at), False
        raise WriteInProgress(
            "The same write is already being handled.", hint="Retry in a moment.", retry_after=1
        )

    async def _existing(
        self, claim: Claim, settings: Idempotency, record: dict[str, Any], deadline_at: float
    ) -> Any | None:
        if record.get("args") != claim.digest:
            raise KeyReused(
                "This idempotency key was already used with different arguments.",
                hint="Use a new key for a different request.",
            )
        state = record.get("state")
        if state == "done":
            return record.get("result")
        if state == "unknown":
            raise WriteOutcomeUnknown(
                "An earlier attempt of this exact write failed after it may have taken "
                "effect. Check the current state before trying again.",
                hint="To force a new attempt, send a new idempotency key.",
            )
        give_up = min(deadline_at, anyio.current_time() + settings.wait_s)
        while anyio.current_time() < give_up:
            await anyio.sleep(POLL_S)
            latest = await self.store.get(NAMESPACE, claim.key)
            if latest is None:
                return None
            if latest.get("state") != "running":
                return await self._existing(claim, settings, latest, deadline_at)
        raise WriteInProgress(
            "The same write is still running.",
            hint="Call again with the same arguments to get its result.",
            retry_after=1,
        )

    async def _first(
        self,
        claim: Claim,
        settings: Idempotency,
        execute: Callable[[], Awaitable[BaseModel]],
        deadline_at: float,
    ) -> BaseModel:
        task = asyncio.ensure_future(self._settle(claim, settings, execute))
        self._background.add(task)
        task.add_done_callback(self._forget_task)
        remaining = max(0.0, deadline_at - anyio.current_time())
        try:
            with anyio.fail_after(remaining):
                return await asyncio.shield(task)
        except TimeoutError:
            raise WriteStillRunning(
                "The write did not finish before the deadline and is still running.",
                hint=(
                    "Call again with the same arguments: you get its result once it "
                    "finishes, and it will not run twice."
                ),
            ) from None

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled():
            task.exception()  # retrieved here so a late failure is not reported twice

    async def _settle(
        self,
        claim: Claim,
        settings: Idempotency,
        execute: Callable[[], Awaitable[BaseModel]],
    ) -> BaseModel:
        try:
            with anyio.fail_after(settings.grace_s):
                result = await execute()
        except BaseException as exc:
            await self._record_failure(claim, exc)
            raise
        await self.store.put(
            NAMESPACE,
            claim.key,
            {"state": "done", "args": claim.digest, "result": result.model_dump(mode="json")},
            ttl_s=settings.ttl_s,
        )
        return result

    async def _record_failure(self, claim: Claim, exc: BaseException) -> None:
        safe = False
        if isinstance(exc, Exception):
            failure = classify(exc, quiet=True)
            safe = failure.category in _SAFE_TO_FORGET or failure.code == "upstream_unreachable"
            if isinstance(exc, OunceError) and exc.category in _SAFE_TO_FORGET:
                safe = True
        try:
            if safe:
                await self.store.delete(NAMESPACE, claim.key)
            else:
                await self.store.put(
                    NAMESPACE,
                    claim.key,
                    {"state": "unknown", "args": claim.digest},
                    ttl_s=3600,
                )
        except Exception:
            logger.warning("ouncemcp: could not record a failed write", exc_info=True)

    async def drain(self) -> None:
        """Wait for background writes (tests and shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
