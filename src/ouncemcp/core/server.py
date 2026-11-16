"""``OunceServer``: a thin layer over FastMCP that makes lean tools the default.

M0 scope (spec section 15): typed tools with declared effects, definition token
counting, and result budgets with concise and detailed formats and truncation.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import sys
import typing
import warnings
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, TypeVar, cast

import anyio
import jsonschema
from fastmcp import FastMCP
from fastmcp.server.auth import AuthCheck
from fastmcp.server.context import Context as FastMCPContext
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import FunctionTool, Tool, ToolResult
from fastmcp.tools.base import InputRequiredToolResult
from mcp_types import InputRequiredResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from ouncemcp.core.budget import Budget, BudgetOverride, BudgetOverrideWarning
from ouncemcp.core.costs import ServerCost, definition_payload, measure_tools
from ouncemcp.core.cursor import BoundCursors, CursorSigner, InvalidCursor
from ouncemcp.core.docs import error_doc
from ouncemcp.core.effects import PARALLEL_SAFE_META, Effect
from ouncemcp.core.errors import Failure, classify, format_validation
from ouncemcp.core.pagination import PageFields, PageWindow, find_page_fields, window
from ouncemcp.core.schema import compact_schema
from ouncemcp.core.shaping import (
    PageSpec,
    ResponseFormat,
    ResultTooLarge,
    is_detail_field,
    model_has_detail_fields,
    nested_models,
    shape_result,
    validate_detail_fields,
)
from ouncemcp.core.tokens import DEFAULT_ENCODING, TokenCounter, get_counter
from ouncemcp.core.wire import wire_tools
from ouncemcp.discovery import (
    DISCOVERY_SETTINGS,
    META_TOOLS,
    DiscoveryDecision,
    DiscoveryMode,
    DiscoverySetting,
    OunceSearchTransform,
    RecallCase,
    RecallReport,
    build_report,
    decide_discovery,
)
from ouncemcp.execution import (
    RESOLVE_TIMEOUT_S,
    ConcurrencyGate,
    Limits,
    RetryPolicy,
    WriteAttempt,
    WriteResolver,
)
from ouncemcp.execution.approval import (
    APPROVAL_KEY,
    ApprovalDeclined,
    ApprovalRequest,
    ApprovalRule,
    ApprovalUnavailable,
    Approver,
    Preview,
    accepted,
    check_rule,
    confirmation,
    elicited_yes,
    needed,
    summarize,
)
from ouncemcp.execution.handles import (
    DEFAULT_HANDLE_TTL_S,
    DEFAULT_JOB_TTL_S,
    HANDLE_KIND,
    JOB_KIND,
    HandlePage,
    JobKind,
    JobResult,
    JobRunner,
    JobStatus,
    binding,
    check_job_args,
    get_items,
    put_items,
)
from ouncemcp.execution.idempotency import (
    Idempotency,
    IdempotencyGuard,
    as_idempotency,
    client_key,
    make_claim,
)
from ouncemcp.execution.middleware import CALL_NOTES, OunceMiddleware
from ouncemcp.execution.runner import run_with_policy
from ouncemcp.execution.store import MemoryStore, OunceStore
from ouncemcp.security.cache import CacheView, PartitionedCache
from ouncemcp.security.http import resolve_http_options
from ouncemcp.security.identity import (
    ANONYMOUS_IDENTITY,
    DEFAULT_TENANT_CLAIMS,
    Identity,
    identity_from_token,
)
from ouncemcp.security.policy import Policy, ToolFacts, policy_check
from ouncemcp.security.replay import InputRequired, UnsafeInputRequest
from ouncemcp.security.state import StateSigner
from ouncemcp.security.stdio import StdioPolicy, StdioPolicyFilter
from ouncemcp.trace import Tracer, TraceSink
from ouncemcp.trace.metrics import MetricsSink

# MCP tool names: 1-128 characters of ASCII letters, digits, underscore, hyphen, dot.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")

# Parameters the model must never control (spec CAP-6), plus names OunceMCP injects.
RESERVED_PARAMS = frozenset(
    {
        "tenant",
        "tenant_id",
        "principal",
        "user_id",
        "credential",
        "credentials",
        "api_key",
        "apikey",
        "access_token",
        "token",
        "password",
        "secret",
        "approval",
        "approval_id",
        "approved",
    }
)
INJECTED_PARAMS = frozenset({"response_format", "ctx"})


T = TypeVar("T")


ANONYMOUS = "anonymous"
# Arguments that move through a result set rather than describe it. They are left out of
# the query a cursor is bound to, so an agent can change the page size between calls.
PAGING_ARGS = frozenset({"cursor", "limit", "response_format"})

PrincipalResolver = Callable[["ToolContext"], str | None]


def _current_identity(tenant_claims: Sequence[str] = DEFAULT_TENANT_CLAIMS) -> Identity:
    """The verified caller of the current request (SEC-1). Never reads ``clientInfo``."""
    token = get_access_token()
    if token is None:
        return ANONYMOUS_IDENTITY
    return identity_from_token(token, tenant_claims=tenant_claims)


def default_principal(ctx: ToolContext) -> str | None:
    """The verified caller: ``issuer|subject`` from the access token (or its subject, or
    client id) when the server has authentication configured, otherwise ``None`` (a
    single local caller). Self-reported client details are never used (SEC-1)."""
    identity = _current_identity()
    return identity.principal if identity.authenticated else None


@functools.cache
def _bare_context_signer() -> CursorSigner:
    """For a ``ToolContext`` built by hand, such as in a unit test of a handler."""
    return CursorSigner.ephemeral()


@dataclass
class ToolContext:
    """Per-call information passed to handlers that declare a ``ToolContext`` parameter.

    Attributes:
        tool: The tool name.
        response_format: ``"concise"`` or ``"detailed"``. Handlers can skip expensive
            lookups for fields that will not be returned; see ``includes`` (RES-11).
        result_budget: Token budget for this call's result: the tool's concise budget,
            or ``Budget.result_ceiling`` for detailed results. Handlers can use it to
            choose how much to fetch (RES-11).
        fastmcp: The underlying FastMCP context, when running inside a server.
    """

    tool: str
    response_format: ResponseFormat
    result_budget: int
    fastmcp: FastMCPContext | None = None
    _signer: CursorSigner | None = field(default=None, repr=False)
    _arguments: Callable[[], Mapping[str, Any]] = field(default=dict, repr=False)
    _resolve: PrincipalResolver = field(default=default_principal, repr=False)
    _principal: str | None = field(default=None, repr=False)
    attempt: int = 1
    _deadline_at: float | None = field(default=None, repr=False)
    _window: PageWindow[Any] | None = field(default=None, repr=False)
    _encode: Callable[[int], str] | None = field(default=None, repr=False)
    _bound: dict[frozenset[str], BoundCursors] = field(default_factory=dict, repr=False)
    effect: Effect | None = None
    replay_safe: bool = False
    _identity: Identity | None = field(default=None, repr=False)
    _tenant_claims: tuple[str, ...] = field(default=DEFAULT_TENANT_CLAIMS, repr=False)
    _cache: PartitionedCache | None = field(default=None, repr=False)
    _state: StateSigner | None = field(default=None, repr=False)
    _deps: Any = field(default=None, repr=False)
    _store: OunceStore | None = field(default=None, repr=False)
    _effects_applied: bool = field(default=False, repr=False)
    _approved: bool = field(default=False, repr=False)
    _jobs: JobRunner | None = field(default=None, repr=False)
    _output_model: type[BaseModel] | None = field(default=None, repr=False)

    @property
    def detailed(self) -> bool:
        """True when the caller asked for ``response_format="detailed"``."""
        return self.response_format == "detailed"

    def includes(self, path: str) -> bool:
        """Whether output field ``path`` is returned in this call (RES-11).

        ``path`` names a field of the tool's output model, dotted for nested models
        (``"owner.history"``). Fields marked ``Detail`` are only returned in detailed
        results, so a handler can skip the lookup that fills them::

            if ctx.includes("metrics"):
                detail.metrics = await fetch_metrics(id)

        Unknown fields raise ``ValueError``, so a renamed field cannot silently turn a
        lookup off.
        """
        model = self._output_model
        if model is None:
            raise RuntimeError("includes() needs a tool context created by OunceServer")
        for part in path.split("."):
            if model is None:
                raise ValueError(f"'{path}': cannot look inside a field that is not a model")
            info = model.model_fields.get(part)
            if info is None:
                raise ValueError(f"'{path}': {model.__name__} has no field '{part}'")
            if is_detail_field(info) and not self.detailed:
                return False
            nested = nested_models(info.annotation)
            model = nested[0] if nested else None
        return True

    @property
    def identity(self) -> Identity:
        """The verified caller (SEC-1): principal, tenant, scopes and claims from the
        access token. Anonymous when the request has no verified token."""
        if self._identity is None:
            self._identity = _current_identity(self._tenant_claims)
        return self._identity

    @property
    def cache(self) -> CacheView:
        """The server's private cache, partitioned by this caller's principal, tenant and
        scopes and namespaced by tool, so entries never cross callers (SEC-5)."""
        if self._cache is None:
            self._cache = PartitionedCache()
        identity = replace(self.identity, principal=self.principal)
        return CacheView(self._cache, identity, self.tool)

    @property
    def deps(self) -> Any:
        """The server's dependencies (``OunceServer(deps=...)``): upstream clients, pools
        and anything else handlers need. Replace them with fakes in tests."""
        if self._deps is None:
            raise RuntimeError("This server has no deps; pass OunceServer(deps=...)")
        return self._deps

    @property
    def store(self) -> OunceStore:
        """The server's shared store (``OunceServer(store=...)``)."""
        if self._store is None:
            self._store = MemoryStore()
        return self._store

    async def store_result(self, items: Sequence[Any], *, ttl_s: int = DEFAULT_HANDLE_TTL_S) -> str:
        """Store a large result and return a handle the caller pages with
        ``handles.read`` (RES-5). Needs ``OunceServer(handles=True)``. The handle is
        bound to this caller and tenant and expires after ``ttl_s``."""
        if self._jobs is None:
            raise RuntimeError("Stored results need OunceServer(handles=True)")
        ref = await put_items(self.store, items, ttl_s=ttl_s)
        return self.issue_state(
            HANDLE_KIND, {"id": ref}, request=binding(self.identity.tenant), ttl_s=ttl_s
        )

    async def start_job(
        self,
        work: Callable[[], Awaitable[Any]] | str,
        args: Mapping[str, Any] | None = None,
        *,
        ttl_s: int = DEFAULT_JOB_TTL_S,
    ) -> str:
        """Run a job in the background and return a job id for ``jobs.get``.

        ``work`` is either a function to run, or the name of a job registered with
        ``@server.job`` plus its JSON ``args``. A named job is started again by the next
        ``jobs.get`` if its process stops; a function cannot be, and is reported
        ``interrupted``. The job returns a model or dict (the summary) or a
        ``JobResult`` with a summary and many items, which are stored behind a handle.
        Needs ``OunceServer(handles=True)``. The id is bound to this caller and tenant.
        """
        if self._jobs is None:
            raise RuntimeError("Jobs need OunceServer(handles=True)")
        if isinstance(work, str):
            kind = self._jobs.kinds.get(work)
            if kind is None:
                raise ValueError(f"No job named '{work}'; register it with @server.job")
            stored = check_job_args(args or {})
            ref = await self._jobs.start(
                lambda: kind.run(stored, 1),
                ttl_s=ttl_s,
                spill=self._job_spill(ttl_s),
                kind=kind.name,
                args=stored,
            )
        else:
            if args is not None:
                raise TypeError("args are for named jobs; a function job takes no args")
            ref = await self._jobs.start(work, ttl_s=ttl_s, spill=self._job_spill(ttl_s))
        return self.issue_state(
            JOB_KIND, {"id": ref}, request=binding(self.identity.tenant), ttl_s=ttl_s
        )

    def _job_spill(self, ttl_s: int) -> Callable[[Any], Awaitable[dict[str, Any]]]:
        """Stores a finished job's result for this caller: the summary inline, many
        items behind a handle bound to this caller and tenant."""
        principal, tenant = self.principal, self.identity.tenant

        async def spill(result: Any) -> dict[str, Any]:
            outcome = result if isinstance(result, JobResult) else JobResult(summary=result)
            summary = outcome.summary
            data: dict[str, Any] = {}
            if isinstance(summary, BaseModel):
                data["result"] = summary.model_dump(mode="json")
            elif summary is not None:
                data["result"] = dict(summary)
            if outcome.items is not None:
                ref = await put_items(self.store, outcome.items, ttl_s=ttl_s)
                data["handle"] = self._state_signer().issue(
                    HANDLE_KIND,
                    principal=principal,
                    request=binding(tenant),
                    data={"id": ref},
                    ttl_s=ttl_s,
                )
                data["items"] = len(outcome.items)
            return data

        return spill

    async def consume_state(self, token: str, kind: str, *, request: Any = None) -> dict[str, Any]:
        """Like ``verify_state(..., consume=True)``, but single use is recorded in the
        shared store, so it holds across replicas when the store is shared (SEC-6)."""
        data = self.verify_state(token, kind, request=request)
        from ouncemcp.security.state import InvalidState

        nonce = token.rsplit(".", 1)[-1]
        fresh = await self.store.put_if_absent(
            "state-used", nonce, {"kind": kind}, ttl_s=self._state_signer().ttl_s * 2
        )
        if not fresh:
            raise InvalidState("replayed")
        return data

    def _state_signer(self) -> StateSigner:
        if self._state is None:
            self._state = StateSigner(self._signer or _bare_context_signer())
        return self._state

    def issue_state(
        self,
        kind: str,
        data: Mapping[str, Any] | None = None,
        *,
        request: Any = None,
        ttl_s: int | None = None,
    ) -> str:
        """Issue a signed token for a handle, approval or continuation (SEC-6).

        It is bound to this caller, to ``request`` (any JSON value that identifies the
        originating request; default: this tool), and expires after ``ttl_s``.
        """
        return self._state_signer().issue(
            kind,
            principal=self.principal,
            request=self.tool if request is None else request,
            data=data,
            ttl_s=ttl_s,
        )

    def verify_state(
        self, token: str, kind: str, *, request: Any = None, consume: bool = False
    ) -> dict[str, Any]:
        """Return the data of a token from ``issue_state``, or raise ``InvalidState``,
        which the caller sees as an actionable validation error. ``consume=True`` makes
        the token single-use, which blocks replay of an approval."""
        return self._state_signer().verify(
            token,
            kind=kind,
            principal=self.principal,
            request=self.tool if request is None else request,
            consume=consume,
        )

    async def gather(
        self, steps: Mapping[str, Callable[[], Awaitable[T]]], *, concurrency: int = 4
    ) -> dict[str, T]:
        """Run the steps of a composite tool concurrently and return their results by name.

        Each step is a function returning an awaitable, such as one upstream call. At most
        ``concurrency`` run at once, all inside this call's deadline (EXE-1). If a step
        fails, the others are cancelled and the error is classified like any handler
        error, so one composite call costs the agent one round trip whichever way it
        ends (spec CAP-7).
        """
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        results: dict[str, T] = {}
        limiter = anyio.Semaphore(concurrency)

        async def run(name: str, step: Callable[[], Awaitable[T]]) -> None:
            async with limiter:
                results[name] = await step()

        try:
            async with anyio.create_task_group() as group:
                for name, step in steps.items():
                    group.start_soon(run, name, step)
        except BaseExceptionGroup as group_exc:
            first = group_exc.exceptions[0]
            while isinstance(first, BaseExceptionGroup):
                first = first.exceptions[0]
            raise first from None
        return {name: results[name] for name in steps}

    def effect_applied(self) -> None:
        """Record that this call changed something outside the server.

        Call it right after an irreversible upstream effect. Afterwards a write tool that
        is not ``replay_safe`` may not ask for input (SEC-7), because the client answers
        by sending the whole request again, which would repeat the effect.
        """
        self._effects_applied = True

    @property
    def input_responses(self) -> Any:
        """The client's answers to a previous ``require_input`` (MCP 2026-07-28)."""
        return getattr(self.fastmcp, "input_responses", None) if self.fastmcp else None

    def require_input(
        self, input_requests: Any, *, state: Mapping[str, Any] | None = None
    ) -> typing.NoReturn:
        """Stop and ask the client for input (MCP multi-round-trip, SEP-2322).

        Raises an exception the server turns into an ``input_required`` result; the client
        answers by repeating this call with ``input_responses``. ``state`` is signed and
        bound to this caller and these arguments (SEC-6); read it back with
        ``resume_state()``. Refused after ``effect_applied()`` unless the tool is
        ``replay_safe`` (SEC-7).
        """
        writes = (Effect.WRITE, Effect.DESTRUCTIVE)
        if self._effects_applied and self.effect in writes and not self.replay_safe:
            raise UnsafeInputRequest(self.tool)
        token = None
        if self._approved:
            state = {**(state or {}), "ouncemcp_approved": True}
        if state is not None:
            token = self.issue_state("input_required", state, request=self._request_key())
        raise InputRequired(
            InputRequiredResult(input_requests=input_requests or None, request_state=token)
        )

    def resume_state(self) -> dict[str, Any] | None:
        """The ``state`` passed to the last ``require_input``, verified, or ``None``."""
        raw = getattr(self.fastmcp, "request_state", None) if self.fastmcp else None
        if not raw:
            return None
        return self.verify_state(raw, "input_required", request=self._request_key())

    def _request_key(self) -> Any:
        args = {k: v for k, v in self._arguments().items() if k not in PAGING_ARGS}
        return [self.tool, args]

    @property
    def principal(self) -> str:
        """The verified caller that pagination cursors are bound to (resolved once)."""
        if self._principal is None:
            self._principal = str(self._resolve(self) or ANONYMOUS)
        return self._principal

    def remaining(self) -> float:
        """Seconds left before this call's deadline, or ``inf`` when it has none.

        Pass it to upstream timeouts so a slow dependency gives up when the call would.
        """
        if self._deadline_at is None:
            return float("inf")
        return max(0.0, self._deadline_at - anyio.current_time())

    def _cursors(self, ignore: Sequence[str] = ()) -> BoundCursors:
        skip = PAGING_ARGS | frozenset(ignore)
        bound = self._bound.get(skip)
        if bound is None:
            signer = self._signer or _bare_context_signer()
            query = {k: v for k, v in self._arguments().items() if k not in skip}
            bound = signer.bind(tool=self.tool, principal=self.principal, query=query)
            self._bound[skip] = bound
        return bound

    def encode_cursor(self, offset: int, *, ignore: Sequence[str] = ()) -> str:
        """Issue a signed cursor that resumes at ``offset`` for this call's tool, caller
        and arguments."""
        return self._cursors(ignore).encode(offset)

    def offset(self, cursor: str | None, *, ignore: Sequence[str] = ()) -> int:
        """Verify a cursor issued by this tool and return its start offset (``None`` is 0).

        Cursors are bound to the tool, the caller and every argument except ``cursor``,
        ``limit``, ``response_format`` and the names in ``ignore``. Raises
        ``InvalidCursor``, which the server returns as an actionable tool error.
        """
        return self._cursors(ignore).decode(cursor)

    def page(
        self,
        rows: Sequence[T],
        *,
        cursor: str | None,
        limit: int,
        ignore: Sequence[str] = (),
    ) -> PageWindow[T]:
        """Slice one page from ``rows``. If OunceMCP trims the page to fit the result
        budget, it moves ``next_cursor`` to resume after the last item returned.

        Name any paging arguments that are not called ``cursor`` or ``limit`` in
        ``ignore``, so changing them does not invalidate the cursor.
        """
        cursors = self._cursors(ignore)
        self._window = window(
            rows, start=cursors.decode(cursor), limit=limit, encode=cursors.encode
        )
        self._encode = cursors.encode
        return self._window

    def window(
        self,
        items: Sequence[T],
        *,
        start: int,
        has_more: bool,
        total: int | None = None,
        ignore: Sequence[str] = (),
    ) -> PageWindow[T]:
        """Register a page fetched elsewhere (for example ``OFFSET start LIMIT n``)."""
        items = list(items)
        end = start + len(items)
        self._window = PageWindow(
            items=items,
            start=start,
            total=total,
            next_cursor=self.encode_cursor(end, ignore=ignore) if has_more else None,
        )
        self._encode = self._cursors(ignore).encode
        return self._window


@dataclass(frozen=True)
class ToolSpec:
    name: str
    effect: Effect
    output_model: type[BaseModel]
    input_model: type[BaseModel] | None
    has_detail: bool
    result_tokens: int | None
    min_items: int = 1
    page_fields: PageFields | None = None
    output_schema: OutputSchemaMode = "on"
    output_schema_advertised: bool = True
    tags: frozenset[str] = frozenset()
    has_policy: bool = False
    replay_safe: bool = False
    handler: Callable[..., Any] | None = field(default=None, compare=False, repr=False)
    idempotency: Idempotency | None = None
    approval: ApprovalRule | None = None
    preview: Preview | None = field(default=None, compare=False, repr=False)
    budget_override: BudgetOverride | None = None
    parallel_safe: bool = False


OutputSchemaMode = Literal["on", "off", "auto"]
OUTPUT_SCHEMA_MODES: tuple[OutputSchemaMode, ...] = ("on", "off", "auto")


def _server_mode(value: object) -> OutputSchemaMode:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, str) and value in OUTPUT_SCHEMA_MODES:
        return cast(OutputSchemaMode, value)
    raise ValueError(
        f"output_schemas must be 'on', 'off' or 'auto' (or True or False), got {value!r}"
    )


def _tool_mode(value: object, tool_name: str) -> OutputSchemaMode | None:
    if value is None:
        return None
    if isinstance(value, str) and value in OUTPUT_SCHEMA_MODES:
        return cast(OutputSchemaMode, value)
    raise ValueError(
        f"Tool '{tool_name}': output_schema must be 'on', 'off' or 'auto', got {value!r}"
    )


logger = logging.getLogger("ouncemcp")


def _too_large_result(
    tool_name: str, exc: ResultTooLarge, response_format: ResponseFormat, tokenizer: str
) -> ToolResult:
    """An actionable error for a result that cannot fit its budget (RES-8).

    The advice depends on why it doesn't fit. A lower ``limit`` is only suggested
    when it could help, which is when the list still holds more than one item.
    """
    concise = " or use response_format=concise" if response_format == "detailed" else ""
    size = f"about {exc.min_tokens} tokens, over the {exc.budget}-token budget"
    if exc.items is None:
        advice = f"The smallest valid result is {size}. Narrow the request{concise}."
    elif exc.items <= 1:
        advice = (
            f"Even one item is {size}, so asking for fewer items will not help. "
            f"Narrow the filters or ask for a different item{concise}."
        )
    else:
        advice = (
            f"Even {exc.items} items, the fewest this tool trims to, are {size}. "
            f"Ask for fewer than {exc.items} items with a lower limit, or narrow the "
            f"filters{concise}."
        )
    hint = (
        f"Raise result_tokens for '{tool_name}' (now {exc.budget}), lower its min_items, "
        "or return smaller items."
    )
    logger.warning("ouncemcp: %s result too large (%s). %s", tool_name, exc.reason, hint)
    meta: dict[str, Any] = {
        "error": "result_too_large",
        "reason": exc.reason,
        "tokenizer": tokenizer,
        "min_tokens": exc.min_tokens,
        "budget": exc.budget,
        "format": response_format,
        "hint": hint,
    }
    if exc.items is not None:
        meta["items"] = exc.items
    return _error_result(f"[ouncemcp] Result too large for '{tool_name}'. {advice}", meta)


def _error_result(message: str, meta: dict[str, Any]) -> ToolResult:
    """A tool execution error (``isError: true``) the model can act on."""
    meta.setdefault("category", "validation")
    meta.setdefault("docs", error_doc(meta["category"]))
    return ToolResult(
        content=[TextContent(type="text", text=message)],
        meta={"ouncemcp": meta},
        is_error=True,
    )


def _client_can_elicit(fastmcp_ctx: FastMCPContext | None) -> bool:
    """Whether the calling client declared elicitation. Approvals are only asked of
    clients that can show them to a person (release gate 4)."""
    if fastmcp_ctx is None:
        return False
    try:
        caps = fastmcp_ctx.session.client_capabilities
    except Exception:  # noqa: BLE001 - no session: in-process call
        return False
    return getattr(caps, "elicitation", None) is not None


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync code, also when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _as_policies(policy: Policy | Sequence[Policy] | None) -> tuple[Policy, ...]:
    if policy is None:
        return ()
    if callable(policy):
        return (policy,)
    policies = tuple(policy)
    if not all(callable(p) for p in policies):
        raise TypeError("policy must be a callable or a list of callables")
    return policies


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


class OunceServer:
    """An MCP server whose tools are typed, effect-declared, and context-budgeted.

    Example::

        server = OunceServer("inventory", budget=Budget(result_tokens=1_500))

        @server.tool(name="inventory.search", effect=Effect.READ)
        async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
            \"\"\"Find resources by region and type.\"\"\"
            ...

        server.run()

    Token counts use ``tokenizer``: a tiktoken encoding name (default ``o200k_base``),
    a ``TokenCounter``, or a function from text to token count, which also needs
    ``tokenizer_name`` so reports can name it. Budgets, shaping and ``check`` all use it.
    """

    def __init__(
        self,
        name: str,
        *,
        instructions: str | None = None,
        version: str | None = None,
        budget: Budget | None = None,
        tokenizer: str | TokenCounter | Callable[[str], int] = DEFAULT_ENCODING,
        tokenizer_name: str | None = None,
        output_schemas: bool | OutputSchemaMode = "on",
        cursors: CursorSigner | None = None,
        principal: PrincipalResolver | None = None,
        discovery: DiscoverySetting = "auto",
        pinned: Sequence[str] = (),
        search_results: int = 5,
        limits: Limits | None = None,
        retry: RetryPolicy | None = None,
        write_resolver: WriteResolver | None = None,
        policy: Policy | Sequence[Policy] | None = None,
        audience: str | Sequence[str] | None = None,
        tenant_claims: Sequence[str] = DEFAULT_TENANT_CLAIMS,
        cache: PartitionedCache | None = None,
        trace: Tracer | TraceSink | Sequence[TraceSink] | None = None,
        stdio_policy: StdioPolicy = "trust",
        store: OunceStore | None = None,
        deps: Any = None,
        approver: Approver | None = None,
        handles: bool = False,
        metrics: bool = False,
        readiness: Sequence[Callable[[], Any]] = (),
        http_host: str = "127.0.0.1",
        **fastmcp_kwargs: Any,
    ) -> None:
        self.name = name
        self.budget = budget or Budget()
        self.counter = get_counter(tokenizer, name=tokenizer_name)
        wrapped = fastmcp_kwargs.pop("_wrap", None)
        self.fastmcp = (
            wrapped
            if wrapped is not None
            else FastMCP(name, instructions=instructions, version=version, **fastmcp_kwargs)
        )
        self._wrapped_effects: dict[str, Effect] = {}
        self.output_schemas: OutputSchemaMode = _server_mode(output_schemas)
        self.cursors = cursors or CursorSigner.from_env()
        self._resolve_principal = principal or default_principal
        self._specs: dict[str, ToolSpec] = {}
        self._registered: dict[str, Tool] = {}
        if discovery not in DISCOVERY_SETTINGS:
            raise ValueError(
                f"discovery must be one of {', '.join(DISCOVERY_SETTINGS)}, got {discovery!r}"
            )
        if not isinstance(search_results, int) or search_results < 1:
            raise ValueError("search_results must be an integer >= 1")
        self.discovery_setting: DiscoverySetting = discovery
        self._decision: DiscoveryDecision | None = None
        self._stdio_filter = StdioPolicyFilter(stdio_policy)
        self.stdio_policy: StdioPolicy = stdio_policy
        self.fastmcp.add_transform(self._stdio_filter)
        self._search = OunceSearchTransform(
            mode=self._discovery_mode,
            pinned=pinned,
            max_results=search_results,
        )
        self.fastmcp.add_transform(self._search)
        self.limits = limits or Limits()
        self.retry = retry or RetryPolicy()
        self.write_resolver = write_resolver
        self.policies: tuple[Policy, ...] = _as_policies(policy)
        self.audiences: tuple[str, ...] = (
            (audience,) if isinstance(audience, str) else tuple(audience or ())
        )
        if any(not a for a in self.audiences):
            raise ValueError("audience must be a non-empty string (the server's resource URL)")
        self.tenant_claims = tuple(tenant_claims)
        self.cache = cache if cache is not None else PartitionedCache()
        self.state = StateSigner(self.cursors)
        self.tracer = Tracer.coerce(trace)
        if store is not None and not isinstance(store, OunceStore):
            raise TypeError("store must implement OunceStore (get, put, put_if_absent, delete)")
        self.store: OunceStore = store if store is not None else MemoryStore()
        self.idempotency = IdempotencyGuard(self.store)
        self.deps = deps
        if approver is not None and not callable(approver):
            raise TypeError("approver must be a function of ApprovalRequest")
        self.approver = approver
        self.jobs = JobRunner(self.store)
        self.handles = bool(handles)
        if handles:
            self._register_handle_tools()
        self.http_host = http_host
        self.readiness = tuple(readiness)
        self.metrics: MetricsSink | None = None
        if metrics:
            self.metrics = MetricsSink()
            self.add_trace_sink(self.metrics)
        self._register_routes()
        self._middleware = OunceMiddleware(
            limits=self.limits,
            gate=ConcurrencyGate(self.limits.max_concurrency),
            effect_of=self._effect_of,
            principal_of=self._principal_of,
            tracer=self.tracer,
            count=self.counter.count,
            tokenizer=self.counter.name,
        )
        self.fastmcp.add_middleware(self._middleware)

    @classmethod
    def from_config(cls, name: str, path: str | Any, **overrides: Any) -> OunceServer:
        """A server whose settings come from ``ouncemcp.toml`` at ``path``; keyword
        arguments override the file. See ``ouncemcp.core.config``."""
        from ouncemcp.core.config import load_config

        return cls(name, **{**load_config(path), **overrides})

    def _register_routes(self) -> None:
        """``/healthz``, ``/readyz`` and, with ``metrics=True``, ``/metrics`` on HTTP."""
        from starlette.requests import Request
        from starlette.responses import JSONResponse, PlainTextResponse, Response

        @self.fastmcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
        async def healthz(request: Request) -> Response:
            return JSONResponse({"status": "ok", "server": self.name})

        @self.fastmcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
        async def readyz(request: Request) -> Response:
            failing = await self.check_ready()
            body = {"status": "ready" if not failing else "not ready", "failing": failing}
            return JSONResponse(body, status_code=200 if not failing else 503)

        if self.metrics is not None:
            metrics = self.metrics

            @self.fastmcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
            async def prometheus(request: Request) -> Response:
                return PlainTextResponse(
                    metrics.prometheus(), media_type="text/plain; version=0.0.4"
                )

    async def check_ready(self) -> list[str]:
        """Names of failing readiness checks: the store, then each ``readiness`` check."""
        failing: list[str] = []
        try:
            await self.store.put("health", "probe", {"ok": True}, ttl_s=10)
            if await self.store.get("health", "probe") is None:
                failing.append("store")
        except Exception:  # noqa: BLE001 - any failure means not ready
            failing.append("store")
        for check in self.readiness:
            name = getattr(check, "__name__", "check")
            try:
                ok = check()
                if inspect.isawaitable(ok):
                    ok = await ok
            except Exception:  # noqa: BLE001
                ok = False
            if ok is not True:
                failing.append(name)
        return failing

    @classmethod
    def wrap(
        cls,
        mcp: FastMCP,
        *,
        effects: Mapping[str, Effect] | None = None,
        **kwargs: Any,
    ) -> OunceServer:
        """Put an existing FastMCP server under OunceMCP without changing its tools.

        Step 1 of a migration (DX-7). The wrapped server keeps its tools as they are and
        gains token accounting (``check``, ``dev``, ``cost_report``), categorized and
        redacted errors, request limits, traces and automatic discovery. Name each
        tool's effect in ``effects``; a tool without one is treated as a read, so a
        timeout is reported as ``transient`` instead of ``ambiguous_write``. Tools added
        to ``mcp`` after wrapping are served but not measured until wrapped again.
        ``mcp`` itself is changed (OunceMCP's middleware and transforms are added to
        it), so wrap a server once and serve the returned ``OunceServer``. Then move
        tools to ``@server.tool`` one at a time.
        """
        if not isinstance(mcp, FastMCP):
            raise TypeError("wrap() takes a FastMCP server")
        existing = _run_sync(mcp.list_tools())
        server = cls(mcp.name, _wrap=mcp, **kwargs)
        unknown = sorted(set(effects or {}) - {t.name for t in existing})
        if unknown:
            raise ValueError(f"effects names tools the server does not have: {', '.join(unknown)}")
        for name, effect in (effects or {}).items():
            if not isinstance(effect, Effect):
                raise TypeError(f"effects['{name}'] must be an Effect")
        server._wrapped_effects = dict(effects or {})
        for tool in existing:
            server._registered[tool.name] = tool
        return server

    def add_trace_sink(self, sink: TraceSink) -> None:
        """Send traces to one more sink, creating a tracer if the server had none."""
        if self.tracer is None:
            self.tracer = Tracer(sink)
        else:
            self.tracer.sinks = (*self.tracer.sinks, sink)
        middleware = getattr(self, "_middleware", None)
        if middleware is not None:  # during __init__ the middleware picks it up itself
            middleware._tracer = self.tracer

    def _principal_of(self, tool_name: str, fastmcp_ctx: FastMCPContext | None) -> str:
        """The caller a call counts against, resolved exactly as for cursors.

        The concurrency gate runs before a call has a ``ToolContext``, so it builds a bare
        one for the resolver. Using one resolver means a custom ``principal=`` names the
        same caller for cursors, concurrency limits and write resolution.
        """
        ctx = ToolContext(
            tool=tool_name,
            response_format="concise",
            result_budget=0,
            fastmcp=fastmcp_ctx,
            _resolve=self._resolve_principal,
        )
        return ctx.principal

    def _effect_of(self, tool_name: str) -> Effect | None:
        spec = self._specs.get(tool_name)
        return spec.effect if spec else self._wrapped_effects.get(tool_name)

    def _facts(self, tool_name: str) -> ToolFacts | None:
        """Declared facts for policy decisions (SEC-3): never the MCP annotations."""
        spec = self._specs.get(tool_name)
        return ToolFacts(effect=spec.effect, tags=spec.tags) if spec else None

    # ------------------------------------------------------------------ registration

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        effect: Effect | None = None,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        result_tokens: int | None = None,
        deadline_ms: int | None = None,
        tags: set[str] | None = None,
        min_items: int = 1,
        output_schema: OutputSchemaMode | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        policy: Policy | Sequence[Policy] | None = None,
        replay_safe: bool = False,
        idempotency: bool | Idempotency | None = None,
        approval: ApprovalRule = "never",
        preview: Preview | None = None,
        definition_tokens: int | None = None,
        budget_reason: str | None = None,
        parallel_safe: bool | None = None,
    ) -> Any:
        """Register a tool. ``effect`` is required.

        ``parallel_safe`` marks the tool as safe for clients to call in parallel with
        other calls, in ``_meta["ouncemcp/parallelSafe"]`` (EXE-5). Read tools are
        parallel-safe unless they depend on call order (a shared queue, a cursor kept on
        the server); set ``parallel_safe=False`` for those. Writes are never marked.

        ``result_tokens`` and ``definition_tokens`` give this tool its own budget (BUD-4):
        the concise result budget, and the definition size above which ``ouncemcp check``
        fails. Raising either above the server's budget needs ``budget_reason``, which
        ``check`` reports next to the tool; without one, registration warns.

        ``policy`` decides who may see and call the tool, in addition to the server's
        ``policy`` (all must allow). It gets the declared effect and the verified caller,
        never the MCP annotations (SEC-2, SEC-3). ``replay_safe=True`` declares that the
        handler can run again after an effect without repeating it, which lets a write
        tool ask for input after ``ctx.effect_applied()`` (SEC-7).

        ``output_schema`` sets whether this tool advertises its output schema: ``"on"``,
        ``"off"``, or ``"auto"`` (advertise it unless the definition would then be over
        ``Budget.tool_definition_target``). It defaults to the server's ``output_schemas``.

        ``auth`` is a FastMCP authorization check (or a list, all of which must pass).
        It decides who can see the tool in lists and search results, and who can call it,
        including by a guessed name.

        The handler can take either a single Pydantic model argument (its fields
        become the tool's top-level arguments) or ordinary keyword arguments, plus
        an optional ``ToolContext`` parameter. It must be annotated to return a
        Pydantic model, which defines the tool's output schema.
        """

        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            self._register(
                handler,
                effect=effect,
                name=name,
                description=description,
                title=title,
                result_tokens=result_tokens,
                deadline_ms=deadline_ms,
                tags=tags,
                min_items=min_items,
                output_schema=output_schema,
                auth=auth,
                policy=policy,
                replay_safe=replay_safe,
                idempotency=idempotency,
                approval=approval,
                preview=preview,
                definition_tokens=definition_tokens,
                budget_reason=budget_reason,
                parallel_safe=parallel_safe,
            )
            return handler

        if fn is not None:
            return decorator(fn)
        return decorator

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        max_tokens: int | None = None,
        policy: Policy | Sequence[Policy] | None = None,
        tags: set[str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a resource (CAP-2). ``{placeholders}`` in ``uri`` make a template.

        The handler returns ``str``, ``bytes``, a model, a dict or a list. Text over
        ``max_tokens`` (default ``Budget.result_ceiling``) is cut with a note; JSON over
        it is refused. ``policy`` works as for tools. See ``ouncemcp.core.primitives``.
        """

        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            from ouncemcp.core.primitives import register_resource

            register_resource(
                self,
                handler,
                uri,
                name=name,
                description=description,
                mime_type=mime_type,
                max_tokens=max_tokens,
                policy=policy,
                tags=tags,
            )
            return handler

        return decorator

    def prompt(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        max_tokens: int | None = None,
        policy: Policy | Sequence[Policy] | None = None,
        tags: set[str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a prompt (CAP-2). Its arguments come from the handler's parameters;
        prompts over ``max_tokens`` are refused. ``policy`` works as for tools."""

        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            from ouncemcp.core.primitives import register_prompt

            register_prompt(
                self,
                handler,
                name=name,
                description=description,
                max_tokens=max_tokens,
                policy=policy,
                tags=tags,
            )
            return handler

        return decorator

    async def _discovery_mode(self) -> DiscoveryMode:
        return (await self.discovery()).mode

    def _budget_override(
        self,
        tool_name: str,
        result_tokens: int | None,
        definition_tokens: int | None,
        reason: str | None,
    ) -> BudgetOverride | None:
        """BUD-4: a tool's own budget, which needs a reason when it raises the server's."""
        if definition_tokens is not None and (
            not isinstance(definition_tokens, int)
            or isinstance(definition_tokens, bool)
            or definition_tokens < 1
        ):
            raise ValueError(f"Tool '{tool_name}': definition_tokens must be a positive integer")
        if reason is not None and not (isinstance(reason, str) and reason.strip()):
            raise ValueError(f"Tool '{tool_name}': budget_reason must be a non-empty string")
        if result_tokens is None and definition_tokens is None:
            return None
        override = BudgetOverride(
            result_tokens=result_tokens,
            definition_tokens=definition_tokens,
            reason=reason.strip() if reason else None,
        )
        if override.raises(self.budget) and override.reason is None:
            raised = [
                f"{label} {value:,}"
                for label, value in (
                    ("result_tokens", result_tokens),
                    ("definition_tokens", definition_tokens),
                )
                if value is not None
            ]
            warnings.warn(
                BudgetOverrideWarning(
                    f"Tool '{tool_name}' raises its budget ({', '.join(raised)}) without "
                    "budget_reason; say why so reviewers and `ouncemcp check` can see it"
                ),
                stacklevel=4,
            )
        return override

    def job(self, name: str, *, max_attempts: int = 3) -> Callable[[Any], Any]:
        """Register a named background job that resumes after a restart.

        ``fn(args)`` or ``fn(args, attempt)`` runs the job; ``args`` is the dict passed to
        ``ctx.start_job(name, args)``, or a Pydantic model if the parameter is annotated
        with one. If the process running it stops, the next ``jobs.get`` (on any
        replica) starts it again from the beginning, up to ``max_attempts`` runs in all,
        so the job must be safe to repeat. Needs ``OunceServer(handles=True)``.
        """
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be an integer >= 1")

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Job '{name}' must be an async function")
            self.jobs.register(JobKind(name, fn, max_attempts))
            return fn

        return decorator

    def budget_overrides(self) -> dict[str, BudgetOverride]:
        """Each tool's own budget and its reason (BUD-4)."""
        return {
            name: spec.budget_override
            for name, spec in self._specs.items()
            if spec.budget_override is not None
        }

    def _register(
        self,
        handler: Callable[..., Any],
        *,
        effect: Effect | None,
        name: str | None,
        description: str | None,
        title: str | None,
        result_tokens: int | None,
        deadline_ms: int | None,
        tags: set[str] | None,
        min_items: int = 1,
        output_schema: OutputSchemaMode | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        policy: Policy | Sequence[Policy] | None = None,
        replay_safe: bool = False,
        idempotency: bool | Idempotency | None = None,
        approval: ApprovalRule = "never",
        preview: Preview | None = None,
        definition_tokens: int | None = None,
        budget_reason: str | None = None,
        parallel_safe: bool | None = None,
    ) -> None:
        tool_name = name or handler.__name__
        schema_mode = _tool_mode(output_schema, tool_name) or self.output_schemas
        if not isinstance(effect, Effect):
            raise TypeError(
                f"Tool '{tool_name}' must declare effect=Effect.READ, Effect.WRITE, "
                "or Effect.DESTRUCTIVE"
            )
        if not TOOL_NAME_RE.match(tool_name):
            raise ValueError(
                f"Invalid tool name '{tool_name}': use 1-128 ASCII letters, digits, "
                "'_', '-', or '.'"
            )
        if tool_name in META_TOOLS:
            raise ValueError(
                f"Tool name '{tool_name}' is reserved for OunceMCP's discovery meta-tools "
                f"({', '.join(META_TOOLS)})"
            )
        if tool_name in self._specs:
            raise ValueError(f"Tool '{tool_name}' is already registered")
        if deadline_ms is not None and (
            not isinstance(deadline_ms, int) or isinstance(deadline_ms, bool) or deadline_ms < 1
        ):
            raise ValueError(f"Tool '{tool_name}': deadline_ms must be a positive integer")
        if result_tokens is not None and not (0 < result_tokens <= self.budget.result_ceiling):
            raise ValueError(
                f"Tool '{tool_name}': result_tokens must be between 1 and the server's "
                f"result_ceiling ({self.budget.result_ceiling})"
            )
        if parallel_safe is not None and not isinstance(parallel_safe, bool):
            raise TypeError(f"Tool '{tool_name}': parallel_safe must be True, False or None")
        if parallel_safe and effect is not Effect.READ:
            raise ValueError(
                f"Tool '{tool_name}': only read tools can be parallel_safe; clients must "
                "order writes (EXE-5)"
            )
        concurrent = effect is Effect.READ and parallel_safe is not False
        override = self._budget_override(tool_name, result_tokens, definition_tokens, budget_reason)
        if not isinstance(min_items, int) or min_items < 1:
            raise ValueError(
                f"Tool '{tool_name}': min_items must be an integer >= 1; lists are never "
                "trimmed to zero items (spec RES-10)"
            )
        doc = description or inspect.getdoc(handler)
        if not doc:
            raise ValueError(
                f"Tool '{tool_name}' needs a description (docstring or description=...)"
            )

        hints = typing.get_type_hints(handler, include_extras=True)
        returned = hints.get("return")
        if not _is_model(returned):
            raise TypeError(
                f"Tool '{tool_name}' must be annotated to return a Pydantic model "
                "(typed outputs, spec CAP-1)"
            )
        output_model = cast(type[BaseModel], returned)
        validate_detail_fields(output_model)
        has_detail = model_has_detail_fields(output_model)
        page_fields = find_page_fields(output_model, tool_name)

        sig = inspect.signature(handler)
        ctx_param: str | None = None
        input_model: type[BaseModel] | None = None
        model_param: str | None = None
        plain_params: list[inspect.Parameter] = []
        for param in sig.parameters.values():
            annotation = hints.get(param.name, param.annotation)
            if annotation is ToolContext:
                ctx_param = param.name
            elif _is_model(annotation) and input_model is None and not plain_params:
                input_model, model_param = annotation, param.name
            else:
                plain_params.append(param.replace(annotation=annotation))
        if input_model is not None and plain_params:
            raise TypeError(
                f"Tool '{tool_name}': use either one input model or plain parameters, not both"
            )

        exposed: list[inspect.Parameter] = []
        if input_model is not None:
            for field_name, field_info in input_model.model_fields.items():
                exposed.append(
                    inspect.Parameter(
                        field_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        default=field_info,
                        annotation=field_info.annotation,
                    )
                )
        else:
            exposed = [p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in plain_params]

        for param in exposed:
            if param.name.lower() in RESERVED_PARAMS:
                raise ValueError(
                    f"Tool '{tool_name}': parameter '{param.name}' is reserved. Identity, "
                    "credentials, and approvals must come from verified context, not "
                    "model-controlled arguments (spec CAP-6)"
                )
            if param.name in INJECTED_PARAMS:
                raise ValueError(
                    f"Tool '{tool_name}': parameter name '{param.name}' is reserved by OunceMCP"
                )

        wrapper_params = list(exposed)
        if has_detail:
            wrapper_params.append(
                inspect.Parameter(
                    "response_format",
                    inspect.Parameter.KEYWORD_ONLY,
                    default="concise",
                    annotation=Literal["concise", "detailed"],
                )
            )
        wrapper_params.append(
            inspect.Parameter(
                "ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=FastMCPContext
            )
        )

        spec = ToolSpec(
            name=tool_name,
            effect=effect,
            output_model=output_model,
            input_model=input_model,
            has_detail=has_detail,
            result_tokens=result_tokens,
            min_items=min_items,
            page_fields=page_fields,
            tags=frozenset(tags or ()),
            replay_safe=bool(replay_safe),
            handler=handler,
            idempotency=as_idempotency(idempotency),
            approval=check_rule(approval, tool_name),
            preview=preview,
            budget_override=override,
            parallel_safe=concurrent,
        )
        if spec.idempotency is not None and effect is Effect.READ:
            raise ValueError(
                f"Tool '{tool_name}': idempotency is for write and destructive tools; reads "
                "can already be repeated safely"
            )
        policies = (*self.policies, *_as_policies(policy))
        checks: list[AuthCheck] = []
        if policies or self.audiences:
            checks.append(
                policy_check(
                    policies,
                    facts=self._facts,
                    audiences=self.audiences,
                    tenant_claims=self.tenant_claims,
                )
            )
        if auth is not None:
            checks.extend(auth if isinstance(auth, list) else [auth])
        spec = replace(spec, has_policy=bool(checks))
        # Filled in after registration, from the schema actually advertised.
        schema_state: dict[str, Any] = {"validator": None, "strict": True}
        is_async = inspect.iscoroutinefunction(handler)
        budget = self.budget
        counter = self.counter
        signer = self.cursors
        resolve_principal = self._resolve_principal
        limits = self.limits
        deadline_s = (deadline_ms or limits.default_deadline_ms) / 1000

        async def wrapper(**kwargs: Any) -> ToolResult:
            fastmcp_ctx = kwargs.pop("ctx", None)
            response_format: ResponseFormat = kwargs.pop("response_format", "concise")
            limit = (
                budget.result_ceiling
                if response_format == "detailed"
                else (spec.result_tokens or budget.result_tokens)
            )
            sent = dict(kwargs)
            call_kwargs: dict[str, Any]
            if input_model is not None:
                assert model_param is not None
                try:
                    call_kwargs = {model_param: input_model.model_validate(kwargs)}
                except PydanticValidationError as exc:
                    return Failure(
                        "validation",
                        "invalid_input",
                        format_validation(exc, limits.max_error_chars),
                    ).to_result()
            else:
                call_kwargs = dict(kwargs)
            tool_ctx = ToolContext(
                tool=tool_name,
                response_format=response_format,
                result_budget=limit,
                _output_model=output_model,
                fastmcp=fastmcp_ctx,
                _signer=signer,
                _arguments=lambda: (
                    input_model.model_validate(kwargs).model_dump(mode="json")
                    if input_model is not None
                    else kwargs
                ),
                _resolve=resolve_principal,
                effect=effect,
                replay_safe=spec.replay_safe,
                _tenant_claims=self.tenant_claims,
                _cache=self.cache,
                _state=self.state,
                _deps=self.deps,
                _store=self.store,
                _jobs=self.jobs if self.handles else None,
            )
            if ctx_param is not None:
                call_kwargs[ctx_param] = tool_ctx
            deadline_at = anyio.current_time() + deadline_s
            tool_ctx._deadline_at = deadline_at

            async def attempt(number: int) -> Any:
                tool_ctx.attempt = number
                if notes is not None:
                    notes["attempts"] = number
                tool_ctx._window = None
                if is_async:
                    return await handler(**call_kwargs)
                return await anyio.to_thread.run_sync(
                    lambda: handler(**call_kwargs), abandon_on_cancel=True
                )

            resolved = False
            replayed = False
            notes = CALL_NOTES.get()
            try:
                if spec.approval not in (None, "never"):
                    await self._approval_gate(spec, tool_ctx, fastmcp_ctx, call_kwargs)
                if spec.idempotency is not None:
                    rc = getattr(fastmcp_ctx, "request_context", None) if fastmcp_ctx else None
                    claim = make_claim(
                        tool=tool_name,
                        principal=tool_ctx.principal,
                        tenant=tool_ctx.identity.tenant,
                        arguments=tool_ctx._arguments(),
                        supplied=client_key(getattr(rc, "meta", None)),
                    )
                    result, replayed = await self.idempotency.run(
                        claim, spec.idempotency, lambda: attempt(1), deadline_at=deadline_at
                    )
                else:
                    result = await run_with_policy(
                        attempt,
                        effect=effect,
                        deadline_s=deadline_s,
                        retry=self.retry,
                        deadline_at=deadline_at,
                    )
                if not isinstance(result, output_model):
                    result = output_model.model_validate(result)
            except InputRequired as exc:
                return InputRequiredToolResult(exc.result)
            except InvalidCursor as exc:
                return _error_result(
                    str(exc),
                    {"error": "invalid_cursor", "category": "validation", "reason": exc.reason},
                )
            except Exception as exc:  # noqa: BLE001 - classified below, never leaked
                failure = classify(
                    exc, effect=effect, tool=tool_name, max_chars=limits.max_error_chars
                )
                known = None
                if failure.category == "ambiguous_write":
                    known = await self._resolve_write(
                        tool_name, tool_ctx.principal, sent, failure, output_model
                    )
                if known is None:
                    return failure.to_result()
                result, resolved = known, True
            page = None
            pf = spec.page_fields
            if pf is not None and tool_ctx._window is not None and tool_ctx._encode is not None:
                page = PageSpec(
                    list_key=pf.list_key,
                    cursor_key=pf.cursor_key,
                    start=tool_ctx._window.start,
                    count=len(tool_ctx._window.items),
                    encode=tool_ctx._encode,
                )
            try:
                shaped = shape_result(
                    result,
                    response_format=response_format,
                    budget=limit,
                    counter=counter,
                    validator=schema_state["validator"],
                    strict=schema_state["strict"],
                    min_items=spec.min_items,
                    page=page,
                    cursor_hint=pf.cursor_key if pf is not None else None,
                    assume_valid=True,
                )
            except ResultTooLarge as exc:
                return _too_large_result(tool_name, exc, response_format, counter.name)
            return ToolResult(
                content=[TextContent(type="text", text=shaped.text)],
                structured_content=shaped.data,
                meta={
                    "ouncemcp": {
                        **shaped.meta(),
                        **({"resolved_by": "write_resolver"} if resolved else {}),
                        **({"replayed": True} if replayed else {}),
                    }
                },
            )

        wrapper.__name__ = getattr(handler, "__name__", tool_name)
        wrapper.__qualname__ = wrapper.__name__
        wrapper.__doc__ = doc
        wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            wrapper_params, return_annotation=output_model
        )
        wrapper.__annotations__ = {p.name: p.annotation for p in wrapper_params}
        wrapper.__annotations__["return"] = output_model

        registered = FunctionTool.from_function(
            wrapper,
            name=tool_name,
            title=title,
            description=doc,
            annotations=ToolAnnotations.model_validate(effect.annotations()),
            tags=tags,
            auth=checks or None,
            meta={PARALLEL_SAFE_META: concurrent},
        )
        # Advertise compact schemas. Validation still runs on the Pydantic models.
        registered.parameters = compact_schema(registered.parameters)
        if registered.output_schema is not None:
            registered.output_schema = compact_schema(registered.output_schema)
        if schema_mode == "off" or (
            schema_mode == "auto"
            and self.counter.count_json(definition_payload(registered))
            > self.budget.tool_definition_target
        ):
            registered.output_schema = None
        # RES-8: shaping keeps results valid against the schema that is advertised. With
        # output schemas off, the model schema still guides shaping, and the text-only
        # fallback is allowed because no structured contract was promised.
        advertised = registered.output_schema
        schema_for_shaping = advertised or compact_schema(output_model.model_json_schema())
        schema_state["validator"] = jsonschema.Draft202012Validator(schema_for_shaping)
        schema_state["strict"] = advertised is not None
        self.fastmcp.add_tool(registered)
        self._specs[tool_name] = replace(
            spec, output_schema=schema_mode, output_schema_advertised=advertised is not None
        )
        self._registered[tool_name] = registered
        self._decision = None

    def _register_handle_tools(self) -> None:
        @self.tool(name="handles.read", effect=Effect.READ)
        async def read_handle(
            ctx: ToolContext,
            handle: str = Field(description="handle from a tool result"),
            cursor: str | None = Field(None, description="next_cursor from the previous page"),
            limit: int = Field(50, ge=1, le=200),
        ) -> HandlePage:
            """Page through a large result. Use it when a tool returns a handle; pass next_cursor to continue."""
            tenant = ctx.identity.tenant
            ref = ctx.verify_state(handle, HANDLE_KIND, request=binding(tenant))["id"]
            items = await get_items(ctx.store, str(ref))
            page = ctx.page(items, cursor=cursor, limit=limit)
            return HandlePage(items=page.items, total=len(items), next_cursor=page.next_cursor)

        @self.tool(name="jobs.get", effect=Effect.READ)
        async def get_job(
            ctx: ToolContext, job: str = Field(description="job from the tool that started it")
        ) -> JobStatus:
            """Check a background job. When done, read large results with handles.read."""
            ref = ctx.verify_state(job, JOB_KIND, request=binding(ctx.identity.tenant))["id"]
            record = await self.jobs.status(str(ref))
            resumed_now = False
            if record.get("state") == "interrupted":
                ttl = float(record.get("ttl_s", DEFAULT_JOB_TTL_S))
                resumed = await self.jobs.resume(str(ref), record, ctx._job_spill(int(ttl)))
                if resumed is not None:
                    record, resumed_now = resumed, True
            state = record.get("state", "interrupted")
            error = record.get("error")
            retry = None
            if state == "interrupted":
                kind = self.jobs.kinds.get(str(record.get("kind")))
                retry = (
                    f"The job stopped {record.get('attempt', 1)} times, using all "
                    f"{kind.max_attempts} attempts. Start it again."
                    if kind is not None
                    else "The job stopped when its server restarted. Start it again."
                )
            return JobStatus(
                state=state,
                result=record.get("result"),
                handle=record.get("handle"),
                items=record.get("items"),
                error=error.get("message") if isinstance(error, dict) else None,
                retry=retry,
                attempt=record.get("attempt") if record.get("kind") or resumed_now else None,
            )

    async def _approval_gate(
        self,
        spec: ToolSpec,
        ctx: ToolContext,
        fastmcp_ctx: FastMCPContext | None,
        call_kwargs: dict[str, Any],
    ) -> None:
        """Return when a person approved this exact call; raise otherwise (gate 4)."""
        assert spec.approval is not None
        arguments = ctx._arguments()
        preview = spec.preview
        summary = await summarize(
            (lambda: preview(**call_kwargs)) if preview is not None else None,
            spec.name,
            spec.effect,
            arguments,
        )
        request = ApprovalRequest(spec.name, spec.effect, arguments, ctx.identity, summary)
        if not await needed(spec.approval, request):
            return
        can_ask = _client_can_elicit(fastmcp_ctx)
        if fastmcp_ctx is not None and fastmcp_ctx._is_modern_protocol() and can_ask:
            raw = getattr(fastmcp_ctx, "request_state", None)
            responses = ctx.input_responses or {}
            answer = responses.get(APPROVAL_KEY) if hasattr(responses, "get") else None
            if raw:
                data = ctx.verify_state(raw, "input_required", request=ctx._request_key())
                if data.get("ouncemcp_approved") is True:
                    # Single use, like the approval itself: a continuation token must
                    # not carry one approval into several runs of the write.
                    await ctx.consume_state(raw, "input_required", request=ctx._request_key())
                    ctx._approved = True
                    return
                if data.get("ouncemcp_approval") is True and answer is not None:
                    await ctx.consume_state(raw, "input_required", request=ctx._request_key())
                    if not accepted(answer):
                        raise ApprovalDeclined()
                    ctx._approved = True
                    return
            ctx.require_input(
                {APPROVAL_KEY: confirmation(summary)}, state={"ouncemcp_approval": True}
            )
        if (
            fastmcp_ctx is not None
            and fastmcp_ctx.request_context is not None
            and can_ask
            and not fastmcp_ctx._is_modern_protocol()
        ):
            try:
                result = await fastmcp_ctx.elicit(summary, response_type=bool)
            except Exception:  # noqa: BLE001 - the client cannot ask; fall through
                result = None
            if result is not None:
                if not elicited_yes(result):
                    raise ApprovalDeclined()
                ctx._approved = True
                return
        if self.approver is not None:
            decision = self.approver(request)
            if inspect.isawaitable(decision):
                decision = await decision
            if decision is True:
                ctx._approved = True
                return
            raise ApprovalDeclined()
        raise ApprovalUnavailable()

    async def _resolve_write(
        self,
        tool_name: str,
        principal: str,
        arguments: dict[str, Any],
        failure: Failure,
        output_model: type[BaseModel],
    ) -> BaseModel | None:
        """Ask the write resolver whether a timed-out write took effect (EXE-3)."""
        resolver = self.write_resolver
        if resolver is None:
            return None
        attempt = WriteAttempt(
            tool=tool_name,
            principal=principal,
            arguments=arguments,
            reason=failure.code,
        )
        try:
            with anyio.fail_after(RESOLVE_TIMEOUT_S):
                outcome = await resolver.resolve(attempt)
            if outcome is None:
                return None
            if isinstance(outcome, output_model):
                return outcome
            return output_model.model_validate(outcome)
        except Exception:
            logger.warning(
                "ouncemcp: write resolver failed for %s; reporting the write as ambiguous",
                tool_name,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------ inspection

    @property
    def tools(self) -> dict[str, ToolSpec]:
        return dict(self._specs)

    @staticmethod
    async def _wire(tools: Iterable[Tool]) -> list[Any]:
        """Definitions of ``tools`` as a client receives them, with ``auth`` ignored."""
        probe = FastMCP("ouncemcp-probe")
        for tool in tools:
            probe.add_tool(tool.model_copy(update={"auth": None}))
        return await wire_tools(probe)

    async def catalog(self) -> list[Any]:
        """Every registered tool's definition, whatever the discovery mode and whoever asks.

        This is what ``direct`` mode sends over stdio. ``check`` measures it so that
        per-tool budgets apply to tools that ``search`` mode only sends on request.
        """
        return await self._wire(self._registered.values())

    async def discovery(self) -> DiscoveryDecision:
        """The discovery mode in force, and the measurements that decided it (DISC-1).

        Measured on the whole registered catalog, not per caller, so the mode and the
        meta-tool list do not change from one principal to the next (DISC-5). Recomputed
        after any tool is registered.
        """
        if self._decision is None:
            unknown = [name for name in self._search.pinned if name not in self._registered]
            if unknown:
                raise ValueError(
                    f"pinned tools are not registered: {', '.join(unknown)}. "
                    f"Registered: {', '.join(sorted(self._registered)) or 'none'}"
                )
            counter = self.counter
            direct = sum(counter.count_json(definition_payload(t)) for t in await self.catalog())
            surface = [
                *self._search.meta_tools,
                *(self._registered[n] for n in self._search.pinned),
            ]
            search = sum(
                counter.count_json(definition_payload(t)) for t in await self._wire(surface)
            )
            self._decision = decide_discovery(
                self.discovery_setting,
                direct_tokens=direct,
                search_tokens=search,
                threshold=self.budget.startup_tokens,
                pinned=self._search.pinned,
            )
        return self._decision

    async def measure_recall(
        self, cases: Sequence[RecallCase], *, k: int | None = None
    ) -> RecallReport:
        """Search recall at ``k`` over the whole catalog (DISC-6).

        ``k`` defaults to the server's ``search_results``. Works in either mode, so it
        can inform the choice. Pinned tools are not part of the search index.
        """
        if not cases or any(not case.expected for case in cases):
            raise ValueError("measure_recall needs at least one case, each expecting a tool")
        known = {name for name in self._registered if name not in self._search.pinned}
        unknown = sorted({n for case in cases for n in case.expected} - known)
        if unknown:
            raise ValueError(f"expected tools are not searchable: {', '.join(unknown)}")
        limit = k or self._search.max_results
        tools = list(self._registered.values())
        searchable = [t for t in tools if t.name in known]
        rankings = [
            [t.name for t in await self._search.rank(searchable, case.query)] for case in cases
        ]
        return build_report(cases, rankings, limit)

    async def list_tools(self) -> list[Any]:
        """Return the MCP tool definitions exactly as clients receive them.

        Definitions are fetched through an in-memory MCP client, so field order and
        serialization match the wire and token counts match a host-side recount.
        """
        return await wire_tools(self.fastmcp)

    async def cost_report(self) -> ServerCost:
        """Measure the token cost of this server's tool definitions."""
        modes = {name: spec.output_schema for name, spec in self._specs.items()}
        return measure_tools(
            await self.list_tools(), self.budget, self.counter, modes, self.budget_overrides()
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Call a tool in-process (useful for tests)."""
        return await self.fastmcp.call_tool(name, arguments or {})

    # ------------------------------------------------------------------ running

    def http_app(self, **kwargs: Any) -> Any:
        """The ASGI app for HTTP transports, with ``Host`` and ``Origin`` validation on
        (SEC-8). Pass ``allowed_hosts`` and ``allowed_origins`` for a public deployment."""
        kwargs.setdefault("host_origin_protection", True)
        return self.fastmcp.http_app(**kwargs)

    def run(
        self,
        transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio",
        *,
        check_budget: bool = True,
        **kwargs: Any,
    ) -> None:
        """Run the server. Prints a budget summary to stderr (stdout stays protocol-only).

        HTTP transports bind to ``127.0.0.1`` and validate ``Host`` and ``Origin`` by
        default (SEC-8). Binding another address needs ``allowed_hosts``.
        """
        if transport != "stdio":
            kwargs.setdefault("host", self.http_host)
            kwargs = resolve_http_options(kwargs)
        if check_budget:
            report = asyncio.run(self.cost_report())
            print(
                f"[ouncemcp] {self.name}: {report.total:,} startup tokens across "
                f"{len(report.tools)} tools ({report.tokenizer})",
                file=sys.stderr,
            )
            for tool in report.tools:
                if tool.status != "ok":
                    print(
                        f"[ouncemcp] {tool.status.upper()}: '{tool.name}' definition is "
                        f"{tool.total} tokens",
                        file=sys.stderr,
                    )
            decision = asyncio.run(self.discovery())
            print(f"[ouncemcp] discovery: {decision.mode} ({decision.reason})", file=sys.stderr)
            if report.startup_status == "warn":
                print(
                    f"[ouncemcp] WARN: startup context exceeds {self.budget.startup_tokens} "
                    "tokens; use discovery='auto' or 'search' to send less",
                    file=sys.stderr,
                )
        self.fastmcp.run(transport=transport, **kwargs)
