"""Shared state for idempotency, approvals, handles and jobs (proposal 0011).

Correctness across replicas must not depend on process memory (spec 9.2). Everything
in OunceMCP that has to survive a restart or be seen by another replica goes through
one ``OunceStore``:

- ``MemoryStore``: the default. Atomic and fast, but one process only.
- ``RedisStore``: atomic across replicas (``SET NX``). Takes a ``redis.asyncio`` client.
- ``KeyValueStore``: any ``py-key-value-aio`` backend (disk, Postgres, DynamoDB and
  others). Data is shared, but ``put_if_absent`` is only atomic within one process,
  because the key-value protocol has no compare-and-set. Use Redis, or one replica,
  when exactly-once claims matter.

Values are JSON objects. Keys are namespaced (``ns``) and digested, so no caller data
appears in key names, and every entry has an expiry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic_core import to_jsonable_python

Value = dict[str, Any]


def store_key(*parts: Any) -> str:
    """A stable, opaque key for any JSON-able parts (principal, tenant, digest...)."""
    text = json.dumps(to_jsonable_python(list(parts), fallback=str), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:40]


@runtime_checkable
class OunceStore(Protocol):
    """What the framework needs from shared storage."""

    async def get(self, ns: str, key: str) -> Value | None: ...

    async def put(self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float) -> None: ...

    async def put_if_absent(
        self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float
    ) -> bool:
        """Store ``value`` only if ``key`` is unset. True if this call stored it."""
        ...

    async def delete(self, ns: str, key: str) -> bool: ...

    @property
    def shared(self) -> bool:
        """True when ``put_if_absent`` is atomic across processes."""
        ...


def _plain(value: Mapping[str, Any]) -> Value:
    out = to_jsonable_python(dict(value), fallback=str)
    if not isinstance(out, dict):
        raise TypeError("store values must be JSON objects")
    return out


class MemoryStore:
    """In-process store. Atomic within one event loop; lost on restart."""

    shared = False

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[tuple[str, str], tuple[float, Value]] = {}
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        self._expire()
        return len(self._data)

    def _expire(self) -> None:
        now = self._clock()
        for slot in [s for s, (exp, _) in self._data.items() if exp <= now]:
            del self._data[slot]

    async def get(self, ns: str, key: str) -> Value | None:
        entry = self._data.get((ns, key))
        if entry is None:
            return None
        if entry[0] <= self._clock():
            del self._data[(ns, key)]
            return None
        return json.loads(json.dumps(entry[1]))

    async def put(self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float) -> None:
        self._data[(ns, key)] = (self._clock() + ttl_s, _plain(value))

    async def put_if_absent(
        self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float
    ) -> bool:
        async with self._lock:
            if await self.get(ns, key) is not None:
                return False
            await self.put(ns, key, value, ttl_s=ttl_s)
            return True

    async def delete(self, ns: str, key: str) -> bool:
        return self._data.pop((ns, key), None) is not None


class RedisStore:
    """A store on a ``redis.asyncio.Redis`` client; claims are atomic across replicas."""

    shared = True

    def __init__(self, client: Any, *, prefix: str = "ouncemcp") -> None:
        self._client = client
        self._prefix = prefix

    def _name(self, ns: str, key: str) -> str:
        return f"{self._prefix}:{ns}:{key}"

    async def get(self, ns: str, key: str) -> Value | None:
        raw = await self._client.get(self._name(ns, key))
        if raw is None:
            return None
        return json.loads(raw)

    async def put(self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float) -> None:
        await self._client.set(
            self._name(ns, key), json.dumps(_plain(value)), px=max(1, int(ttl_s * 1000))
        )

    async def put_if_absent(
        self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float
    ) -> bool:
        stored = await self._client.set(
            self._name(ns, key),
            json.dumps(_plain(value)),
            px=max(1, int(ttl_s * 1000)),
            nx=True,
        )
        return bool(stored)

    async def delete(self, ns: str, key: str) -> bool:
        return bool(await self._client.delete(self._name(ns, key)))


class KeyValueStore:
    """Any ``py-key-value-aio`` store. ``put_if_absent`` is atomic per process only."""

    shared = False

    def __init__(self, kv: Any) -> None:
        self._kv = kv
        self._lock = asyncio.Lock()

    async def get(self, ns: str, key: str) -> Value | None:
        value = await self._kv.get(key, collection=ns)
        return dict(value) if value is not None else None

    async def put(self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float) -> None:
        await self._kv.put(key, _plain(value), collection=ns, ttl=ttl_s)

    async def put_if_absent(
        self, ns: str, key: str, value: Mapping[str, Any], *, ttl_s: float
    ) -> bool:
        async with self._lock:
            if await self.get(ns, key) is not None:
                return False
            await self.put(ns, key, value, ttl_s=ttl_s)
            return True

    async def delete(self, ns: str, key: str) -> bool:
        return bool(await self._kv.delete(key, collection=ns))
