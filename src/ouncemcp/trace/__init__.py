"""Per-call traces: tokens, latency, retries and outcomes (spec BUD-5, SEC-11, proposal 0009).

``OunceServer(trace=...)`` takes a sink (or a list of sinks). Every tool call, including
discovery meta-tools and calls they proxy, produces one ``CallTrace``:

- ``result_tokens`` as the model receives the result, counted with the server's tokenizer,
  with the budget, whether shaping truncated it, and the tokenizer name (BUD-5);
- latency, attempts (read retries), outcome, error category and code;
- the caller as a keyed digest by default, never the raw token.

Traces are redacted by default (SEC-11). Arguments are not recorded unless the tracer
is built with ``arguments=True``, and then secret-looking keys are dropped and strings
are scrubbed of credentials, URLs, hosts, paths, email addresses and phone numbers.
Error messages are never recorded, only their category and code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ouncemcp.core.errors import redact

logger = logging.getLogger("ouncemcp.trace")

Outcome = Literal["ok", "error", "input_required"]
PrincipalMode = Literal["hash", "plain", "omit"]


@dataclass(frozen=True)
class CallTrace:
    """One tool call as the server handled it."""

    tool: str
    outcome: Outcome
    started_at: float
    duration_ms: float
    result_tokens: int
    tokenizer: str
    budget: int | None = None
    truncated: bool = False
    attempts: int = 1
    effect: str | None = None
    error_category: str | None = None
    error_code: str | None = None
    principal: str | None = None
    parent: str | None = None
    resolved_by: str | None = None
    arguments: Mapping[str, Any] | None = None
    trace_id: str = field(default_factory=lambda: secrets.token_hex(8))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@runtime_checkable
class TraceSink(Protocol):
    def emit(self, trace: CallTrace) -> None: ...


class MemorySink:
    """Keeps the most recent traces in memory (tests, ``ouncemcp dev``)."""

    def __init__(self, max_traces: int = 1000) -> None:
        self.traces: deque[CallTrace] = deque(maxlen=max_traces)

    def emit(self, trace: CallTrace) -> None:
        self.traces.append(trace)

    def __iter__(self) -> Any:
        return iter(list(self.traces))

    def __len__(self) -> int:
        return len(self.traces)


class JsonlSink:
    """Appends one JSON object per call to a file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, trace: CallTrace) -> None:
        line = json.dumps(trace.to_dict(), separators=(",", ":"), sort_keys=True, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class LoggingSink:
    """Logs one line per call to the ``ouncemcp.trace`` logger."""

    def __init__(self, level: int = logging.INFO) -> None:
        self.level = level

    def emit(self, trace: CallTrace) -> None:
        logger.log(self.level, "%s", json.dumps(trace.to_dict(), default=str, sort_keys=True))


class CallbackSink:
    """Calls a function with each trace."""

    def __init__(self, callback: Callable[[CallTrace], None]) -> None:
        self.callback = callback

    def emit(self, trace: CallTrace) -> None:
        self.callback(trace)


# ------------------------------------------------------------------ redaction (SEC-11)

SECRET_KEY = re.compile(
    r"(?i)(pass(word|wd)?|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|"
    r"authorization|auth|credential|cookie|session|ssn|card|cvv|iban|account[_\-]?number)"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{7,}\d(?![\w.])")
MAX_TRACE_STRING = 200
MAX_TRACE_ITEMS = 20
MAX_TRACE_DEPTH = 4


def redact_text(text: str) -> str:
    """``errors.redact`` plus personal data: email addresses and phone numbers."""
    text = _EMAIL.sub("<email>", text)
    text = _PHONE.sub("<phone>", text)
    text = redact(text)
    if len(text) > MAX_TRACE_STRING:
        text = text[: MAX_TRACE_STRING - 1] + "…"
    return text


def redact_value(value: Any, depth: int = 0) -> Any:
    """A copy of ``value`` that is safe to keep in a trace."""
    if depth >= MAX_TRACE_DEPTH:
        return "<nested>"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= MAX_TRACE_ITEMS:
                out["<more>"] = len(value) - MAX_TRACE_ITEMS
                break
            name = str(key)
            out[name] = "<redacted>" if SECRET_KEY.search(name) else redact_value(item, depth + 1)
        return out
    if isinstance(value, Sequence | set | frozenset):
        items = list(value)
        kept = [redact_value(v, depth + 1) for v in items[:MAX_TRACE_ITEMS]]
        if len(items) > MAX_TRACE_ITEMS:
            kept.append(f"<{len(items) - MAX_TRACE_ITEMS} more>")
        return kept
    return f"<{type(value).__name__}>"


class Tracer:
    """Builds redacted traces and sends them to sinks. A failing sink never fails a call."""

    def __init__(
        self,
        sinks: TraceSink | Iterable[TraceSink],
        *,
        arguments: bool = False,
        principals: PrincipalMode = "hash",
        key: bytes | None = None,
    ) -> None:
        self.sinks: tuple[TraceSink, ...] = (
            (sinks,) if isinstance(sinks, TraceSink) else tuple(sinks)
        )
        if not all(isinstance(s, TraceSink) for s in self.sinks):
            raise TypeError("trace sinks need an emit(trace) method")
        if principals not in ("hash", "plain", "omit"):
            raise ValueError("principals must be 'hash', 'plain' or 'omit'")
        self.arguments = arguments
        self.principals: PrincipalMode = principals
        self._key = key or secrets.token_bytes(16)

    @classmethod
    def coerce(cls, value: Any) -> Tracer | None:
        if value is None or isinstance(value, Tracer):
            return value
        return cls(value)

    def principal(self, principal: str | None) -> str | None:
        if principal is None or self.principals == "omit":
            return None
        if self.principals == "plain":
            return principal
        digest = hmac.new(self._key, principal.encode(), hashlib.sha256).hexdigest()[:12]
        return f"p:{digest}"

    def record(self, *, principal: str | None, arguments: Any, **fields: Any) -> CallTrace:
        trace = CallTrace(
            principal=self.principal(principal),
            arguments=redact_value(arguments) if self.arguments and arguments else None,
            **fields,
        )
        for sink in self.sinks:
            try:
                sink.emit(trace)
            except Exception:
                logger.warning("ouncemcp: trace sink %r failed", sink, exc_info=True)
        return trace


def now() -> float:
    return time.time()


__all__ = [
    "CallTrace",
    "CallbackSink",
    "JsonlSink",
    "LoggingSink",
    "MemorySink",
    "TraceSink",
    "Tracer",
    "redact_text",
    "redact_value",
]
