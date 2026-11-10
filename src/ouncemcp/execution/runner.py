"""Run one tool call under a deadline, with bounded retries for reads (EXE-1, EXE-2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import anyio

from ouncemcp.core.effects import Effect
from ouncemcp.core.errors import RETRYABLE, DeadlineExceeded, classify
from ouncemcp.execution.retry import RetryPolicy

T = TypeVar("T")


async def run_with_policy(
    attempt: Callable[[int], Awaitable[T]],
    *,
    effect: Effect,
    deadline_s: float,
    retry: RetryPolicy,
    deadline_at: float,
) -> T:
    """Call ``attempt(n)`` until it succeeds, fails for good, or the deadline passes.

    ``deadline_at`` is an ``anyio.current_time()`` value that already includes
    ``deadline_s``. Cancellation of the surrounding task (a client cancelling the call)
    reaches ``attempt`` as an ordinary cancellation, so upstream awaits stop with it.

    Raises:
        DeadlineExceeded: the call ran out of time. The attempt was cancelled.
        Exception: whatever the last attempt raised, when it is not worth retrying.
    """
    number = 0
    while True:
        number += 1
        remaining = deadline_at - anyio.current_time()
        if remaining <= 0:
            raise DeadlineExceeded(deadline_s)
        try:
            with anyio.move_on_after(remaining) as scope:
                return await attempt(number)
        except Exception as exc:
            if not _worth_retrying(exc, effect, number, retry):
                raise
            failure = classify(exc, effect=effect, quiet=True)
            wait = retry.delay(number, failure.retry_after)
            if anyio.current_time() + wait >= deadline_at:
                raise
            await retry.sleep(wait)
            continue
        if scope.cancelled_caught:
            raise DeadlineExceeded(deadline_s)


def _worth_retrying(exc: Exception, effect: Effect, number: int, retry: RetryPolicy) -> bool:
    if effect is not Effect.READ or number >= retry.max_attempts:
        return False
    return classify(exc, effect=effect, quiet=True).category in RETRYABLE
