"""Security test catalog (spec EVAL-2).

Probes that a server author points at their own ``OunceServer``. Each one plays an
attacker against a real call path (in-process, through FastMCP's client) and reports
whether the server held. ``run_security_suite`` always runs the automatic probes
(guessed names and listing for every tool with a policy) plus the cases you add:

=====================  ==========================================================
Case                   The attack
=====================  ==========================================================
``GuessedName``        Call a protected tool by name as a caller who cannot see it
``CrossTenant``        Ask for another tenant's data with the same arguments
``CursorReuse``        Page with a cursor issued to another caller
``HandleReplay``       Use another caller's handle or approval, or reuse a spent one
``TokenPassthrough``   Make the tool call upstream and look for the caller's token
``SSRFProbe``          Point a URL argument at metadata, loopback and private hosts
=====================  ==========================================================

A caller is a dict of ``ouncemcp.testing.make_token`` fields, or ``None`` for an
anonymous caller. Use the report in a test::

    report = await run_security_suite(server, cases)
    assert report.passed, report.render_text()
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client

from ouncemcp.testing import as_caller

Caller = Mapping[str, Any] | None

CANARY_TOKEN = "ouncemcp-canary-token-7f3a9c2e5b1d"
SSRF_TARGETS = (
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:22/",
    "http://localhost/admin",
    "http://10.0.0.1/",
    "http://[::1]/",
    "http://metadata.google.internal/computeMetadata/v1/",
)


@dataclass(frozen=True)
class ProbeResult:
    case: str
    target: str
    passed: bool
    detail: str


@dataclass
class SecurityReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[ProbeResult]:
        return [r for r in self.results if not r.passed]

    def render_text(self) -> str:
        lines = [
            f"Security suite: {sum(r.passed for r in self.results)}/{len(self.results)} passed"
        ]
        for r in self.results:
            lines.append(f"  {'PASS' if r.passed else 'FAIL'} {r.case} {r.target}: {r.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [r.__dict__ for r in self.results],
        }


def _as(caller: Caller) -> Any:
    return as_caller(**dict(caller)) if caller is not None else nullcontext()


async def _call(server: Any, caller: Caller, tool: str, args: Mapping[str, Any]) -> Any:
    with _as(caller):
        async with Client(server.fastmcp) as client:
            return await client.call_tool(tool, dict(args), raise_on_error=False)


async def _names(server: Any, caller: Caller) -> set[str]:
    with _as(caller):
        async with Client(server.fastmcp) as client:
            return {t.name for t in await client.list_tools()}


def _data(result: Any) -> Any:
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    return "".join(getattr(c, "text", "") for c in result.content or [])


def _meta(result: Any) -> dict[str, Any]:
    return dict((getattr(result, "meta", None) or {}).get("ouncemcp") or {})


# ------------------------------------------------------------------ cases


@dataclass(frozen=True)
class GuessedName:
    """``intruder`` must neither see nor call ``tool``, and the refusal must look the
    same as for a tool that does not exist (spec DISC-3, SEC-2)."""

    tool: str
    intruder: Caller = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    async def run(self, server: Any) -> list[ProbeResult]:
        listed = self.tool in await _names(server, self.intruder)
        hidden = await _call(server, self.intruder, self.tool, self.arguments)
        missing = await _call(server, self.intruder, f"{self.tool}-does-not-exist", {})
        same = _meta(hidden).get("category") == _meta(missing).get("category")
        ok = not listed and bool(hidden.is_error) and same
        detail = (
            "hidden and refused like an unknown tool"
            if ok
            else f"listed={listed}, error={hidden.is_error}, same_as_unknown={same}"
        )
        return [ProbeResult("guessed-name", self.tool, ok, detail)]


@dataclass(frozen=True)
class CrossTenant:
    """``intruder`` calling ``tool`` with ``owner``'s arguments must not get ``owner``'s
    data. Passes when the intruder gets an error or different data."""

    tool: str
    arguments: Mapping[str, Any]
    owner: Caller
    intruder: Caller

    async def run(self, server: Any) -> list[ProbeResult]:
        mine = await _call(server, self.owner, self.tool, self.arguments)
        theirs = await _call(server, self.intruder, self.tool, self.arguments)
        if mine.is_error:
            return [
                ProbeResult("cross-tenant", self.tool, False, "owner call failed; fix the case")
            ]
        leaked = not theirs.is_error and _data(theirs) == _data(mine)
        detail = "intruder got the owner's data" if leaked else "owner data stayed private"
        return [ProbeResult("cross-tenant", self.tool, not leaked, detail)]


@dataclass(frozen=True)
class CursorReuse:
    """A cursor issued to ``owner`` must be refused for ``intruder`` (SEC-12)."""

    tool: str
    arguments: Mapping[str, Any]
    owner: Caller
    intruder: Caller
    cursor_field: str = "next_cursor"
    cursor_argument: str = "cursor"

    async def run(self, server: Any) -> list[ProbeResult]:
        first = await _call(server, self.owner, self.tool, self.arguments)
        data = _data(first)
        cursor = data.get(self.cursor_field) if isinstance(data, dict) else None
        if not cursor:
            return [
                ProbeResult("cursor-reuse", self.tool, False, "no cursor on page 1; fix the case")
            ]
        args = {**self.arguments, self.cursor_argument: cursor}
        reused = await _call(server, self.intruder, self.tool, args)
        ok = bool(reused.is_error)
        detail = "refused" if ok else "another caller could page with the owner's cursor"
        return [ProbeResult("cursor-reuse", self.tool, ok, detail)]


@dataclass(frozen=True)
class HandleReplay:
    """A handle from ``issue`` must fail for ``intruder`` and, when ``single_use``, on a
    second use by ``owner`` (SEC-6). ``handle_field`` names the field of the issuing
    result that holds the handle and ``handle_argument`` the argument of ``use``."""

    issue: str
    issue_arguments: Mapping[str, Any]
    use: str
    handle_field: str
    handle_argument: str
    owner: Caller
    intruder: Caller
    use_arguments: Mapping[str, Any] = field(default_factory=dict)
    single_use: bool = False

    async def run(self, server: Any) -> list[ProbeResult]:
        issued = await _call(server, self.owner, self.issue, self.issue_arguments)
        data = _data(issued)
        handle = data.get(self.handle_field) if isinstance(data, dict) else None
        if not handle:
            return [ProbeResult("handle-replay", self.use, False, "no handle issued; fix the case")]
        args = {**self.use_arguments, self.handle_argument: handle}
        stolen = await _call(server, self.intruder, self.use, args)
        results = [
            ProbeResult(
                "handle-cross-principal",
                self.use,
                bool(stolen.is_error),
                "refused" if stolen.is_error else "another caller used the handle",
            )
        ]
        if self.single_use:
            first = await _call(server, self.owner, self.use, args)
            again = await _call(server, self.owner, self.use, args)
            ok = not first.is_error and bool(again.is_error)
            results.append(
                ProbeResult(
                    "handle-replay",
                    self.use,
                    ok,
                    "second use refused" if ok else "the handle worked twice",
                )
            )
        return results


@dataclass(frozen=True)
class TokenPassthrough:
    """Run ``tool`` as a caller whose token is a canary, then check every recorded
    upstream request for it (SEC-4). ``recorded`` returns the headers of the upstream
    requests the tool made, for example from an ``httpx.MockTransport``."""

    tool: str
    arguments: Mapping[str, Any]
    recorded: Callable[[], Iterable[Mapping[str, Any]]]
    caller: Mapping[str, Any] = field(default_factory=lambda: {"subject": "canary-user"})

    async def run(self, server: Any) -> list[ProbeResult]:
        before = len(list(self.recorded()))
        await _call(server, {**self.caller, "token": CANARY_TOKEN}, self.tool, self.arguments)
        sent = list(self.recorded())[before:]
        leaked = any(CANARY_TOKEN in json.dumps(dict(h), default=str) for h in sent)
        detail = (
            "the caller's token reached an upstream request"
            if leaked
            else f"{len(sent)} upstream requests, none carried the token"
        )
        return [ProbeResult("token-passthrough", self.tool, not leaked, detail)]


@dataclass(frozen=True)
class SSRFProbe:
    """Every URL in ``targets`` passed as ``argument`` must be refused (SEC-9, P1)."""

    tool: str
    argument: str
    caller: Caller = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    targets: Sequence[str] = SSRF_TARGETS

    async def run(self, server: Any) -> list[ProbeResult]:
        results = []
        for url in self.targets:
            result = await _call(
                server, self.caller, self.tool, {**self.arguments, self.argument: url}
            )
            ok = bool(result.is_error)
            results.append(
                ProbeResult("ssrf", f"{self.tool} {url}", ok, "refused" if ok else "fetched")
            )
        return results


# ------------------------------------------------------------------ runner


def automatic_cases(server: Any) -> list[GuessedName]:
    """A guessed-name probe, as an anonymous caller, for every tool with a policy."""
    specs = getattr(server, "tools", {})
    return [GuessedName(name) for name, spec in sorted(specs.items()) if spec.has_policy]


async def run_security_suite(
    server: Any, cases: Iterable[Any] = (), *, automatic: bool = True
) -> SecurityReport:
    report = SecurityReport()
    planned = [*(automatic_cases(server) if automatic else []), *cases]
    for case in planned:
        report.results.extend(await case.run(server))
    return report


__all__ = [
    "CANARY_TOKEN",
    "SSRF_TARGETS",
    "CrossTenant",
    "CursorReuse",
    "GuessedName",
    "HandleReplay",
    "ProbeResult",
    "SSRFProbe",
    "SecurityReport",
    "TokenPassthrough",
    "automatic_cases",
    "run_security_suite",
]
