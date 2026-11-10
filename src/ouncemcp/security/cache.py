"""A private cache partitioned by authorization context (spec SEC-5).

Two callers who would get different answers must never share a cache entry. Entries
are keyed by the caller's principal, tenant and granted scopes as well as the key the
handler chooses, so a cached result for one tenant cannot be served to another, and a
caller whose scopes change gets fresh results. Handlers use it through ``ctx.cache``.

In-memory and per process. Replicas do not share it, which is safe (a miss, never a leak).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any

from ouncemcp.security.identity import Identity

DEFAULT_TTL_S = 60.0
DEFAULT_MAX_ENTRIES = 1024


def partition_key(identity: Identity) -> str:
    """A digest of everything about the caller that can change an answer."""
    material = json.dumps(
        [
            identity.principal,
            identity.tenant,
            sorted(identity.scopes),
            identity.authenticated,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class PartitionedCache:
    """An LRU cache with expiry whose entries belong to one authorization context."""

    def __init__(
        self,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[tuple[str, Hashable], tuple[float, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._data)

    def get(self, identity: Identity, key: Hashable, default: Any = None) -> Any:
        slot = (partition_key(identity), key)
        entry = self._data.get(slot)
        if entry is None:
            return default
        expires, value = entry
        if expires <= self._clock():
            del self._data[slot]
            return default
        self._data.move_to_end(slot)
        return value

    def set(
        self, identity: Identity, key: Hashable, value: Any, *, ttl_s: float | None = None
    ) -> None:
        slot = (partition_key(identity), key)
        self._data[slot] = (self._clock() + (ttl_s or self.ttl_s), value)
        self._data.move_to_end(slot)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self, identity: Identity | None = None) -> None:
        """Drop every entry, or only the entries of ``identity``'s partition."""
        if identity is None:
            self._data.clear()
            return
        part = partition_key(identity)
        for slot in [s for s in self._data if s[0] == part]:
            del self._data[slot]


@dataclass(frozen=True)
class CacheView:
    """``ctx.cache``: the shared cache, seen through the current caller's partition."""

    cache: PartitionedCache
    identity: Identity
    namespace: str

    def get(self, key: Hashable, default: Any = None) -> Any:
        return self.cache.get(self.identity, (self.namespace, key), default)

    def set(self, key: Hashable, value: Any, *, ttl_s: float | None = None) -> None:
        self.cache.set(self.identity, (self.namespace, key), value, ttl_s=ttl_s)

    async def get_or_set(
        self, key: Hashable, make: Callable[[], Awaitable[Any]], *, ttl_s: float | None = None
    ) -> Any:
        """Return the cached value, or await ``make()`` and cache what it returns."""
        missing = object()
        value = self.get(key, missing)
        if value is missing:
            value = await make()
            self.set(key, value, ttl_s=ttl_s)
        return value
