"""The audit engine behind ``ouncemcp audit`` (spec section 8.6)."""

from __future__ import annotations

import json
import os
import re
import shlex
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastmcp import Client

from ouncemcp.core.budget import Budget
from ouncemcp.core.costs import ServerCost, measure_tools
from ouncemcp.core.tokens import TokenCounter, compact_json, get_counter
from ouncemcp.discovery import decide_discovery

REPORT_VERSION = 1
LONG_DESCRIPTION_TOKENS = 200
DEEP_NESTING = 4
LARGE_ENUM = 25
MANY_OPTIONAL = 12
LATENCY_RUNS = 5


@dataclass(frozen=True)
class BloatFinding:
    rule: str
    tool: str
    detail: str


@dataclass
class AuditReport:
    target: str
    transport: str
    protocol_version: str | None
    server_name: str | None
    tokenizer: str
    cost: ServerCost
    resources: dict[str, int] = field(default_factory=dict)
    prompts: dict[str, int] = field(default_factory=dict)
    findings: list[BloatFinding] = field(default_factory=list)
    connect_ms: float = 0.0
    list_ms_p50: float = 0.0
    list_ms_p95: float = 0.0
    recommendation: dict[str, Any] = field(default_factory=dict)
    samples: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def startup_tokens(self) -> int:
        return self.cost.total + sum(self.resources.values()) + sum(self.prompts.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "target": self.target,
            "transport": self.transport,
            "protocol_version": self.protocol_version,
            "server_name": self.server_name,
            "tokenizer": self.tokenizer,
            "tool_tokens": self.cost.total,
            "startup_tokens": self.startup_tokens,
            "tools": [
                {
                    "name": t.name,
                    "total": t.total,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                    "other": t.name_tokens + t.structure,
                    "status": t.status,
                }
                for t in self.cost.tools
            ],
            "resources": self.resources,
            "prompts": self.prompts,
            "findings": [asdict(f) for f in self.findings],
            "latency_ms": {
                "connect": round(self.connect_ms, 1),
                "list_p50": round(self.list_ms_p50, 1),
                "list_p95": round(self.list_ms_p95, 1),
            },
            "recommendation": self.recommendation,
            "samples": self.samples,
        }

    def render_text(self) -> str:
        lines = [
            f"Audit of {self.target} ({self.transport}, protocol {self.protocol_version or '?'})",
            f"Tokenizer        {self.tokenizer}",
            (
                f"Startup context  {self.startup_tokens:,} tokens: tools {self.cost.total:,}, "
                f"resources {sum(self.resources.values()):,}, "
                f"prompts {sum(self.prompts.values()):,}"
            ),
            "",
            f"{'Tool':<32} {'Total':>6} {'Desc':>6} {'Input':>6} {'Output':>6}  Status",
        ]
        for t in sorted(self.cost.tools, key=lambda t: -t.total):
            lines.append(
                f"{t.name[:32]:<32} {t.total:>6} {t.description:>6} {t.input_schema:>6} "
                f"{t.output_schema:>6}  {t.status.upper()}"
            )
        rec = self.recommendation
        lines += [
            "",
            f"Discovery        recommend {rec.get('mode')}: {rec.get('reason')}",
            (
                f"Latency          connect {self.connect_ms:.0f} ms; tools/list p50 "
                f"{self.list_ms_p50:.0f} ms, p95 {self.list_ms_p95:.0f} ms"
            ),
        ]
        if self.findings:
            lines.append("Findings")
            lines += [f"  [{f.rule}] {f.tool}: {f.detail}" for f in self.findings]
        for name, sample in self.samples.items():
            lines.append(
                f"Sample {name}: result tokens median {sample['median']:,}, "
                f"p95 {sample['p95']:,} over {sample['runs']} calls"
            )
        return "\n".join(lines)

    def render_markdown(self) -> str:
        rows = "\n".join(
            f"| `{t.name}` | {t.total} | {t.description} | {t.input_schema} | "
            f"{t.output_schema} | {t.status} |"
            for t in sorted(self.cost.tools, key=lambda t: -t.total)
        )
        findings = (
            "\n".join(f"- `{f.rule}` on `{f.tool}`: {f.detail}" for f in self.findings) or "None."
        )
        rec = self.recommendation
        return (
            f"# Audit of `{self.target}`\n\n"
            f"Transport {self.transport}, protocol {self.protocol_version or 'unknown'}, "
            f"tokenizer `{self.tokenizer}`.\n\n"
            f"**Startup context: {self.startup_tokens:,} tokens** (tools {self.cost.total:,}, "
            f"resources {sum(self.resources.values()):,}, prompts "
            f"{sum(self.prompts.values()):,}). Recommended discovery: **{rec.get('mode')}** "
            f"({rec.get('reason')}).\n\n"
            "| Tool | Total | Description | Input | Output | Status |\n"
            "| --- | ---: | ---: | ---: | ---: | --- |\n"
            f"{rows}\n\n## Findings\n\n{findings}\n"
        )


# ------------------------------------------------------------------ connecting (AUD-1, AUD-2)


def connect_target(
    target: str,
    *,
    headers: Mapping[str, str] | None = None,
    bearer: str | None = None,
    env: Mapping[str, str] | None = None,
    inherit_env: bool = False,
    auth: Any = None,
    **client_kwargs: Any,
) -> tuple[Client, str]:
    """A client for ``target``: an http(s) URL, a ``.py`` file or ``module:attr`` with a
    server object (in-process), or a command line that starts a stdio server.

    For HTTP, ``auth`` is an ``OunceOAuth`` (SEC-10) or any httpx auth; it takes
    precedence over ``bearer``."""
    if re.match(r"^https?://", target):
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(
            target, headers=dict(headers or {}), auth=auth if auth is not None else bearer
        )
        return Client(transport, **client_kwargs), "http"
    ref = target.partition(":")[0]
    if ref.endswith(".py") and " " not in target and Path(ref).exists():
        from ouncemcp.cli.loader import load_server

        server = load_server(target)
        return Client(getattr(server, "fastmcp", server), **client_kwargs), "in-process"
    parts = shlex.split(target)
    if not parts:
        raise ValueError("audit needs a URL, a server file, or a command")
    from fastmcp.client.transports import StdioTransport

    child_env = dict(os.environ) if inherit_env else {}
    child_env.update(env or {})
    stdio = StdioTransport(parts[0], parts[1:], env=child_env or None)
    return Client(stdio, **client_kwargs), "stdio"


# ------------------------------------------------------------------ bloat lints (AUD-5)


def _depth(schema: Any, level: int = 0) -> int:
    if isinstance(schema, dict):
        return max([level, *(_depth(v, level + 1) for v in schema.values())])
    if isinstance(schema, list):
        return max([level, *(_depth(v, level) for v in schema)])
    return level


def _enums(schema: Any) -> int:
    if isinstance(schema, dict):
        own = len(schema["enum"]) if isinstance(schema.get("enum"), list) else 0
        return max([own, *(_enums(v) for v in schema.values())])
    if isinstance(schema, list):
        return max([0, *(_enums(v) for v in schema)])
    return 0


def _sentences(text: str) -> list[str]:
    return [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 30]


def bloat(tools: Sequence[Any], counter: TokenCounter) -> list[BloatFinding]:
    findings: list[BloatFinding] = []
    seen: Counter[str] = Counter()
    for tool in tools:
        description = tool.description or ""
        seen.update(set(_sentences(description)))
        tokens = counter.count(description)
        if tokens > LONG_DESCRIPTION_TOKENS:
            findings.append(
                BloatFinding("long-description", tool.name, f"description is {tokens} tokens")
            )
        schema = tool.input_schema or {}
        depth = _depth(schema.get("properties", {})) // 2
        if depth > DEEP_NESTING:
            findings.append(
                BloatFinding("deep-nesting", tool.name, f"input nests about {depth} levels")
            )
        enum = _enums(schema)
        if enum > LARGE_ENUM:
            findings.append(BloatFinding("large-enum", tool.name, f"an enum has {enum} values"))
        props = schema.get("properties", {}) or {}
        optional = len(set(props) - set(schema.get("required", []) or []))
        if optional > MANY_OPTIONAL:
            findings.append(
                BloatFinding("many-optional-fields", tool.name, f"{optional} optional input fields")
            )
    repeated = [s for s, n in seen.items() if n >= 3]
    if repeated:
        findings.append(
            BloatFinding(
                "duplicated-text",
                "(server)",
                f"{len(repeated)} sentences repeat in 3 or more tool descriptions; move "
                "shared guidance to the server instructions",
            )
        )
    return findings


# ------------------------------------------------------------------ the audit


def _definition_tokens(item: Any, counter: TokenCounter) -> int:
    return counter.count(
        compact_json(item.model_dump(mode="json", by_alias=True, exclude_none=True))
    )


async def _search_surface_tokens(counter: TokenCounter) -> int:
    """What OunceMCP's search mode would send at startup (the three meta-tools)."""
    from ouncemcp.core.server import OunceServer

    probe = OunceServer("ouncemcp-audit-probe", discovery="search", tokenizer=counter)
    return (await probe.cost_report()).total


def _result_tokens(result: Any, counter: TokenCounter) -> int:
    text = "".join(getattr(c, "text", "") for c in (result.content or []))
    return counter.count(text)


async def audit(
    target: str,
    *,
    budget: Budget | None = None,
    tokenizer: str | TokenCounter | None = None,
    samples: Mapping[str, Mapping[str, Any]] | None = None,
    sample_runs: int = 3,
    headers: Mapping[str, str] | None = None,
    bearer: str | None = None,
    env: Mapping[str, str] | None = None,
    inherit_env: bool = False,
    auth: Any = None,
) -> AuditReport:
    """Measure ``target``. Tools are only called when named in ``samples``. ``auth`` is an
    ``OunceOAuth`` for OAuth-protected HTTP servers (SEC-10)."""
    budget = budget or Budget()
    counter = get_counter(tokenizer) if tokenizer is not None else get_counter()
    client, transport = connect_target(
        target, headers=headers, bearer=bearer, env=env, inherit_env=inherit_env, auth=auth
    )
    started = time.perf_counter()
    async with client:
        connect_ms = (time.perf_counter() - started) * 1000
        init = client.initialize_result
        protocol = client.protocol_version or getattr(init, "protocol_version", None)
        timings, tools = [], []
        for _ in range(LATENCY_RUNS):
            t0 = time.perf_counter()
            tools = await client.list_tools()
            timings.append((time.perf_counter() - t0) * 1000)
        resources: dict[str, int] = {}
        prompts: dict[str, int] = {}
        caps = getattr(init, "capabilities", None)
        if caps is None or getattr(caps, "resources", None) is not None:
            for res in await client.list_resources():
                resources[str(res.uri)] = _definition_tokens(res, counter)
            for tpl in await client.list_resource_templates():
                resources[tpl.uri_template] = _definition_tokens(tpl, counter)
        if caps is None or getattr(caps, "prompts", None) is not None:
            for prompt in await client.list_prompts():
                prompts[prompt.name] = _definition_tokens(prompt, counter)
        sampled: dict[str, dict[str, Any]] = {}
        for name, args in (samples or {}).items():
            sizes = []
            for _ in range(max(1, sample_runs)):
                result = await client.call_tool(name, dict(args), raise_on_error=False)
                sizes.append(_result_tokens(result, counter))
            ordered = sorted(sizes)
            sampled[name] = {
                "runs": len(sizes),
                "median": int(statistics.median(ordered)),
                "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            }
    cost = measure_tools(tools, budget, counter)
    total = cost.total + sum(resources.values()) + sum(prompts.values())
    decision = decide_discovery(
        "auto",
        direct_tokens=cost.total,
        search_tokens=await _search_surface_tokens(counter),
        threshold=budget.startup_tokens,
        pinned=(),
    )
    ordered_timings = sorted(timings)
    return AuditReport(
        target=target,
        transport=transport,
        protocol_version=protocol,
        server_name=getattr(getattr(init, "server_info", None), "name", None),
        tokenizer=counter.name,
        cost=cost,
        resources=resources,
        prompts=prompts,
        findings=bloat(tools, counter),
        connect_ms=connect_ms,
        list_ms_p50=statistics.median(ordered_timings),
        list_ms_p95=ordered_timings[-1],
        recommendation={
            "mode": decision.mode,
            "reason": decision.reason,
            "startup_tokens_if_followed": (
                total - cost.total + decision.search_tokens if decision.mode == "search" else total
            ),
        },
        samples=sampled,
    )


# ------------------------------------------------------------------ comparing (AUD-13)


def compare(
    old: Mapping[str, Any], new: Mapping[str, Any], *, tolerance: float = 0.05
) -> list[str]:
    """Regressions from report ``old`` to ``new`` (dicts from ``to_dict``)."""
    out: list[str] = []
    before, after = int(old.get("startup_tokens", 0)), int(new.get("startup_tokens", 0))
    if before and after > before * (1 + tolerance):
        out.append(f"startup tokens grew from {before:,} to {after:,}")
    old_tools = {t["name"]: t["total"] for t in old.get("tools", [])}
    for tool in new.get("tools", []):
        prev = old_tools.get(tool["name"])
        if prev is not None and tool["total"] > prev * (1 + tolerance):
            out.append(f"{tool['name']} grew from {prev} to {tool['total']} tokens")
    old_findings = {(f["rule"], f["tool"]) for f in old.get("findings", [])}
    for f in new.get("findings", []):
        if (f["rule"], f["tool"]) not in old_findings:
            out.append(f"new finding [{f['rule']}] on {f['tool']}")
    return out


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())
