"""Error categories, classification, and redaction (spec ERR-1 to ERR-3).

Every failure a caller sees falls in one of eight categories. Anything unexpected is
reported as ``internal`` with a reference id and no message text: the details go to the
server log, not to the model. Messages written by tool authors (the ``OunceError``
subclasses) are passed through ``redact`` and a length cap as defense in depth.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp.exceptions import AuthorizationError, NotFoundError, ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import ToolResult
from mcp_types import TextContent
from pydantic import ValidationError as PydanticValidationError

from ouncemcp.core.docs import error_doc
from ouncemcp.core.effects import Effect

logger = logging.getLogger("ouncemcp")

ErrorCategory = Literal[
    "validation",
    "authentication",
    "permission",
    "not_found",
    "rate_limit",
    "transient",
    "ambiguous_write",
    "internal",
]
CATEGORIES: tuple[ErrorCategory, ...] = (
    "validation",
    "authentication",
    "permission",
    "not_found",
    "rate_limit",
    "transient",
    "ambiguous_write",
    "internal",
)

# A later identical call may succeed without the caller changing anything.
RETRYABLE: frozenset[ErrorCategory] = frozenset({"rate_limit", "transient"})

DEFAULT_MAX_ERROR_CHARS = 300

_LABELS: dict[ErrorCategory, str] = {
    "validation": "Invalid input",
    "authentication": "Authentication required",
    "permission": "Not permitted",
    "not_found": "Not found",
    "rate_limit": "Rate limited",
    "transient": "Temporary failure",
    "ambiguous_write": "Write status unknown",
    "internal": "Internal error",
}


class OunceError(Exception):
    """Base for failures a tool author raises on purpose.

    ``message`` is shown to the caller after redaction, so write it for the agent: say
    what went wrong and how to fix it, and leave out hosts, credentials and stack
    traces. ``hint`` is appended. ``retry_after`` (seconds) is meaningful for rate limits.
    """

    category: ErrorCategory = "internal"
    code: str | None = None

    def __init__(
        self, message: str, *, hint: str | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retry_after = retry_after


class InvalidInput(OunceError):
    """The arguments are wrong. Retrying the same call cannot help."""

    category: ErrorCategory = "validation"


class AuthenticationRequired(OunceError):
    """The caller has no valid credentials. Reauthorize through the permitted flow."""

    category: ErrorCategory = "authentication"


class PermissionDenied(OunceError):
    """The caller is known and not allowed. Never suggest broader authority."""

    category: ErrorCategory = "permission"


class NotFound(OunceError):
    """The thing asked for does not exist, or the caller cannot see it."""

    category: ErrorCategory = "not_found"


class RateLimited(OunceError):
    """A limit was hit. Set ``retry_after`` (seconds) when the upstream says when."""

    category: ErrorCategory = "rate_limit"


class TransientError(OunceError):
    """A temporary failure. For reads, OunceMCP retries within the deadline."""

    category: ErrorCategory = "transient"


class AmbiguousWrite(OunceError):
    """A write may or may not have been applied. Callers must check state first."""

    category: ErrorCategory = "ambiguous_write"


class DeadlineExceeded(Exception):
    """Raised by OunceMCP when a call outlives its deadline. Not meant to be raised by tools."""

    def __init__(self, deadline_s: float) -> None:
        super().__init__(f"deadline of {deadline_s:g}s exceeded")
        self.deadline_s = deadline_s


class TooManyCalls(Exception):
    """The caller already has the maximum number of calls in flight."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"{limit} calls in flight")
        self.limit = limit


class RequestTooLarge(Exception):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"{size} bytes over {limit}")
        self.size = size
        self.limit = limit


class RequestTooDeep(Exception):
    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(f"depth {depth} over {limit}")
        self.depth = depth
        self.limit = limit


# ------------------------------------------------------------------ redaction (ERR-3)

MAX_REDACT_CHARS = 8_192
_REDACT_TAIL = 512

_TRACEBACK = re.compile(r"Traceback \(most recent call last\).*", re.DOTALL)
_TRACE_FILE = re.compile(r'File "[^"]*", line \d+(?:, in \S+)?')
_URL = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>)]+")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}")
_KEYED_SECRET = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_\-]?key|access[_\-]?key|"
    r"private[_\-]?key|authorization|credentials?|cookie|session[_\-]?id)\b"
    r"(\s*[:=]\s*|\s+is\s+)(\"[^\"]*\"|'[^']*'|\S+)"
)
_TOKEN_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"
)
_OPAQUE = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9+/_\-])")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
# The lookbehind lets a match start only at the beginning of a host-like run, so the
# pattern is tried once per run and stays linear on long hostile input.
_INTERNAL_HOST = re.compile(
    r"(?<![A-Za-z0-9\-.])(?:[A-Za-z0-9\-]+\.)*[A-Za-z0-9\-]+\."
    r"(?:internal|local|localdomain|lan|corp|intranet|svc|home|"
    r"com|net|org|io|dev|cloud|app|ai|co|in|uk|de|us|eu)(?::\d{2,5})?\b"
)
_LOCALHOST = re.compile(r"\blocalhost(?::\d{2,5})?\b", re.IGNORECASE)
_PATH = re.compile(r"(?:(?<=\s)|^|(?<=[(\[=:'\"]))(?:[A-Za-z]:\\|/)(?:[\w.\-]+[/\\])+[\w.\-]*")


def redact(text: str) -> str:
    """Remove stack traces, URLs, addresses, hostnames, file paths and credentials.

    Best effort, meant for messages a tool author wrote. Unexpected exceptions never
    reach this function: their text is not shown at all.

    Only the first ``MAX_REDACT_CHARS`` characters are scanned, because messages often
    echo an argument the caller chose and every result is clipped far below that. The
    cut is made at whitespace, so a secret is never left half inside the kept text.
    """
    if len(text) > MAX_REDACT_CHARS:
        kept = text[:MAX_REDACT_CHARS]
        space = max(kept.rfind(" "), kept.rfind("\n"), kept.rfind("\t"))
        text = kept[:space] if space >= MAX_REDACT_CHARS - _REDACT_TAIL else kept[:-_REDACT_TAIL]
    text = _TRACEBACK.sub("", text)
    text = _TRACE_FILE.sub("<trace>", text)
    text = _URL.sub("<url>", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} <redacted>", text)
    text = _KEYED_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _TOKEN_SHAPES.sub("<redacted>", text)
    text = _IPV4.sub("<host>", text)
    text = _IPV6.sub("<host>", text)
    text = _LOCALHOST.sub("<host>", text)
    text = _INTERNAL_HOST.sub("<host>", text)
    text = _PATH.sub("<path>", text)
    text = _OPAQUE.sub("<redacted>", text)
    return " ".join(text.split())


def clip(text: str, max_chars: int) -> str:
    """Cut ``text`` to ``max_chars`` characters, ending with an ellipsis."""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "\u2026"


# ------------------------------------------------------------------ validation (ERR-2)

_TYPE_PHRASES = {
    "int_parsing": "must be a whole number",
    "int_type": "must be a whole number",
    "int_from_float": "must be a whole number",
    "float_parsing": "must be a number",
    "float_type": "must be a number",
    "decimal_parsing": "must be a number",
    "bool_parsing": "must be true or false",
    "bool_type": "must be true or false",
    "string_type": "must be text",
    "list_type": "must be a list",
    "dict_type": "must be an object",
    "none_required": "must be null",
    "date_parsing": "must be a date such as 2026-01-31",
    "date_from_datetime_parsing": "must be a date such as 2026-01-31",
    "datetime_parsing": "must be an ISO 8601 date and time",
    "uuid_parsing": "must be a UUID",
    "url_parsing": "must be a URL",
    "json_invalid": "must be valid JSON",
}
_CTX_PHRASES = {
    "greater_than": "must be greater than {gt}",
    "greater_than_equal": "must be at least {ge}",
    "less_than": "must be less than {lt}",
    "less_than_equal": "must be at most {le}",
    "multiple_of": "must be a multiple of {multiple_of}",
    "string_too_short": "must have at least {min_length} characters",
    "string_too_long": "must have at most {max_length} characters",
    "too_short": "must have at least {min_length} items",
    "too_long": "must have at most {max_length} items",
    "string_pattern_mismatch": "must match the pattern {pattern}",
}
MAX_LISTED_ERRORS = 3


def _field(loc: Iterable[Any]) -> str:
    parts = [str(p) for p in loc if not (isinstance(p, str) and p.startswith("call["))]
    return ".".join(parts) or "input"


def _problem(error: Any) -> str:
    kind = error.get("type", "")
    field = _field(error.get("loc", ()))
    ctx = error.get("ctx") or {}
    if kind in {"missing", "missing_argument", "missing_keyword_only_argument"}:
        return f"`{field}` is required"
    if kind in {"extra_forbidden", "unexpected_keyword_argument"}:
        return f"`{field}` is not a parameter of this tool"
    if kind in {"literal_error", "enum"} and "expected" in ctx:
        options = str(ctx["expected"]).replace("'", "")
        return f"`{field}` must be one of: {options}"
    if kind in _CTX_PHRASES:
        try:
            return f"`{field}` " + _CTX_PHRASES[kind].format(**ctx)
        except (KeyError, IndexError):
            pass
    if kind in _TYPE_PHRASES:
        return f"`{field}` {_TYPE_PHRASES[kind]}"
    message = redact(str(error.get("msg", "is not valid")))
    message = re.sub(r"^(?i:input should be|value error,?)\s*", "must be ", message)
    return f"`{field}` {message}"


def format_validation(
    exc: PydanticValidationError, max_chars: int = DEFAULT_MAX_ERROR_CHARS
) -> str:
    """A short message that says how to fix the input. Never repeats the values sent."""
    errors = exc.errors(include_url=False, include_context=True, include_input=False)
    shown = [_problem(e) for e in errors[:MAX_LISTED_ERRORS]]
    extra = len(errors) - len(shown)
    tail = f"; and {extra} more" if extra > 0 else ""
    return clip(f"Fix the arguments and call again: {'; '.join(shown)}{tail}.", max_chars)


# ------------------------------------------------------------------ classification (ERR-1)

_PRE_SEND = frozenset({"ConnectError", "ConnectTimeout", "ConnectionRefusedError", "SSLError"})
_TIMEOUT_NAMES = frozenset({"TimeoutException", "ReadTimeout", "WriteTimeout", "PoolTimeout"})
_TRANSPORT_NAMES = frozenset(
    {"TransportError", "NetworkError", "ReadError", "WriteError", "RemoteProtocolError"}
)
_WRITES = (Effect.WRITE, Effect.DESTRUCTIVE)


def _mro_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _status_of(exc: BaseException) -> tuple[int | None, float | None]:
    """HTTP status and Retry-After seconds from an exception that carries a response.

    HTTP client exceptions are recognized by shape, so no client library is required.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return None, None
    retry_after: float | None = None
    headers: Any = getattr(response, "headers", None)
    raw = None
    if hasattr(headers, "get"):
        raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is not None:
        try:
            retry_after = max(0.0, float(raw))
        except (TypeError, ValueError):
            retry_after = None
    return status, retry_after


@dataclass(frozen=True)
class Failure:
    """A classified failure, ready to send to the caller."""

    category: ErrorCategory
    code: str
    message: str
    hint: str | None = None
    retry_after: float | None = None
    ref: str | None = None

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE

    def text(self) -> str:
        parts = [f"[ouncemcp] {_LABELS[self.category]}: {self.message}"]
        if self.hint:
            parts.append(self.hint)
        return " ".join(parts)

    def meta(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "error": self.code,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.retry_after is not None:
            data["retry_after_s"] = round(self.retry_after, 3)
        if self.ref is not None:
            data["ref"] = self.ref
        data["docs"] = error_doc(self.category)
        return data

    def to_result(self) -> ToolResult:
        return ToolResult(
            content=[TextContent(type="text", text=self.text())],
            meta={"ouncemcp": self.meta()},
            is_error=True,
        )


def _write_unknown(deadline_note: str) -> str:
    return (
        f"{deadline_note} The write may or may not have been applied. Check the current "
        "state before trying again, and do not repeat it blindly."
    )


def _timeout_failure(effect: Effect | None, what: str) -> Failure:
    if effect in _WRITES:
        return Failure("ambiguous_write", "write_status_unknown", _write_unknown(what))
    return Failure("transient", "deadline_exceeded", f"{what} Try again, or narrow the request.")


_FASTMCP_WRAPPER_PREFIX = "Error calling tool"


def classify(
    exc: BaseException,
    *,
    effect: Effect | None = None,
    tool: str | None = None,
    max_chars: int = DEFAULT_MAX_ERROR_CHARS,
    quiet: bool = False,
) -> Failure:
    """Map an exception to a ``Failure``. Unknown exceptions become ``internal`` with a
    reference id; their text is logged (unless ``quiet``) and never returned."""

    def own(message: str) -> str:
        return clip(redact(message), max_chars)

    original = _wrapped_by_fastmcp(exc)
    if original is not None:
        # FastMCP wraps whatever a tool raises in ``ToolError("Error calling tool ...")``.
        # Classify the original so a tool FastMCP runs directly gets the same categories
        # and the same no-leak rule as one wrapped by ``OunceServer``.
        return classify(original, effect=effect, tool=tool, max_chars=max_chars, quiet=quiet)
    if isinstance(exc, OunceError):
        return Failure(
            exc.category,
            exc.code or exc.category,
            own(exc.message),
            hint=own(exc.hint) if exc.hint else None,
            retry_after=exc.retry_after,
        )
    if isinstance(exc, DeadlineExceeded):
        return _timeout_failure(
            effect,
            f"The call did not finish within its {exc.deadline_s:g}s deadline and was cancelled.",
        )
    if isinstance(exc, RequestTooLarge):
        return Failure(
            "validation",
            "request_too_large",
            f"The arguments are {exc.size:,} bytes and the limit is {exc.limit:,}. Send less data.",
        )
    if isinstance(exc, RequestTooDeep):
        return Failure(
            "validation",
            "request_too_deep",
            f"The arguments are nested {exc.depth} levels deep and the limit is {exc.limit}. "
            "Flatten the input.",
        )
    if isinstance(exc, TooManyCalls):
        return Failure(
            "rate_limit",
            "too_many_concurrent_calls",
            f"This caller already has {exc.limit} calls in progress. Wait for one to finish, "
            "then retry.",
            retry_after=1.0,
        )
    if isinstance(exc, FastMCPValidationError):
        cause = exc.__cause__
        if isinstance(cause, PydanticValidationError):
            return Failure("validation", "invalid_input", format_validation(cause, max_chars))
        return Failure("validation", "invalid_input", "The arguments are not valid for this tool.")
    if isinstance(exc, NotFoundError):
        return Failure(
            "not_found",
            "unknown_tool",
            "No such tool. List the tools again to see what is available.",
        )
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return Failure("permission", "permission_denied", "You are not allowed to do this.")
    status, retry_after = _status_of(exc)
    if status is not None:
        return _from_status(status, retry_after, effect, exc, tool, quiet)
    names = _mro_names(exc)
    if names & _PRE_SEND or isinstance(exc, ConnectionRefusedError):
        return Failure(
            "transient",
            "upstream_unreachable",
            "The upstream service could not be reached. The request was not sent. Try again later.",
        )
    if (
        isinstance(exc, (TimeoutError, ConnectionError))
        or names & _TIMEOUT_NAMES
        or names & _TRANSPORT_NAMES
    ):
        return _timeout_failure(effect, "The upstream service did not answer in time.")
    if isinstance(exc, ToolError):
        return _internal(exc, tool, quiet, message=own(str(exc)))
    return _internal(exc, tool, quiet)


def _wrapped_by_fastmcp(exc: BaseException) -> BaseException | None:
    if isinstance(exc, ToolError) and str(exc).startswith(_FASTMCP_WRAPPER_PREFIX):
        return exc.__cause__
    return None


def _from_status(
    status: int,
    retry_after: float | None,
    effect: Effect | None,
    exc: BaseException,
    tool: str | None,
    quiet: bool,
) -> Failure:
    if status == 401:
        return Failure(
            "authentication",
            "upstream_unauthenticated",
            "The upstream service rejected the credentials. Reauthorize and try again.",
        )
    if status == 403:
        return Failure(
            "permission", "upstream_forbidden", "The upstream service denied this request."
        )
    if status in {404, 410}:
        return Failure("not_found", "upstream_not_found", "The upstream service has no such item.")
    if status == 429:
        return Failure(
            "rate_limit",
            "upstream_rate_limited",
            "The upstream service is rate limiting this caller.",
            retry_after=retry_after,
        )
    if status == 408 or status in {502, 503, 504}:
        return _timeout_failure(effect, f"The upstream service failed temporarily ({status}).")
    if status in {400, 422}:
        return Failure(
            "validation", "upstream_rejected_input", "The upstream service rejected the arguments."
        )
    return _internal(exc, tool, quiet)


def _internal(
    exc: BaseException | None, tool: str | None, quiet: bool, *, message: str | None = None
) -> Failure:
    ref = uuid.uuid4().hex[:8]
    if exc is not None and not quiet:
        logger.error(
            "ouncemcp: unexpected failure in %s (ref %s): %r",
            tool or "tool",
            ref,
            exc,
            exc_info=exc,
        )
    shown = message or "The tool failed unexpectedly."
    return Failure(
        "internal",
        "internal_error",
        f"{shown} Tell the server's maintainer the reference {ref}.",
        ref=ref,
    )
