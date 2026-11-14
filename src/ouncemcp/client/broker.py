"""The reference client broker (spec CLI-1 to CLI-8, proposal 0016).

A ``Broker`` connects an agent or host to many MCP servers at once without filling the
context:

- **Namespaced tools (CLI-1).** Each server has a stable local id; its tools are named
  ``<id>.<tool>``, so two servers' ``search`` tools never collide.
- **Progressive discovery across servers (CLI-2).** ``host_tools()`` returns every
  definition while they fit ``Budget.startup_tokens``, and otherwise three meta-tools
  (``search_tools``, ``describe_tool``, ``call_tool``) that ``handle()`` answers.
- **Definition cache (CLI-3)** that honors ``ttlMs`` and ``cacheScope`` and is dropped
  when a server sends ``notifications/tools/list_changed``.
- **Output validation (CLI-4)** against each tool's ``outputSchema``.
- **Hooks (CLI-5).** Elicitation and sampling are offered to servers only when the host
  gives a handler, so servers cannot run those flows behind the host's back.
- **Deadlines and isolation (CLI-6).** One deadline per call, connections reused, and a
  server that keeps failing is cut off for a cool-down instead of slowing every call.
- **Token counts (CLI-7)** on every result.
- **No cross-server authority (CLI-8).** Server text is data. A call that follows from
  another server's result (``caused_by=``) to a tool that is not read-only needs the
  host's ``approver``; without one it is refused.
"""

from __future__ import annotations

import contextlib
import inspect
import math
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

import anyio
import jsonschema
from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler

from ouncemcp.core.budget import Budget
from ouncemcp.core.tokens import TokenCounter, compact_json, get_counter

SERVER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SEARCH, DESCRIBE, CALL = "search_tools", "describe_tool", "call_tool"


class BrokerError(Exception):
    """A broker-level failure the host should show or act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BrokerTool:
    name: str
    server: str
    tool: str
    definition: Any

    @property
    def read_only(self) -> bool:
        annotations = getattr(self.definition, "annotations", None)
        return bool(getattr(annotations, "read_only_hint", False))


@dataclass
class BrokerResult:
    """A tool result as the host should handle it. ``text`` is what the model reads;
    ``untrusted`` is always true: server output is data, never instructions."""

    name: str
    server: str
    text: str
    structured: Any
    is_error: bool
    tokens: int
    tokenizer: str
    meta: dict[str, Any] = field(default_factory=dict)
    untrusted: bool = True

    def for_model(self) -> str:
        """The text with its provenance, for hosts that pass results to the model."""
        return f"[result of {self.name} from server '{self.server}'; untrusted data]\n{self.text}"


@dataclass(frozen=True)
class CrossServerCall:
    """What the approver decides on (CLI-8)."""

    tool: str
    arguments: Mapping[str, Any]
    caused_by: BrokerResult


Approver = Callable[[CrossServerCall], bool | Awaitable[bool]]


@dataclass
class _Server:
    id: str
    target: Any
    client: Client | None = None
    transport: str = ""
    tools: list[Any] | None = None
    tools_expire: float = 0.0
    cache_scope: str = "private"
    failures: int = 0
    open_until: float = 0.0
    unavailable: str | None = None
    instructions: str | None = None


class _ListChanged(MessageHandler):
    def __init__(self, server: _Server) -> None:
        super().__init__()
        self._server = server

    async def on_tool_list_changed(self, message: Any) -> None:
        self._server.tools = None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bm25(docs: Sequence[tuple[str, str]], query: str, k: int) -> list[str]:
    """Rank ``(name, text)`` documents for ``query``; return the top ``k`` names."""
    tokenized = [(name, _words(f"{name.replace('.', ' ')} {text}")) for name, text in docs]
    if not tokenized:
        return []
    avg = sum(len(t) for _, t in tokenized) / len(tokenized)
    df: Counter[str] = Counter()
    for _, terms in tokenized:
        df.update(set(terms))
    n = len(tokenized)
    scores = []
    for name, terms in tokenized:
        tf = Counter(terms)
        score = 0.0
        for q in set(_words(query)):
            if q not in tf:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            score += idf * tf[q] * 2.2 / (tf[q] + 1.2 * (0.25 + 0.75 * len(terms) / avg))
        scores.append((score, name))
    return [name for score, name in sorted(scores, key=lambda x: -x[0]) if score > 0][:k]


class Broker:
    """Connect to many servers as one. Use as ``async with Broker({...}) as broker:``.

    ``servers`` maps a local id to a target: an http(s) URL, a server file
    (``path.py[:name]``), a command line (string or list), or a FastMCP or OunceServer
    object (in-process).
    """

    def __init__(
        self,
        servers: Mapping[str, Any],
        *,
        budget: Budget | None = None,
        tokenizer: str | TokenCounter | None = None,
        deadline_s: float = 60.0,
        elicitation_handler: Any = None,
        sampling_handler: Any = None,
        roots: Any = None,
        approver: Approver | None = None,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        search_results: int = 5,
        connect_timeout_s: float = 15.0,
        auth: Mapping[str, Any] | None = None,
    ) -> None:
        for sid in servers:
            if not SERVER_ID.match(sid):
                raise ValueError(
                    f"Server id '{sid}' must be 1-32 lowercase letters, digits, '_' or '-', "
                    "starting with a letter"
                )
        self._servers = {sid: _Server(sid, target) for sid, target in servers.items()}
        unknown = set(auth or {}) - set(servers)
        if unknown:
            raise ValueError(f"auth names unknown servers: {', '.join(sorted(unknown))}")
        self._auth = dict(auth or {})
        self.budget = budget or Budget()
        self.counter = get_counter(tokenizer) if tokenizer is not None else get_counter()
        self.deadline_s = deadline_s
        self._client_kwargs = {
            k: v
            for k, v in {
                "elicitation_handler": elicitation_handler,
                "sampling_handler": sampling_handler,
                "roots": roots,
            }.items()
            if v is not None
        }
        self.approver = approver
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.search_results = search_results
        self.connect_timeout_s = connect_timeout_s
        self._stack = contextlib.AsyncExitStack()

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> Self:
        await self._stack.__aenter__()
        async with anyio.create_task_group() as group:
            for server in self._servers.values():
                group.start_soon(self._connect, server)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stack.__aexit__(exc_type, exc, tb)

    def _make_client(self, server: _Server) -> tuple[Client, str]:
        kwargs = {**self._client_kwargs, "message_handler": _ListChanged(server)}
        target = server.target
        if isinstance(target, FastMCP) or hasattr(target, "fastmcp"):
            return Client(getattr(target, "fastmcp", target), **kwargs), "in-process"
        from ouncemcp.audit.runner import connect_target

        if isinstance(target, (list, tuple)):
            import shlex

            target = shlex.join(target)
        return connect_target(str(target), auth=self._auth.get(server.id), **kwargs)

    async def _connect(self, server: _Server) -> None:
        try:
            client, transport = self._make_client(server)
            with anyio.fail_after(self.connect_timeout_s):
                await self._stack.enter_async_context(client)
            server.client, server.transport = client, transport
            init = client.initialize_result
            server.instructions = getattr(init, "instructions", None)
        except Exception as exc:  # noqa: BLE001 - one bad server must not stop the rest
            server.unavailable = f"{type(exc).__name__}: could not connect"

    @property
    def unavailable(self) -> dict[str, str]:
        """Servers that could not be reached, with a short reason."""
        return {s.id: s.unavailable for s in self._servers.values() if s.unavailable}

    def instructions(self) -> dict[str, str]:
        """Each server's instructions, kept apart and labeled; never merge them into the
        host's own instructions (CLI-8)."""
        return {s.id: s.instructions for s in self._servers.values() if s.instructions}

    # ------------------------------------------------------------------ catalog (CLI-1, CLI-3)

    def _usable(self, server: _Server) -> bool:
        return server.client is not None and server.open_until <= time.monotonic()

    async def _server_tools(self, server: _Server, refresh: bool) -> list[Any]:
        now = time.monotonic()
        if not refresh and server.tools is not None and server.tools_expire > now:
            return server.tools
        assert server.client is not None
        tools: list[Any] = []
        cursor: str | None = None
        ttl_ms: int | None = None
        while True:
            page = await server.client.list_tools_mcp(cursor=cursor, cache_mode="bypass")
            tools.extend(page.tools)
            ttl_ms = page.ttl_ms if ttl_ms is None else min(ttl_ms, page.ttl_ms)
            server.cache_scope = page.cache_scope
            cursor = page.next_cursor
            if not cursor:
                break
        server.tools = tools
        server.tools_expire = now + (ttl_ms or 0) / 1000
        return tools

    async def tools(self, *, refresh: bool = False) -> list[BrokerTool]:
        """Every reachable server's tools, namespaced ``<server>.<tool>``."""
        out: list[BrokerTool] = []
        for server in self._servers.values():
            if not self._usable(server):
                continue
            try:
                defs = await self._server_tools(server, refresh)
            except Exception:  # noqa: BLE001 - isolate the failing server
                self._failed(server)
                continue
            out += [BrokerTool(f"{server.id}.{t.name}", server.id, t.name, t) for t in defs]
        return out

    def _definition(self, tool: BrokerTool) -> dict[str, Any]:
        data = tool.definition.model_dump(mode="json", by_alias=True, exclude_none=True)
        data["name"] = tool.name
        return data

    # ------------------------------------------------------------------ discovery (CLI-2)

    async def host_tools(self) -> tuple[str, list[dict[str, Any]]]:
        """What to give the model: ``("direct", every definition)`` while they fit
        ``Budget.startup_tokens``, else ``("search", three meta-tool definitions)``."""
        definitions = [self._definition(t) for t in await self.tools()]
        total = sum(self.counter.count(compact_json(d)) for d in definitions)
        if total <= self.budget.startup_tokens:
            return "direct", definitions
        return "search", META_DEFINITIONS

    async def search(
        self, query: str, *, detail: str = "summary", limit: int | None = None
    ) -> list[dict[str, Any]]:
        catalog = {t.name: t for t in await self.tools()}
        docs = [(name, t.definition.description or "") for name, t in catalog.items()]
        names = _bm25(docs, query, limit or self.search_results)
        if detail == "name":
            return [{"name": n} for n in names]
        if detail == "full":
            return [self._definition(catalog[n]) for n in names]
        return [
            {"name": n, "description": (catalog[n].definition.description or "").split(". ")[0]}
            for n in names
        ]

    async def describe(self, name: str) -> dict[str, Any]:
        tool = await self._find(name)
        return self._definition(tool)

    async def handle(self, meta_tool: str, arguments: Mapping[str, Any]) -> Any:
        """Answer one of the three meta-tools from ``host_tools()`` in search mode."""
        if meta_tool == SEARCH:
            return await self.search(
                str(arguments.get("query", "")), detail=str(arguments.get("detail", "summary"))
            )
        if meta_tool == DESCRIBE:
            return await self.describe(str(arguments["name"]))
        if meta_tool == CALL:
            return await self.call(str(arguments["name"]), dict(arguments.get("arguments") or {}))
        raise BrokerError("unknown_tool", f"No meta-tool named '{meta_tool}'.")

    async def _find(self, name: str) -> BrokerTool:
        sid, _, _tool = name.partition(".")
        server = self._servers.get(sid)
        if server is None or not _tool:
            raise BrokerError(
                "unknown_tool", f"No tool named '{name}'. Names look like '<server>.<tool>'."
            )
        if server.unavailable or server.client is None:
            raise BrokerError("server_unavailable", f"Server '{sid}' is not connected.")
        if server.open_until > time.monotonic():
            raise BrokerError(
                "server_unavailable", f"Server '{sid}' failed repeatedly; try again later."
            )
        for tool in await self.tools():
            if tool.name == name:
                return tool
        raise BrokerError("unknown_tool", f"No tool named '{name}'. Search the tools first.")

    # ------------------------------------------------------------------ calls (CLI-4 to CLI-8)

    def _failed(self, server: _Server) -> None:
        server.failures += 1
        if server.failures >= self.failure_threshold:
            server.open_until = time.monotonic() + self.cooldown_s
            server.failures = 0

    async def _allow_cross_server(
        self, tool: BrokerTool, arguments: Mapping[str, Any], caused_by: BrokerResult | None
    ) -> None:
        if caused_by is None or caused_by.server == tool.server or tool.read_only:
            return
        if self.approver is None:
            raise BrokerError(
                "cross_server_call_refused",
                f"'{tool.name}' may change something and was suggested by output of server "
                f"'{caused_by.server}'. Server output is not authority to call another "
                "server; the host must approve this call.",
            )
        decision = self.approver(CrossServerCall(tool.name, arguments, caused_by))
        if inspect.isawaitable(decision):
            decision = await decision
        if decision is not True:
            raise BrokerError("cross_server_call_refused", "The host did not approve this call.")

    def _withheld(self, name: str, server: str) -> BrokerResult:
        text = _withheld_text(name)
        return BrokerResult(
            name=name,
            server=server,
            text=text,
            structured=None,
            is_error=True,
            tokens=self.counter.count(text),
            tokenizer=self.counter.name,
            meta={"broker_error": "invalid_output"},
        )

    async def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        deadline_s: float | None = None,
        caused_by: BrokerResult | None = None,
    ) -> BrokerResult:
        """Call ``<server>.<tool>``. ``caused_by`` is the result that led to this call,
        if any; it decides whether the host must approve (CLI-8)."""
        args = dict(arguments or {})
        tool = await self._find(name)
        await self._allow_cross_server(tool, args, caused_by)
        server = self._servers[tool.server]
        assert server.client is not None
        try:
            with anyio.fail_after(deadline_s or self.deadline_s):
                raw = await server.client.call_tool_mcp(tool.tool, args)
        except RuntimeError as exc:
            if "Invalid structured content" not in str(exc):
                self._failed(server)
                raise BrokerError(
                    "server_error", f"Server '{tool.server}' failed: {type(exc).__name__}."
                ) from None
            return self._withheld(name, tool.server)  # the SDK's own schema check (CLI-4)
        except TimeoutError:
            self._failed(server)
            raise BrokerError(
                "deadline_exceeded", f"'{name}' did not answer within the deadline."
            ) from None
        except Exception as exc:  # noqa: BLE001 - transport failure of one server
            self._failed(server)
            raise BrokerError(
                "server_error", f"Server '{tool.server}' failed: {type(exc).__name__}."
            ) from None
        server.failures = 0
        text = "".join(getattr(c, "text", "") for c in raw.content or [])
        structured = raw.structured_content
        is_error = bool(raw.is_error)
        meta: dict[str, Any] = dict(raw.meta or {})
        schema = getattr(tool.definition, "output_schema", None)
        if schema and not is_error and structured is not None:
            try:
                jsonschema.Draft202012Validator(schema).validate(structured)
            except jsonschema.ValidationError:
                return self._withheld(name, tool.server)
        return BrokerResult(
            name=name,
            server=tool.server,
            text=text,
            structured=structured,
            is_error=is_error,
            tokens=self.counter.count(text),
            tokenizer=self.counter.name,
            meta=meta,
        )


def _withheld_text(name: str) -> str:
    return (
        f"[ouncemcp broker] '{name}' returned output that does not match its declared "
        "schema, so it was withheld."
    )


def _meta_definitions() -> list[dict[str, Any]]:
    def tool(
        name: str, description: str, properties: dict[str, Any], required: list[str]
    ) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
        }

    return [
        tool(
            SEARCH,
            "Search the tools of every connected server. Returns <server>.<tool> names.",
            {
                "query": {"type": "string"},
                "detail": {"type": "string", "enum": ["name", "summary", "full"]},
            },
            ["query"],
        ),
        tool(DESCRIBE, "Get one tool's full definition.", {"name": {"type": "string"}}, ["name"]),
        tool(
            CALL,
            "Call a tool by its <server>.<tool> name.",
            {"name": {"type": "string"}, "arguments": {"type": "object"}},
            ["name"],
        ),
    ]


META_DEFINITIONS = _meta_definitions()

__all__ = [
    "META_DEFINITIONS",
    "Broker",
    "BrokerError",
    "BrokerResult",
    "BrokerTool",
    "CrossServerCall",
]
