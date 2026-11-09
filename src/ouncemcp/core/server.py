"""``OunceServer``: a thin layer over FastMCP that makes lean tools the default.

M0 scope (spec section 15): typed tools with declared effects, definition token
counting, and result budgets with concise and detailed formats and truncation.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import anyio
from fastmcp import FastMCP
from fastmcp.server.context import Context as FastMCPContext
from fastmcp.tools import FunctionTool, ToolResult
from mcp_types import TextContent
from pydantic import BaseModel

from ouncemcp.core.budget import Budget
from ouncemcp.core.costs import ServerCost, measure_tools
from ouncemcp.core.effects import Effect
from ouncemcp.core.schema import compact_schema
from ouncemcp.core.shaping import (
    ResponseFormat,
    model_has_detail_fields,
    shape_result,
    validate_detail_fields,
)
from ouncemcp.core.tokens import DEFAULT_ENCODING, get_counter
from ouncemcp.core.wire import wire_tools

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


@dataclass(frozen=True)
class ToolContext:
    """Per-call information passed to handlers that declare a ``ToolContext`` parameter.

    Attributes:
        tool: The tool name.
        response_format: ``"concise"`` or ``"detailed"``. Handlers can skip expensive
            lookups for fields that will not be returned.
        result_budget: Token budget for this call's result.
        fastmcp: The underlying FastMCP context, when running inside a server.
    """

    tool: str
    response_format: ResponseFormat
    result_budget: int
    fastmcp: FastMCPContext | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    effect: Effect
    output_model: type[BaseModel]
    input_model: type[BaseModel] | None
    has_detail: bool
    result_tokens: int | None


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
    """

    def __init__(
        self,
        name: str,
        *,
        instructions: str | None = None,
        version: str | None = None,
        budget: Budget | None = None,
        tokenizer: str = DEFAULT_ENCODING,
        output_schemas: bool = True,
        **fastmcp_kwargs: Any,
    ) -> None:
        self.name = name
        self.budget = budget or Budget()
        self.counter = get_counter(tokenizer)
        self.fastmcp = FastMCP(name, instructions=instructions, version=version, **fastmcp_kwargs)
        self.output_schemas = output_schemas
        self._specs: dict[str, ToolSpec] = {}

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
    ) -> Any:
        """Register a tool. ``effect`` is required.

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
            )
            return handler

        if fn is not None:
            return decorator(fn)
        return decorator

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
    ) -> None:
        tool_name = name or handler.__name__
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
        if tool_name in self._specs:
            raise ValueError(f"Tool '{tool_name}' is already registered")
        if result_tokens is not None and not (0 < result_tokens <= self.budget.result_ceiling):
            raise ValueError(
                f"Tool '{tool_name}': result_tokens must be between 1 and the server's "
                f"result_ceiling ({self.budget.result_ceiling})"
            )
        doc = description or inspect.getdoc(handler)
        if not doc:
            raise ValueError(
                f"Tool '{tool_name}' needs a description (docstring or description=...)"
            )

        hints = typing.get_type_hints(handler, include_extras=True)
        output_model = hints.get("return")
        if not _is_model(output_model):
            raise TypeError(
                f"Tool '{tool_name}' must be annotated to return a Pydantic model "
                "(typed outputs, spec CAP-1)"
            )
        validate_detail_fields(output_model)
        has_detail = model_has_detail_fields(output_model)

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
        )
        is_async = inspect.iscoroutinefunction(handler)
        budget = self.budget
        counter = self.counter

        async def wrapper(**kwargs: Any) -> ToolResult:
            fastmcp_ctx = kwargs.pop("ctx", None)
            response_format: ResponseFormat = kwargs.pop("response_format", "concise")
            limit = (
                budget.result_ceiling
                if response_format == "detailed"
                else (spec.result_tokens or budget.result_tokens)
            )
            call_kwargs: dict[str, Any]
            if input_model is not None:
                call_kwargs = {model_param: input_model.model_validate(kwargs)}
            else:
                call_kwargs = dict(kwargs)
            if ctx_param is not None:
                call_kwargs[ctx_param] = ToolContext(
                    tool=tool_name,
                    response_format=response_format,
                    result_budget=limit,
                    fastmcp=fastmcp_ctx,
                )
            if is_async:
                result = await handler(**call_kwargs)
            else:
                result = await anyio.to_thread.run_sync(lambda: handler(**call_kwargs))
            if not isinstance(result, output_model):
                result = output_model.model_validate(result)
            shaped = shape_result(
                result, response_format=response_format, budget=limit, counter=counter
            )
            return ToolResult(
                content=[TextContent(type="text", text=shaped.text)],
                structured_content=shaped.data,
                meta={"ouncemcp": shaped.meta()},
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
            annotations=effect.annotations(),
            tags=tags,
            timeout=(deadline_ms / 1000) if deadline_ms else None,
        )
        # Advertise compact schemas. Validation still runs on the Pydantic models.
        registered.parameters = compact_schema(registered.parameters)
        if self.output_schemas and registered.output_schema is not None:
            registered.output_schema = compact_schema(registered.output_schema)
        else:
            registered.output_schema = None
        self.fastmcp.add_tool(registered)
        self._specs[tool_name] = spec

    # ------------------------------------------------------------------ inspection

    @property
    def tools(self) -> dict[str, ToolSpec]:
        return dict(self._specs)

    async def list_tools(self) -> list[Any]:
        """Return the MCP tool definitions exactly as clients receive them.

        Definitions are fetched through an in-memory MCP client, so field order and
        serialization match the wire and token counts match a host-side recount.
        """
        return await wire_tools(self.fastmcp)

    async def cost_report(self) -> ServerCost:
        """Measure the token cost of this server's tool definitions."""
        return measure_tools(await self.list_tools(), self.budget, self.counter)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Call a tool in-process (useful for tests)."""
        return await self.fastmcp.call_tool(name, arguments or {})

    # ------------------------------------------------------------------ running

    def run(self, transport: str = "stdio", *, check_budget: bool = True, **kwargs: Any) -> None:
        """Run the server. Prints a budget summary to stderr (stdout stays protocol-only)."""
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
            if report.startup_status == "warn":
                print(
                    f"[ouncemcp] WARN: startup context exceeds {self.budget.startup_tokens} "
                    "tokens; search-based discovery arrives in M1",
                    file=sys.stderr,
                )
        self.fastmcp.run(transport=transport, **kwargs)
