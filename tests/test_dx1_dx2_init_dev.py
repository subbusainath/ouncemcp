"""DX-1 (``ouncemcp init``) and DX-2 (``ouncemcp dev``)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from fastmcp import Client

from ouncemcp.cli import dev, main


def test_init_scaffold_passes_its_own_tests(tmp_path: Path, capsys):
    target = tmp_path / "notes-server"
    assert main(["init", str(target)]) == 0
    assert "uv run pytest" in capsys.readouterr().out
    for rel in ("server.py", "tests/test_server.py", "pyproject.toml", ".github/workflows/ci.yml"):
        assert (target / rel).is_file()
    env = {**os.environ, "PYTHONPATH": str(target)}
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "6 passed" in run.stdout


def test_init_has_one_read_and_one_write_tool(tmp_path: Path):
    from ouncemcp.cli.loader import load_server

    target = tmp_path / "svc"
    assert main(["init", str(target)]) == 0
    server = load_server(str(target / "server.py"))
    assert sorted(s.effect.value for s in server.tools.values()) == ["read", "write"]  # type: ignore[union-attr]
    assert main(["check", str(target / "server.py")]) == 0


def test_init_refuses_bad_names_and_non_empty_directories(tmp_path: Path, capsys):
    assert main(["init", str(tmp_path / "1bad")]) == 2
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "x").write_text("")
    assert main(["init", str(taken)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert main(["init", str(taken), "--force"]) == 0


def test_dev_once_prints_the_budget_summary(capsys):
    example = Path(__file__).resolve().parents[1] / "examples" / "inventory" / "server.py"
    assert main(["dev", str(example), "--once"]) == 0
    err = capsys.readouterr().err
    assert "inventory.search" in err and "startup context" in err and "discovery: direct" in err


def test_dev_reports_import_errors_instead_of_crashing(tmp_path: Path, capsys):
    broken = tmp_path / "server.py"
    broken.write_text("raise RuntimeError('boom')\n")
    assert main(["dev", str(broken), "--once"]) == 2
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_snapshot_sees_edits_and_skips_virtualenvs(tmp_path: Path):
    (tmp_path / "server.py").write_text("x = 1\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("")
    before = dev.snapshot(tmp_path)
    assert list(before) == [str(tmp_path / "server.py")]
    time.sleep(0.01)
    (tmp_path / "server.py").write_text("x = 2\n")
    os.utime(tmp_path / "server.py", ns=(time.time_ns(), time.time_ns() + 10**9))
    assert dev.changed(before, dev.snapshot(tmp_path)) == [str(tmp_path / "server.py")]


SERVER = '''
from pydantic import BaseModel
from ouncemcp import Effect, OunceServer

server = OunceServer("live")

class Out(BaseModel):
    value: str

@server.tool(name="{name}", effect=Effect.READ)
async def tool() -> Out:
    """Return a value."""
    return Out(value="{name}")
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(lines: list[str], needle: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in line for line in lines):
            return
        time.sleep(0.1)
    raise AssertionError(f"{needle!r} not seen in:\n" + "".join(lines))


@pytest.mark.anyio
async def test_dev_hot_reloads_and_prints_live_call_lines(tmp_path: Path):
    path = tmp_path / "server.py"
    path.write_text(SERVER.format(name="first.tool"))
    port = free_port()
    proc = subprocess.Popen(  # noqa: ASYNC220 - the dev command is the thing under test
        [sys.executable, "-m", "ouncemcp", "dev", str(path), "--port", str(port)],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    lines: list[str] = []

    def pump() -> None:
        assert proc.stderr is not None
        lines.extend(proc.stderr)

    import threading

    threading.Thread(target=pump, daemon=True).start()
    try:
        wait_for(lines, "serving over http")
        await anyio.sleep(1.0)
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            result = await client.call_tool("first.tool", {})
        assert result.structured_content == {"value": "first.tool"}
        wait_for(lines, "call first.tool ok")
        path.write_text(SERVER.format(name="second.tool"))
        wait_for(lines, "restarting")
        wait_for(lines, "second.tool")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
