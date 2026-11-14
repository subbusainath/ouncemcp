"""Policy and safety lints for ``ouncemcp check`` (spec DX-3, SEC-7).

Each finding names a rule, a severity and the page that explains it (DX-6). Errors make
``check`` exit non-zero, like a budget failure. Warnings are printed and do not fail.

Rules:

- ``input-after-effect`` (error): a write or destructive handler calls
  ``ctx.require_input`` after ``ctx.effect_applied()`` and the tool is not
  ``replay_safe``. The client answers by repeating the call, so the effect would happen
  twice (SEC-7). The same mistake is refused at run time.
- ``elicit-in-write`` (warning): a write or destructive handler calls ``elicit`` itself,
  which skips the SEC-7 guard. Use ``ctx.require_input``.
- ``destructive-without-policy`` (warning): a destructive tool has no ``policy`` or
  ``auth``, so any caller who can reach the server can run it (SEC-2).
- ``description-too-short`` (warning): fewer than five words, which rarely says what
  the tool is for (CAP-5).
- ``write-without-side-effects`` (warning): a write or destructive tool's description
  does not say what it changes (CAP-5).
- ``missing-usage-guidance`` (warning): in a server with several tools, the description
  never says when to use it or what to use instead (CAP-5).
- ``budget-override-without-reason`` (warning): a tool raises its result or definition
  budget above the server's without ``budget_reason`` (BUD-4).
- ``undocumented-parameter`` (warning): an input parameter has no description, and its
  type does not explain it (an enum, a boolean, a bounded number). Models fill
  parameters from their names and descriptions alone (DX-3).
- ``free-form-input`` (warning): an input parameter accepts any object or any value, so
  the model has to guess its structure; declare a model or typed fields (DX-3).
- ``unpaginated-list`` (warning): the output has a list of records but the tool has no
  cursor, so records beyond the result budget are dropped with no way to fetch them;
  mark the list ``Paginated`` and add a ``Cursor`` (DX-3, RES-3).
- ``inconsistent-tool-names`` (warning): the server's tool names mix separators
  (``tickets.search`` with ``create_ticket``), which makes related tools harder for a
  model to find and group (DX-3).
- ``stdio-skips-policy`` (warning): tools have policies but the server uses
  ``stdio_policy="trust"``, so over stdio they are visible and callable by anyone who
  can start the server. Ignore it for servers served only over HTTP.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ouncemcp.core.docs import rule_doc
from ouncemcp.core.effects import Effect

Severity = Literal["error", "warning"]

RULES: dict[str, Severity] = {
    "input-after-effect": "error",
    "elicit-in-write": "warning",
    "destructive-without-policy": "warning",
    "stdio-skips-policy": "warning",
    "description-too-short": "warning",
    "write-without-side-effects": "warning",
    "missing-usage-guidance": "warning",
    "budget-override-without-reason": "warning",
    "undocumented-parameter": "warning",
    "free-form-input": "warning",
    "unpaginated-list": "warning",
    "inconsistent-tool-names": "warning",
}

# Parameters OunceMCP adds to every tool; they are documented by the framework.
FRAMEWORK_PARAMETERS = frozenset({"response_format"})

SIDE_EFFECT = re.compile(
    r"(?i)\b(side effects?|creates?|deletes?|removes?|updates?|sends?|changes?|modif\w*|"
    r"writes?|notif\w*|charges?|closes?|opens?|refunds?|stores?|adds?|cancels?|"
    r"permanent\w*|cannot be undone)\b"
)
GUIDANCE = re.compile(
    r"(?i)\b(use|instead|only|before|after|when|unless|first|then|not|avoid|prefer)\b"
)


def lint_description(
    name: str, description: str, *, effect: Effect, siblings: int
) -> list[Finding]:
    """CAP-5 rules for one tool description."""
    findings: list[Finding] = []
    words = len(description.split())
    if words < 5:
        findings.append(
            Finding(
                "description-too-short",
                name,
                f"description has {words} words; say what the tool does and returns",
            )
        )
    if effect in (Effect.WRITE, Effect.DESTRUCTIVE) and not SIDE_EFFECT.search(description):
        findings.append(
            Finding(
                "write-without-side-effects",
                name,
                "description does not say what the tool changes; add 'Side effect: ...'",
            )
        )
    if siblings > 1 and not GUIDANCE.search(description):
        findings.append(
            Finding(
                "missing-usage-guidance",
                name,
                "description never says when to use the tool or what to use instead",
            )
        )
    return findings


@dataclass(frozen=True)
class Finding:
    rule: str
    tool: str
    message: str

    @property
    def severity(self) -> Severity:
        return RULES[self.rule]

    @property
    def docs(self) -> str:
        return rule_doc(self.rule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "tool": self.tool,
            "message": self.message,
            "docs": self.docs,
        }

    def render_text(self) -> str:
        return f"{self.severity.upper()} [{self.rule}] {self.tool}: {self.message} See {self.docs}"


def _calls(handler: Callable[..., Any]) -> dict[str, list[int]]:
    """Line numbers of method calls by name in the handler's source, if available."""
    try:
        source = textwrap.dedent(inspect.getsource(handler))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return {}
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.setdefault(node.func.attr, []).append(node.lineno)
    return found


def lint_tool(
    name: str,
    *,
    effect: Effect,
    handler: Callable[..., Any] | None,
    replay_safe: bool,
    has_policy: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    writes = effect in (Effect.WRITE, Effect.DESTRUCTIVE)
    calls = _calls(handler) if handler is not None and writes else {}
    effects, asks = calls.get("effect_applied", []), calls.get("require_input", [])
    if writes and not replay_safe and effects and any(a > min(effects) for a in asks):
        findings.append(
            Finding(
                "input-after-effect",
                name,
                "asks for input after ctx.effect_applied(); ask before the effect, or "
                "register the tool with replay_safe=True if repeating it is harmless",
            )
        )
    if writes and calls.get("elicit"):
        findings.append(
            Finding(
                "elicit-in-write",
                name,
                "calls elicit directly; use ctx.require_input so the replay guard applies",
            )
        )
    if effect is Effect.DESTRUCTIVE and not has_policy:
        findings.append(
            Finding(
                "destructive-without-policy",
                name,
                "is destructive and has no policy or auth; add policy=... to limit who can call it",
            )
        )
    return findings


def lint_server(server: Any) -> list[Finding]:
    """Lint every tool of an ``OunceServer``. Plain FastMCP servers have nothing to lint."""
    specs = getattr(server, "tools", None)
    if not isinstance(specs, dict):
        return []
    findings: list[Finding] = []
    guarded = sorted(name for name, spec in specs.items() if spec.has_policy)
    if guarded and getattr(server, "stdio_policy", "trust") == "trust":
        shown = ", ".join(guarded[:3]) + (
            f" and {len(guarded) - 3} more" if len(guarded) > 3 else ""
        )
        findings.append(
            Finding(
                "stdio-skips-policy",
                "(server)",
                f"policies on {shown} do not apply over stdio with stdio_policy='trust'; "
                "set stdio_policy='enforce' if the server is also run over stdio by "
                "untrusted users",
            )
        )
    descriptions = {
        t.name: t.description or "" for t in getattr(server, "_registered", {}).values()
    }
    budget = getattr(server, "budget", None)
    registered = getattr(server, "_registered", {})
    findings.extend(lint_tool_names(list(specs)))
    for name, spec in sorted(specs.items()):
        tool = registered.get(name)
        if tool is not None:
            findings.extend(lint_schema(name, getattr(tool, "parameters", None) or {}))
        findings.extend(
            lint_output_lists(
                name,
                getattr(spec, "output_model", None),
                getattr(spec, "page_fields", None) is not None,
            )
        )
        override = getattr(spec, "budget_override", None)
        if (
            override is not None
            and budget is not None
            and override.raises(budget)
            and override.reason is None
        ):
            findings.append(
                Finding(
                    "budget-override-without-reason",
                    name,
                    "raises its budget without budget_reason; say why the tool needs it",
                )
            )
        findings.extend(
            lint_description(
                name,
                descriptions.get(name, ""),
                effect=spec.effect,
                siblings=len(specs),
            )
        )
        findings.extend(
            lint_tool(
                name,
                effect=spec.effect,
                handler=spec.handler,
                replay_safe=spec.replay_safe,
                has_policy=spec.has_policy,
            )
        )
    return findings


def _resolve_ref(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, Mapping) else {}
        return {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def _variants(schema: Mapping[str, Any], root: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The non-null alternatives of a property schema (``anyOf``/``oneOf``)."""
    schema = _resolve_ref(schema, root)
    options = schema.get("anyOf") or schema.get("oneOf")
    if not options:
        return [schema]
    out = []
    for option in options:
        option = _resolve_ref(option, root)
        if option.get("type") != "null":
            out.append(option)
    return out or [schema]


def _self_explaining(schema: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    for option in _variants(schema, root):
        kind = option.get("type")
        if "enum" in option or "const" in option or kind == "boolean":
            continue
        if kind in ("integer", "number") and "minimum" in option and "maximum" in option:
            continue
        return False
    return True


# Schema keys that describe a value without constraining its shape.
_ANNOTATION_KEYS = frozenset({"description", "default", "title", "examples", "deprecated"})


def _free_form(schema: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    for option in _variants(schema, root):
        shape = set(option) - _ANNOTATION_KEYS
        if not shape or shape == {"additionalProperties"}:
            return True  # any value at all
        if option.get("type") == "object" and not option.get("properties"):
            extra = option.get("additionalProperties", True)
            if extra is True or extra == {}:
                return True
    return False


def lint_schema(name: str, parameters: Mapping[str, Any]) -> list[Finding]:
    """DX-3 schema rules for one tool's advertised input schema."""
    findings: list[Finding] = []
    properties = parameters.get("properties") or {}
    undocumented, free = [], []
    for param, schema in properties.items():
        if param in FRAMEWORK_PARAMETERS or not isinstance(schema, Mapping):
            continue
        resolved = _resolve_ref(schema, parameters)
        if _free_form(resolved, parameters):
            free.append(param)
        elif not resolved.get("description") and not _self_explaining(resolved, parameters):
            undocumented.append(param)
    if undocumented:
        findings.append(
            Finding(
                "undocumented-parameter",
                name,
                f"{', '.join(undocumented)} {'has' if len(undocumented) == 1 else 'have'} "
                "no description; say what the value is and where it comes from",
            )
        )
    if free:
        findings.append(
            Finding(
                "free-form-input",
                name,
                f"{', '.join(free)} {'accepts' if len(free) == 1 else 'accept'} any "
                "structure; declare a model or typed fields",
            )
        )
    return findings


def lint_output_lists(name: str, output_model: Any, paginated: bool) -> list[Finding]:
    """DX-3: a list of records the caller cannot page through."""
    if paginated or output_model is None:
        return []
    from pydantic import BaseModel

    from ouncemcp.core.shaping import nested_models

    lists = []
    for field_name, info in output_model.model_fields.items():
        if any(type(m).__name__ == "MaxLen" for m in info.metadata):
            continue  # a declared bound (Field(max_length=...)) fits the budget by design
        origin = typing.get_origin(info.annotation)
        args = typing.get_args(info.annotation)
        if origin is typing.Union or isinstance(info.annotation, types.UnionType):
            inner = [a for a in args if a is not type(None)]
            if len(inner) == 1:
                origin, args = typing.get_origin(inner[0]), typing.get_args(inner[0])
        if (
            origin in (list, tuple)
            and args
            and any(
                isinstance(m, type) and issubclass(m, BaseModel) for m in nested_models(args[0])
            )
        ):
            lists.append(field_name)
    if not lists:
        return []
    return [
        Finding(
            "unpaginated-list",
            name,
            f"{', '.join(lists)} {'is a list' if len(lists) == 1 else 'are lists'} of records "
            "with no cursor; mark it Paginated and add a Cursor so callers can fetch the rest",
        )
    ]


def _name_style(name: str) -> str | None:
    styles = [
        style
        for sep, style in ((".", "dotted"), ("_", "snake_case"), ("-", "kebab-case"))
        if sep in name
    ]
    if len(styles) == 1:
        return styles[0]
    if not styles:
        return None
    # "tickets.search_open" mixes styles inside a namespace; the namespace separator wins.
    return "dotted" if "." in name else styles[0]


# Tools OunceMCP adds (handles=True); their names do not set the server's style.
FRAMEWORK_TOOLS = frozenset({"handles.read", "jobs.get"})


def lint_tool_names(names: Sequence[str]) -> list[Finding]:
    """DX-3: one naming style across a server's tools."""
    styles: dict[str, list[str]] = {}
    for tool in sorted(set(names) - FRAMEWORK_TOOLS):
        style = _name_style(tool)
        if style is not None:
            styles.setdefault(style, []).append(tool)
    if len(styles) < 2:
        return []
    summary = "; ".join(
        f"{style}: {', '.join(tools[:3])}" for style, tools in sorted(styles.items())
    )
    return [
        Finding(
            "inconsistent-tool-names",
            "(server)",
            f"tool names mix styles ({summary}); use one, e.g. namespace.verb",
        )
    ]
