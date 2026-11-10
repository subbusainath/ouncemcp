"""Bounded retries for transient read failures (spec EXE-2)."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import anyio


@dataclass(frozen=True)
class RetryPolicy:
    """How a read is retried after a transient or rate-limit failure.

    Only tools declared ``Effect.READ`` are ever retried. Writes are never retried
    automatically. Every wait must fit inside the call's remaining deadline, or the
    failure is returned at once.

    Attributes:
        max_attempts: Total tries, including the first. ``1`` turns retries off.
        base_delay: Wait before the second try, in seconds. It doubles each time.
        max_delay: Longest backoff wait. Does not cap an upstream ``Retry-After``.
        jitter: Fraction (0 to 1) by which each backoff is randomly shortened or
            lengthened, so many callers do not retry together.
        sleep: How to wait. Replaceable in tests.
    """

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 2.0
    jitter: float = 0.2
    sleep: Callable[[float], Awaitable[None]] = field(default=anyio.sleep, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be an integer >= 1")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("RetryPolicy needs 0 <= base_delay <= max_delay")
        if not 0 <= self.jitter <= 1:
            raise ValueError("RetryPolicy.jitter must be between 0 and 1")

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait after failed attempt number ``attempt`` (1 is the first).

        An upstream ``retry_after`` wins over the backoff.
        """
        if retry_after is not None:
            return retry_after
        backoff = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        if self.jitter:
            backoff *= 1 + random.uniform(-self.jitter, self.jitter)
        return max(0.0, backoff)
