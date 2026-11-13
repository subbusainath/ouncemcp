"""Command-line interface for OunceMCP (spec section 12).

Implemented: ``init`` (scaffold), ``dev`` (hot reload and live budget summary),
``check`` (budgets and lints) and ``context`` (definition view). Other commands report
the milestone in which they are planned.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ouncemcp import __version__
from ouncemcp.core.tokens import DEFAULT_ENCODING, TokenCounter, get_counter

COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("Scaffold a server with tests and CI", "M1"),
    "dev": ("Run with hot reload and a live budget summary", "M1"),
    "check": ("Check tool definition sizes against the budget", "M0"),
    "context": ("Show exactly what the model receives for a tool", "M0"),
    "eval": ("Run an agent task suite and report success, tools, tokens", "M3"),
    "bench": ("Run the reproducible benchmark harness", "M3"),
    "trace": ("Open the trace and context viewer", "M3"),
    "audit": ("Optional: measure any MCP server and recommend fixes", "M2"),
}
IMPLEMENTED = {"init", "dev", "check", "context", "audit", "trace", "eval"}


def _add_tokenizer_option(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument(
        "--tokenizer",
        metavar="ENCODING",
        help=(
            "tiktoken encoding to count with (for example cl100k_base). Default: the "
            f"server's own tokenizer, else {DEFAULT_ENCODING}"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ouncemcp",
        description="OunceMCP: every token counts. Build lean, fast, safe MCP servers.",
    )
    parser.add_argument("--version", action="version", version=f"ouncemcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, (help_text, _milestone) in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        if name == "check":
            cmd.add_argument("server", help="path/to/server.py[:name] or module[:name]")
            cmd.add_argument("--json", action="store_true", help="print a JSON report")
            _add_tokenizer_option(cmd)
        elif name == "init":
            cmd.add_argument("directory", help="directory to create, e.g. notes-server")
            cmd.add_argument(
                "--force", action="store_true", help="write into a non-empty directory"
            )
        elif name == "dev":
            cmd.add_argument("server", help="path/to/server.py[:name] or module[:name]")
            cmd.add_argument(
                "--transport",
                choices=["http", "streamable-http", "sse", "stdio"],
                default="http",
                help="default http; a restart drops stdio sessions",
            )
            cmd.add_argument("--host", default="127.0.0.1")
            cmd.add_argument("--port", type=int, default=8000)
            cmd.add_argument(
                "--once", action="store_true", help="print the budget summary once and exit"
            )
        elif name == "audit":
            cmd.add_argument(
                "target",
                nargs="+",
                help="an http(s) URL, a server file (path/to/server.py[:name]), or a command "
                "that starts a stdio server (put it after --)",
            )
            cmd.add_argument("--json", action="store_true", help="print a JSON report")
            cmd.add_argument("--markdown", action="store_true", help="print a Markdown report")
            cmd.add_argument("--output", metavar="PATH", help="also write the JSON report here")
            _add_tokenizer_option(cmd)
            cmd.add_argument("--header", action="append", default=[], metavar="'Name: value'")
            cmd.add_argument("--bearer", metavar="TOKEN", help="bearer token for HTTP servers")
            cmd.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
            cmd.add_argument(
                "--inherit-env", action="store_true", help="pass this environment to stdio servers"
            )
            cmd.add_argument(
                "--sample",
                action="append",
                default=[],
                metavar="TOOL=JSON",
                help="call TOOL with these arguments to measure result size; tools are "
                "never called without this flag",
            )
            cmd.add_argument("--sample-runs", type=int, default=3)
            cmd.add_argument("--fail-startup", type=int, metavar="TOKENS")
            cmd.add_argument("--fail-tool", type=int, metavar="TOKENS")
            cmd.add_argument("--compare", metavar="OLD.json", help="report regressions")
            cmd.add_argument("--fail-on-regression", action="store_true")
        elif name == "context":
            cmd.add_argument("server", help="path/to/server.py[:name] or module[:name]")
            cmd.add_argument("tool", help="tool name")
            cmd.add_argument(
                "--call",
                metavar="JSON",
                help="also call the tool in-process with these arguments and show the result "
                "exactly as the model receives it",
            )
            _add_tokenizer_option(cmd)
        elif name == "eval":
            cmd.add_argument("suite", help="a task suite in YAML")
            cmd.add_argument(
                "--model",
                default="replay",
                choices=["replay"],
                help="replay plays each task's script (no API key, deterministic)",
            )
            cmd.add_argument("--repeats", type=int, help="runs per task (default: the suite's)")
            cmd.add_argument("--split", choices=["all", "train", "heldout"], default="all")
            cmd.add_argument("--server", help="run against this server instead of the suite's")
            _add_tokenizer_option(cmd)
            cmd.add_argument("--json", action="store_true", help="print the report as JSON")
            cmd.add_argument("--output", metavar="PATH", help="also write the JSON report here")
            cmd.add_argument("--html", metavar="PATH", help="also write an HTML report here")
            cmd.add_argument(
                "--a2ui", metavar="PATH", help="also write the report as A2UI messages (JSON lines)"
            )
            cmd.add_argument("--compare", metavar="OLD.json", help="list regressions against it")
            cmd.add_argument("--fail-on-regression", action="store_true")
            cmd.add_argument(
                "--allow-failures", action="store_true", help="exit 0 even if a task fails"
            )
        elif name == "trace":
            cmd.add_argument("file", help="a JSONL trace file written by JsonlSink")
            cmd.add_argument(
                "--html", metavar="PATH", help="where to write the HTML report (default: FILE.html)"
            )
            cmd.add_argument("--no-html", action="store_true", help="print the summary only")
            cmd.add_argument(
                "--a2ui", metavar="PATH", help="also write the report as A2UI messages (JSON lines)"
            )
            cmd.add_argument("--json", action="store_true", help="print the summary as JSON")
        else:
            cmd.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


async def _definitions(server: Any) -> tuple[list[Any], Any, TokenCounter]:
    from ouncemcp.core.budget import Budget
    from ouncemcp.core.server import OunceServer
    from ouncemcp.core.wire import wire_tools

    if isinstance(server, OunceServer):
        return await server.catalog(), server.budget, server.counter
    return await wire_tools(server), Budget(), get_counter()


def _counter(ns: argparse.Namespace, default: TokenCounter) -> TokenCounter:
    return get_counter(ns.tokenizer) if ns.tokenizer else default


def _discovery(server: Any) -> Any:
    from ouncemcp.core.server import OunceServer

    if isinstance(server, OunceServer):
        return asyncio.run(server.discovery())
    return None


def _check(ns: argparse.Namespace) -> int:
    from ouncemcp.cli.loader import LoadError, load_server
    from ouncemcp.core.costs import measure_tools
    from ouncemcp.core.lints import lint_server
    from ouncemcp.core.server import OunceServer

    try:
        server = load_server(ns.server)
    except LoadError as exc:
        print(f"ouncemcp check: {exc}", file=sys.stderr)
        return 2
    tools, budget, default = asyncio.run(_definitions(server))
    try:
        counter = _counter(ns, default)
    except ValueError as exc:
        print(f"ouncemcp check: {exc}", file=sys.stderr)
        return 2
    modes = (
        {name: spec.output_schema for name, spec in server.tools.items()}
        if isinstance(server, OunceServer)
        else None
    )
    report = measure_tools(tools, budget, counter, output_schema_modes=modes)
    decision = _discovery(server)
    findings = lint_server(server)
    lint_failed = any(f.severity == "error" for f in findings)
    if ns.json:
        data = report.to_dict()
        if decision is not None:
            data["discovery"] = decision.to_dict()
        data["lints"] = [f.to_dict() for f in findings]
        print(json.dumps(data, indent=2))
    else:
        print(report.render_text())
        if decision is not None:
            print(decision.render_text())
        if findings:
            print("Lints:")
            for finding in findings:
                print(f"  {finding.render_text()}")
    return 1 if report.failed or lint_failed else 0


def _context(ns: argparse.Namespace) -> int:
    from ouncemcp.cli.loader import LoadError, load_server
    from ouncemcp.core.costs import (
        COUNTED_FIELDS,
        UNCOUNTED_FIELDS,
        definition_payload,
        measure_tool,
    )

    try:
        server = load_server(ns.server)
    except LoadError as exc:
        print(f"ouncemcp context: {exc}", file=sys.stderr)
        return 2
    tools, budget, default = asyncio.run(_definitions(server))
    try:
        counter = _counter(ns, default)
    except ValueError as exc:
        print(f"ouncemcp context: {exc}", file=sys.stderr)
        return 2
    match = next((t for t in tools if t.name == ns.tool), None)
    if match is None:
        names = ", ".join(sorted(t.name for t in tools))
        print(f"ouncemcp context: no tool named '{ns.tool}'. Tools: {names}", file=sys.stderr)
        return 2
    cost = measure_tool(match, budget, counter)
    print(json.dumps(definition_payload(match), indent=2, ensure_ascii=False))
    print(
        f"\n{cost.total} tokens (description {cost.description}, input schema "
        f"{cost.input_schema}, output schema {cost.output_schema}, other "
        f"{cost.name_tokens + cost.structure}) [{cost.status.upper()}] "
        f"counted with {counter.name}\n"
        f"Counted: {', '.join(COUNTED_FIELDS)}; not counted: {', '.join(UNCOUNTED_FIELDS)}",
        file=sys.stderr,
    )
    if ns.call is not None:
        return _context_call(server, ns.tool, ns.call, counter)
    return 0


def _context_call(server: Any, tool: str, raw: str, counter: TokenCounter) -> int:
    """Call ``tool`` in-process and show the result as the model receives it (DX-4)."""
    from fastmcp import Client

    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(f"ouncemcp context: --call is not valid JSON ({exc})", file=sys.stderr)
        return 2

    async def run() -> Any:
        async with Client(getattr(server, "fastmcp", server)) as client:
            return await client.call_tool_mcp(tool, arguments)

    result = asyncio.run(run())
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    print("\n--- result content (what the model reads) ---")
    print(text)
    if result.structured_content is not None:
        print("--- structuredContent ---")
        print(json.dumps(result.structured_content, indent=2, ensure_ascii=False))
    meta = (result.meta or {}).get("ouncemcp")
    tokens = counter.count(text)
    status = "error" if result.is_error else "ok"
    print(
        f"\nresult: {status}, {tokens} tokens of content counted with {counter.name}"
        + (f"; _meta.ouncemcp = {json.dumps(meta)}" if meta else ""),
        file=sys.stderr,
    )
    return 1 if result.is_error else 0


def _audit(ns: argparse.Namespace) -> int:
    import shlex

    from ouncemcp.audit.runner import audit, compare, load_report
    from ouncemcp.core.budget import Budget

    target = ns.target[0] if len(ns.target) == 1 else shlex.join(ns.target)
    try:
        headers = dict(h.split(":", 1) for h in ns.header)
        env = dict(e.split("=", 1) for e in ns.env)
        samples = {}
        for item in ns.sample:
            tool, _, raw = item.partition("=")
            samples[tool] = json.loads(raw or "{}")
    except ValueError as exc:
        print(f"ouncemcp audit: bad option ({exc})", file=sys.stderr)
        return 2
    budget = Budget(tool_definition_fail=ns.fail_tool) if ns.fail_tool else Budget()
    try:
        report = asyncio.run(
            audit(
                target,
                budget=budget,
                tokenizer=ns.tokenizer,
                samples=samples,
                sample_runs=ns.sample_runs,
                headers={k.strip(): v.strip() for k, v in headers.items()},
                bearer=ns.bearer,
                env=env,
                inherit_env=ns.inherit_env,
            )
        )
    except Exception as exc:  # noqa: BLE001 - report connection problems plainly
        print(
            f"ouncemcp audit: could not audit {target}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    data = report.to_dict()
    regressions = compare(load_report(ns.compare), data) if ns.compare else []
    data["regressions"] = regressions
    if ns.output:
        Path(ns.output).write_text(json.dumps(data, indent=2))
    if ns.json:
        print(json.dumps(data, indent=2))
    elif ns.markdown:
        print(report.render_markdown())
    else:
        print(report.render_text())
        for line in regressions:
            print(f"REGRESSION {line}")
    failed = bool(report.cost.failed)
    if ns.fail_startup is not None and report.startup_tokens > ns.fail_startup:
        print(
            f"ouncemcp audit: startup context {report.startup_tokens:,} tokens is over "
            f"{ns.fail_startup:,}",
            file=sys.stderr,
        )
        failed = True
    if ns.fail_on_regression and regressions:
        failed = True
    return 1 if failed else 0


def _eval(ns: argparse.Namespace) -> int:
    from ouncemcp.eval.agent import SuiteError, load_suite, run_suite
    from ouncemcp.eval.agent.report import compare, load_report

    try:
        suite = load_suite(ns.suite)
    except SuiteError as exc:
        print(f"ouncemcp eval: {exc}", file=sys.stderr)
        return 2
    report = asyncio.run(
        run_suite(
            suite, repeats=ns.repeats, split=ns.split, server=ns.server, tokenizer=ns.tokenizer
        )
    )
    data = report.to_dict()
    regressions = compare(load_report(ns.compare), data) if ns.compare else []
    data["regressions"] = regressions
    if ns.output:
        Path(ns.output).write_text(json.dumps(data, indent=2, default=str))
    if ns.html:
        Path(ns.html).write_text(report.render_html(), encoding="utf-8")
    if ns.a2ui:
        from ouncemcp.ui.a2ui import to_jsonl

        Path(ns.a2ui).write_text(to_jsonl(report.a2ui_messages()), encoding="utf-8")
    if ns.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(report.render_text())
        for line in regressions:
            print(f"REGRESSION {line}")
    failed = (not report.passed and not ns.allow_failures) or (
        ns.fail_on_regression and bool(regressions)
    )
    return 1 if failed else 0


def _trace(ns: argparse.Namespace) -> int:
    from ouncemcp.trace.report import (
        a2ui_messages,
        load_traces,
        render_html,
        render_text,
        summarize,
    )
    from ouncemcp.ui.a2ui import to_jsonl

    path = Path(ns.file)
    if not path.is_file():
        print(f"ouncemcp trace: no such file: {path}", file=sys.stderr)
        return 2
    traces = load_traces(path)
    if not traces:
        print(f"ouncemcp trace: no traces in {path}", file=sys.stderr)
        return 2
    if ns.json:
        print(json.dumps(summarize(traces), indent=2))
    else:
        print(render_text(traces))
    if not ns.no_html:
        out = Path(ns.html) if ns.html else path.with_suffix(path.suffix + ".html")
        out.write_text(render_html(traces, title=f"Trace: {path.name}"), encoding="utf-8")
        print(f"\nHTML report: {out}", file=sys.stderr)
    if ns.a2ui:
        title = f"Trace: {path.name}"
        Path(ns.a2ui).write_text(to_jsonl(a2ui_messages(traces, title=title)), encoding="utf-8")
    return 0


def _init(ns: argparse.Namespace) -> int:
    from ouncemcp.cli.scaffold import ScaffoldError, scaffold

    try:
        written = scaffold(ns.directory, force=ns.force)
    except ScaffoldError as exc:
        print(f"ouncemcp init: {exc}", file=sys.stderr)
        return 2
    print(f"Created {ns.directory} ({len(written)} files). Next:")
    print(f"  cd {ns.directory}")
    print('  uv venv && uv pip install -e ".[dev]"')
    print("  uv run pytest")
    print("  uv run ouncemcp dev server.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    if ns.command == "check":
        return _check(ns)
    if ns.command == "context":
        return _context(ns)
    if ns.command == "init":
        return _init(ns)
    if ns.command == "audit":
        return _audit(ns)
    if ns.command == "trace":
        return _trace(ns)
    if ns.command == "eval":
        return _eval(ns)
    if ns.command == "dev":
        from ouncemcp.cli import dev

        return dev.run(ns.server, transport=ns.transport, host=ns.host, port=ns.port, once=ns.once)
    _help, milestone = COMMANDS[ns.command]
    print(
        f"ouncemcp {ns.command}: not implemented yet (planned for {milestone}). See docs/spec.md.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
