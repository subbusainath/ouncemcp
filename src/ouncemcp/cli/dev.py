"""``ouncemcp dev``: run a server with hot reload and a live budget summary (spec DX-2).

The command runs the server in a child process and watches the server's directory for
changes to ``.py`` and ``.toml`` files. On a change it restarts the child, which prints
the budget summary again: tokens per tool definition, the startup total and the
discovery mode. While it runs, every tool call prints one line with the tokens its
result used against its budget. If the server fails to import, the error is shown and
the command waits for the next change instead of exiting.

Output goes to stderr, so ``--transport stdio`` keeps stdout for the protocol. HTTP is
the default because a restart drops stdio sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
WATCH_SUFFIXES = {".py", ".toml"}
MAX_WATCHED = 5000


def watch_root(target: str) -> Path:
    ref = target.partition(":")[0]
    path = Path(ref)
    if ref.endswith(".py") or path.exists():
        return path.resolve().parent
    return Path.cwd()


def snapshot(root: Path) -> dict[str, int]:
    """Modification times of the files a change to which should restart the server."""
    found: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix in WATCH_SUFFIXES:
                full = os.path.join(dirpath, name)
                try:
                    found[full] = os.stat(full).st_mtime_ns
                except OSError:
                    continue
                if len(found) >= MAX_WATCHED:
                    return found
    return found


def changed(before: dict[str, int], after: dict[str, int]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


# ------------------------------------------------------------------ summary


def budget_summary(server: Any) -> str:
    """The startup budget summary ``dev`` prints after every (re)load."""
    from ouncemcp.core.costs import measure_tools
    from ouncemcp.core.lints import lint_server
    from ouncemcp.core.server import OunceServer

    if isinstance(server, OunceServer):
        report = asyncio.run(server.cost_report())
        decision = asyncio.run(server.discovery())
        catalog = measure_tools(
            asyncio.run(server.catalog()),
            server.budget,
            server.counter,
            {n: s.output_schema for n, s in server.tools.items()},
        )
        tools, startup = catalog.tools, report.total
        mode = f"{decision.mode} ({decision.reason})"
        findings = lint_server(server)
        budget = server.budget
    else:
        from ouncemcp.core.budget import Budget
        from ouncemcp.core.tokens import get_counter
        from ouncemcp.core.wire import wire_tools

        budget = Budget()
        catalog = measure_tools(asyncio.run(wire_tools(server)), budget, get_counter())
        tools, startup, mode, findings = catalog.tools, catalog.total, "direct (FastMCP)", []
    width = max((len(t.name) for t in tools), default=4)
    lines = [f"[ouncemcp] {getattr(server, 'name', 'server')}: {len(tools)} tools"]
    for tool in tools:
        flag = "" if tool.status == "ok" else f"  {tool.status.upper()}"
        lines.append(f"  {tool.name:<{width}}  {tool.total:>6,} tokens{flag}")
    lines.append(
        f"  startup context: {startup:,} tokens (warn at {budget.startup_tokens:,}); "
        f"discovery: {mode}; counted with {catalog.tokenizer}"
    )
    for finding in findings:
        lines.append(f"  {finding.render_text()}")
    return "\n".join(lines)


def call_line(trace: Any) -> str:
    """One live line per tool call."""
    budget = f"/{trace.budget:,}" if trace.budget else ""
    extra = []
    if trace.truncated:
        extra.append("truncated")
    if trace.attempts > 1:
        extra.append(f"{trace.attempts} attempts")
    if trace.error_code:
        extra.append(f"{trace.error_category}:{trace.error_code}")
    tail = f" ({', '.join(extra)})" if extra else ""
    return (
        f"[ouncemcp] call {trace.tool} {trace.outcome} {trace.result_tokens:,}{budget} tokens "
        f"{trace.duration_ms:.1f} ms{tail}"
    )


# ------------------------------------------------------------------ child


def child(target: str, *, transport: str, host: str, port: int, once: bool) -> int:
    from ouncemcp.cli.loader import LoadError, load_server
    from ouncemcp.core.server import OunceServer
    from ouncemcp.trace import CallbackSink

    try:
        server = load_server(target)
        summary = budget_summary(server)
    except LoadError as exc:
        print(f"[ouncemcp] dev: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - show any import error to the developer
        print(
            f"[ouncemcp] dev: could not load {target}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(summary, file=sys.stderr, flush=True)
    if once:
        return 0
    if isinstance(server, OunceServer):
        server.add_trace_sink(
            CallbackSink(lambda t: print(call_line(t), file=sys.stderr, flush=True))
        )
        kwargs: dict[str, Any] = {"check_budget": False}
        if transport != "stdio":
            kwargs.update(host=host, port=port, show_banner=False)
        print(
            f"[ouncemcp] dev: serving over {transport}"
            + (f" at http://{host}:{port}/mcp" if transport != "stdio" else ""),
            file=sys.stderr,
            flush=True,
        )
        server.run(transport, **kwargs)  # type: ignore[arg-type]
    else:
        server.run(transport=transport)  # type: ignore[arg-type]
    return 0


# ------------------------------------------------------------------ parent


def run(
    target: str,
    *,
    transport: str = "http",
    host: str = "127.0.0.1",
    port: int = 8000,
    interval: float = 0.5,
    once: bool = False,
    out: TextIO | None = None,
    stop: Callable[[], bool] = lambda: False,
) -> int:
    """Run ``target`` and restart it on every change until interrupted (or ``stop()``)."""
    if once:
        return child(target, transport=transport, host=host, port=port, once=True)
    stream = out or sys.stderr
    root = watch_root(target)
    command = [
        sys.executable,
        "-m",
        "ouncemcp.cli.dev",
        target,
        "--transport",
        transport,
        "--host",
        host,
        "--port",
        str(port),
    ]
    print(f"[ouncemcp] dev: watching {root} (Ctrl+C to stop)", file=stream, flush=True)
    proc: subprocess.Popen[bytes] | None = None
    try:
        while not stop():
            state = snapshot(root)
            proc = subprocess.Popen(command)
            reported_exit = False
            while not stop():
                time.sleep(interval)
                now = snapshot(root)
                diff = changed(state, now)
                if diff:
                    shown = ", ".join(os.path.relpath(p, root) for p in diff[:3])
                    print(f"[ouncemcp] dev: changed {shown}; restarting", file=stream, flush=True)
                    break
                if proc.poll() is not None and not reported_exit:
                    reported_exit = True
                    print(
                        f"[ouncemcp] dev: server exited ({proc.returncode}); waiting for changes",
                        file=stream,
                        flush=True,
                    )
            _stop(proc)
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None:
            _stop(proc)
    return 0


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ouncemcp.cli.dev")
    parser.add_argument("server")
    parser.add_argument("--transport", default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    ns = parser.parse_args(argv)
    return child(ns.server, transport=ns.transport, host=ns.host, port=ns.port, once=False)


if __name__ == "__main__":
    raise SystemExit(main())
