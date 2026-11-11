"""Resources and prompts (spec CAP-2, proposal 0014).

``@server.resource(uri)`` and ``@server.prompt()`` register MCP resources and prompts
with what tools already have:

- **Typed content.** A resource handler returns ``str``, ``bytes``, a Pydantic model, a
  dict or a list. Models are sent as compact JSON (``application/json``) without unset
  optional fields, like tool results.
- **Budgets.** Text is counted with the server's tokenizer. Text over the budget
  (``max_tokens``, default ``Budget.result_ceiling``) is cut with a note saying so; JSON
  over the budget is refused with an error, because cut JSON would not parse. Prompts
  over budget are refused.
- **Policy.** ``policy=`` (plus the server's policy and ``audience``) decides who can
  list and read them, exactly as for tools, including ``stdio_policy="enforce"``.
- **Caller context.** A handler can take a ``ToolContext`` parameter for the verified
  identity, ``ctx.deps`` and ``ctx.cache``.

URIs with ``{placeholders}`` become resource templates.
"""

from __future__ import annotations

import inspect
import json
import typing
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastmcp.server.context import Context as FastMCPContext
from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from ouncemcp.core.effects import Effect
from ouncemcp.core.errors import InvalidInput
from ouncemcp.security.policy import ToolFacts, policy_check

if TYPE_CHECKING:
    from ouncemcp.core.server import OunceServer

TRUNCATION_NOTE = (
    "\n\n[ouncemcp] Truncated: {shown:,} of {total:,} tokens shown. Read a narrower resource."
)


def _render(value: Any) -> tuple[str | bytes, str]:
    if isinstance(value, bytes):
        return value, "application/octet-stream"
    if isinstance(value, str):
        return value, "text/plain"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    plain = to_jsonable_python(value, fallback=str)
    return json.dumps(plain, separators=(",", ":"), ensure_ascii=False), "application/json"


def fit_text(text: str, limit: int, count: Callable[[str], int]) -> str:
    """``text`` cut to at most ``limit`` tokens including a note, or unchanged."""
    total = count(text)
    if total <= limit:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + TRUNCATION_NOTE.format(shown=count(text[:mid]), total=total)
        if count(candidate) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + TRUNCATION_NOTE.format(shown=count(text[:lo]), total=total)


def _split_context(handler: Callable[..., Any]) -> tuple[inspect.Signature, str | None]:
    """The handler's signature without a ``ToolContext`` parameter, and that parameter."""
    from ouncemcp.core.server import ToolContext

    hints = typing.get_type_hints(handler)
    sig = inspect.signature(handler)
    ctx_param = next((n for n in sig.parameters if hints.get(n) is ToolContext), None)
    params = [p for n, p in sig.parameters.items() if n != ctx_param]
    params.append(
        inspect.Parameter(
            "_fastmcp_ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=FastMCPContext
        )
    )
    return sig.replace(parameters=params, return_annotation=inspect.Signature.empty), ctx_param


def _checks(server: OunceServer, policy: Any, tags: set[str] | None) -> list[Any] | None:
    from ouncemcp.core.server import _as_policies

    policies = (*server.policies, *_as_policies(policy))
    if not policies and not server.audiences:
        return None
    facts = ToolFacts(effect=Effect.READ, tags=frozenset(tags or ()))
    return [
        policy_check(
            policies,
            facts=lambda _name: facts,
            audiences=server.audiences,
            tenant_claims=server.tenant_claims,
        )
    ]


def _context(server: OunceServer, name: str, fastmcp_ctx: Any) -> Any:
    from ouncemcp.core.server import ToolContext

    return ToolContext(
        tool=name,
        response_format="concise",
        result_budget=server.budget.result_ceiling,
        fastmcp=fastmcp_ctx,
        _signer=server.cursors,
        _resolve=server._resolve_principal,
        effect=Effect.READ,
        _tenant_claims=server.tenant_claims,
        _cache=server.cache,
        _state=server.state,
        _deps=server.deps,
        _store=server.store,
    )


async def _invoke(
    server: OunceServer, name: str, handler: Callable[..., Any], ctx_param: str | None, kwargs: dict
) -> Any:
    fastmcp_ctx = kwargs.pop("_fastmcp_ctx", None)
    if ctx_param is not None:
        kwargs[ctx_param] = _context(server, name, fastmcp_ctx)
    out = handler(**kwargs)
    return await out if inspect.isawaitable(out) else out


def register_resource(
    server: OunceServer,
    handler: Callable[..., Any],
    uri: str,
    *,
    name: str | None,
    description: str | None,
    mime_type: str | None,
    max_tokens: int | None,
    policy: Any,
    tags: set[str] | None,
) -> None:
    resource_name = name or handler.__name__
    doc = description or inspect.getdoc(handler)
    if not doc:
        raise ValueError(
            f"Resource '{resource_name}' needs a description (docstring or description=)"
        )
    limit = max_tokens or server.budget.result_ceiling
    if not 0 < limit <= server.budget.result_ceiling:
        raise ValueError(
            f"Resource '{resource_name}': max_tokens must be between 1 and result_ceiling "
            f"({server.budget.result_ceiling})"
        )
    sig, ctx_param = _split_context(handler)
    counter = server.counter

    async def wrapper(**kwargs: Any) -> str | bytes:
        content, _kind = _render(await _invoke(server, resource_name, handler, ctx_param, kwargs))
        if isinstance(content, bytes):
            return content
        if _kind == "application/json":
            tokens = counter.count(content)
            if tokens > limit:
                raise InvalidInput(
                    f"This resource is about {tokens:,} tokens, over its {limit:,}-token "
                    "limit. Read a narrower resource.",
                )
            return content
        return fit_text(content, limit, counter.count)

    wrapper.__name__ = resource_name
    wrapper.__doc__ = doc
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = {p.name: p.annotation for p in sig.parameters.values()}
    returned = typing.get_type_hints(handler).get("return")
    guessed = mime_type
    if guessed is None and isinstance(returned, type):
        if issubclass(returned, BaseModel) or returned in (dict, list):
            guessed = "application/json"
        elif returned is str:
            guessed = "text/plain"
    server.fastmcp.resource(
        uri,
        name=resource_name,
        description=doc,
        mime_type=guessed,
        tags=tags,
        auth=_checks(server, policy, tags),
    )(wrapper)


def register_prompt(
    server: OunceServer,
    handler: Callable[..., Any],
    *,
    name: str | None,
    description: str | None,
    max_tokens: int | None,
    policy: Any,
    tags: set[str] | None,
) -> None:
    prompt_name = name or handler.__name__
    doc = description or inspect.getdoc(handler)
    if not doc:
        raise ValueError(f"Prompt '{prompt_name}' needs a description (docstring or description=)")
    limit = max_tokens or server.budget.result_ceiling
    sig, ctx_param = _split_context(handler)
    counter = server.counter

    async def wrapper(**kwargs: Any) -> Any:
        result = await _invoke(server, prompt_name, handler, ctx_param, kwargs)
        text = (
            result
            if isinstance(result, str)
            else json.dumps(to_jsonable_python(result, fallback=str))
        )
        tokens = counter.count(text)
        if tokens > limit:
            raise InvalidInput(
                f"This prompt is about {tokens:,} tokens, over its {limit:,}-token limit. "
                "Use narrower arguments.",
            )
        return result

    wrapper.__name__ = prompt_name
    wrapper.__doc__ = doc
    wrapper.__signature__ = sig.replace(  # type: ignore[attr-defined]
        return_annotation=inspect.signature(handler).return_annotation
    )
    wrapper.__annotations__ = {p.name: p.annotation for p in sig.parameters.values()}
    server.fastmcp.prompt(
        name=prompt_name,
        description=doc,
        tags=tags,
        auth=_checks(server, policy, tags),
    )(wrapper)
