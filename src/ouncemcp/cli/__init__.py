"""Command-line interface for OunceMCP.

Commands are specified in docs/spec.md section 12. Each command reports the
milestone in which it is planned until it is implemented.
"""

from __future__ import annotations

import argparse
import sys

from ouncemcp import __version__

COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("Scaffold a server with tests and CI", "M1"),
    "dev": ("Run with hot reload and a live budget summary", "M1"),
    "check": ("Lint schemas, names, descriptions, budgets, and policy", "M1"),
    "context": ("Show exactly what the model receives for a tool", "M1"),
    "eval": ("Run security and agent task suites", "M3"),
    "bench": ("Run the reproducible benchmark harness", "M3"),
    "trace": ("Open the trace and context viewer", "M3"),
    "audit": ("Optional: measure any MCP server and recommend fixes", "M2"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ouncemcp",
        description="OunceMCP: every token counts. Build lean, fast, safe MCP servers.",
    )
    parser.add_argument("--version", action="version", version=f"ouncemcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, (help_text, _milestone) in COMMANDS.items():
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        cmd.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    _help, milestone = COMMANDS[ns.command]
    print(
        f"ouncemcp {ns.command}: not implemented yet (planned for {milestone}). "
        "See docs/spec.md.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
