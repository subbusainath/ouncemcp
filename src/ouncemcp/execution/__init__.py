"""Execution semantics: deadlines, cancellation, bounded retries, idempotency, and approvals.

Spec: docs/spec.md sections 8.1.5 and 10. Milestone: M1 to M2. M1 has deadlines, bounded
read retries, request limits and the write-resolver seam. Idempotency (EXE-4) and
approvals are M2.
"""

from ouncemcp.execution.limits import (
    ConcurrencyGate,
    Limits,
    check_request,
    nesting_depth,
)
from ouncemcp.execution.resolver import RESOLVE_TIMEOUT_S, WriteAttempt, WriteResolver
from ouncemcp.execution.retry import RetryPolicy

__all__ = [
    "RESOLVE_TIMEOUT_S",
    "ConcurrencyGate",
    "Limits",
    "RetryPolicy",
    "WriteAttempt",
    "WriteResolver",
    "check_request",
    "nesting_depth",
]
