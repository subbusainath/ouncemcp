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
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ouncemcp.core.docs import rule_doc
from ouncemcp.core.effects import Effect

Severity = Literal["error", "warning"]

RULES: dict[str, Severity] = {
    "input-after-effect": "error",
    "elicit-in-write": "warning",
    "destructive-without-policy": "warning",
}


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
    for name, spec in sorted(specs.items()):
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
