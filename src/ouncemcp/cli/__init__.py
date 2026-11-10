"""Command-line interface for OunceMCP (spec section 12).

Implemented in M0: ``check`` (budget checks) and ``context`` (definition view).
Other commands report the milestone in which they are planned.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from ouncemcp import __version__
from ouncemcp.core.tokens import DEFAULT_ENCODING, TokenCounter, get_counter

COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("Scaffold a server with tests and CI", "M1"),
    "dev": ("Run with hot reload and a live budget summary", "M1"),
    "check": ("Check tool definition sizes against the budget", "M0"),
    "context": ("Show exactly what the model receives for a tool", "M0"),
    "eval": ("Run security and agent task suites", "M3"),
    "bench": ("Run the reproducible benchmark harness", "M3"),
    "trace": ("Open the trace and context viewer", "M3"),
    "audit": ("Optional: measure any MCP server and recommend fixes", "M2"),
}
IMPLEMENTED = {"check", "context"}


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
        elif name == "context":
            cmd.add_argument("server", help="path/to/server.py[:name] or module[:name]")
            cmd.add_argument("tool", help="tool name")
            _add_tokenizer_option(cmd)
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
    if ns.json:
        data = report.to_dict()
        if decision is not None:
            data["discovery"] = decision.to_dict()
        print(json.dumps(data, indent=2))
    else:
        print(report.render_text())
        if decision is not None:
            print(decision.render_text())
    return 1 if report.failed else 0


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
    _help, milestone = COMMANDS[ns.command]
    print(
        f"ouncemcp {ns.command}: not implemented yet (planned for {milestone}). See docs/spec.md.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
