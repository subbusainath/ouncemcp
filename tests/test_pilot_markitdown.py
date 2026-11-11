"""Pilot 4: migrating markitdown-mcp (spec 15.1, DX-7). Before and after measurements."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from ouncemcp.cli import main
from ouncemcp.eval import SSRFProbe, run_security_suite

PILOT = Path(__file__).resolve().parents[1] / "examples" / "markitdown"


@dataclass
class Doc:
    markdown: str
    title: str | None = "Annual report"


class FakeConverter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.fetched: list[str] = []

    def convert_uri(self, uri: str) -> Doc:
        self.fetched.append(uri)
        return Doc(self.text)


BIG = "\n\n".join(f"## Section {i}\n\n" + "Revenue grew in every region. " * 40 for i in range(60))


def load(name: str) -> Any:
    """Load a pilot file under a unique module name. ``server`` is also the inventory
    example's module name, so importing it by name would replace that module."""
    spec = importlib.util.spec_from_file_location(f"markitdown_pilot_{name}", PILOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pilot() -> Any:
    sys.path.insert(0, str(PILOT))
    try:
        yield {name: load(name) for name in ("original", "server")}
    finally:
        sys.path.remove(str(PILOT))


async def call(server: Any, uri: str) -> Any:
    target = getattr(server, "fastmcp", server)
    async with Client(target) as client:
        return await client.call_tool("convert_to_markdown", {"uri": uri}, raise_on_error=False)


@pytest.mark.anyio
async def test_results_go_from_the_whole_document_to_a_preview_and_handle(pilot):
    from ouncemcp import OunceServer
    from ouncemcp.core.tokens import get_counter

    counter = get_counter()
    before = await call(pilot["original"].build(FakeConverter(BIG)), "https://93.184.216.34/r.pdf")
    before_tokens = counter.count(before.content[0].text)
    after_server: OunceServer = pilot["server"].build(FakeConverter(BIG))
    after = await call(after_server, "https://93.184.216.34/r.pdf")
    after_tokens = after.meta["ouncemcp"]["result_tokens"]
    data = after.structured_content
    assert before_tokens > 10_000
    assert after_tokens < 600
    assert data["characters"] == len(BIG) and data["handle"]
    page = await after_server.call_tool("handles.read", {"handle": data["handle"], "limit": 2})
    assert page.structured_content["total"] > 10
    assert page.meta["ouncemcp"]["result_tokens"] <= page.meta["ouncemcp"]["budget"]


@pytest.mark.anyio
async def test_ssrf_probes_fail_before_and_pass_after(pilot):
    from ouncemcp import OunceServer

    targets = ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:22/", "http://[::1]/"]
    original = FakeConverter("x")
    wrapped = OunceServer.wrap(pilot["original"].build(original))
    before = await run_security_suite(
        wrapped, [SSRFProbe("convert_to_markdown", "uri", targets=targets)]
    )
    migrated = FakeConverter("x")
    after = await run_security_suite(
        pilot["server"].build(migrated), [SSRFProbe("convert_to_markdown", "uri")]
    )
    assert not before.passed and len(original.fetched) == 3
    assert after.passed, after.render_text()
    assert migrated.fetched == []


@pytest.mark.anyio
async def test_file_uris_need_opt_in_and_stay_under_the_root(pilot, tmp_path, monkeypatch):
    doc = tmp_path / "notes.md"
    doc.write_text("hello")
    server = pilot["server"].build(FakeConverter("hello"))
    refused = await call(server, doc.as_uri())
    assert refused.is_error and "disabled" in refused.content[0].text
    monkeypatch.setenv("MARKITDOWN_ALLOW_FILES", "1")
    monkeypatch.setenv("MARKITDOWN_ROOT", str(tmp_path))
    assert not (await call(server, doc.as_uri())).is_error
    outside = await call(server, "file:///etc/passwd")
    assert outside.is_error and "outside" in outside.content[0].text


def test_each_step_passes_check(capsys):
    for name in ("step1.py", "server.py"):
        assert main(["check", str(PILOT / name)]) == 0
    assert "convert_to_markdown" in capsys.readouterr().out
