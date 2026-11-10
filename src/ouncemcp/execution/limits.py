"""Request limits, deadlines, and per-principal concurrency (spec EXE-1, EXE-6)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from ouncemcp.core.errors import (
    DEFAULT_MAX_ERROR_CHARS,
    RequestTooDeep,
    RequestTooLarge,
    TooManyCalls,
)


@dataclass(frozen=True)
class Limits:
    """Bounds on what one call may cost the server.

    Attributes:
        default_deadline_ms: Deadline for a tool that does not set ``deadline_ms``.
        max_request_bytes: Largest JSON-encoded argument object accepted.
        max_depth: Deepest nesting of objects and lists accepted in arguments.
        max_concurrency: Calls one principal may have in flight; more are refused at once.
        max_error_chars: Longest error message sent to a caller.
    """

    default_deadline_ms: int = 30_000
    max_request_bytes: int = 65_536
    max_depth: int = 16
    max_concurrency: int = 8
    max_error_chars: int = DEFAULT_MAX_ERROR_CHARS

    def __post_init__(self) -> None:
        for name in (
            "default_deadline_ms",
            "max_request_bytes",
            "max_depth",
            "max_concurrency",
            "max_error_chars",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"Limits.{name} must be a positive integer, got {value!r}")


def nesting_depth(value: Any, stop_after: int) -> int:
    """Nesting depth of ``value``, counted iteratively and abandoned past ``stop_after``.

    A scalar has depth 0 and ``{}`` has depth 1. Not recursive, so hostile input cannot
    exhaust the stack.
    """
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, (list, tuple)):
            children = item
        else:
            continue
        depth += 1
        deepest = max(deepest, depth)
        if deepest > stop_after:
            return deepest
        stack.extend((child, depth) for child in children)
    return deepest


def check_request(arguments: dict[str, Any] | None, limits: Limits) -> None:
    """Raise ``RequestTooDeep`` or ``RequestTooLarge`` if the arguments break a limit."""
    if not arguments:
        return
    depth = nesting_depth(arguments, limits.max_depth)
    if depth > limits.max_depth:
        raise RequestTooDeep(depth, limits.max_depth)
    size = len(json.dumps(arguments, separators=(",", ":"), default=str).encode())
    if size > limits.max_request_bytes:
        raise RequestTooLarge(size, limits.max_request_bytes)


class ConcurrencyGate:
    """Counts in-flight calls per principal and refuses calls over the limit.

    Refusing, rather than queueing, keeps one noisy caller from tying up workers and
    tells the agent to back off. Counts are exact because there is no await between the
    check and the increment.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._active: dict[str, int] = {}

    def active(self, principal: str) -> int:
        return self._active.get(principal, 0)

    @contextmanager
    def slot(self, principal: str) -> Iterator[None]:
        count = self._active.get(principal, 0)
        if count >= self.limit:
            raise TooManyCalls(self.limit)
        self._active[principal] = count + 1
        try:
            yield
        finally:
            remaining = self._active[principal] - 1
            if remaining:
                self._active[principal] = remaining
            else:
                del self._active[principal]
