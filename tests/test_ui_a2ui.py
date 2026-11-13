"""Proposal 0020: reports are A2UI v0.9 surfaces rendered by the bundled A2UI renderer."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ouncemcp.cli import main
from ouncemcp.eval.agent import load_suite, run_suite
from ouncemcp.trace import CallTrace
from ouncemcp.trace.report import a2ui_messages, a2ui_surface, render_html
from ouncemcp.ui import a2ui
from ouncemcp.ui.a2ui import Surface, render_page, to_jsonl, validate

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "ouncemcp" / "ui" / "static"
EVIL = "</script><img src=x onerror=alert(1)>"


def _traces() -> list[CallTrace]:
    out = []
    for i in range(12):
        failed = i % 4 == 0
        out.append(
            CallTrace(
                tool=["inv.search", "inv.get", EVIL][i % 3],
                started_at=1_700_000_000 + i,
                duration_ms=1.5 + i,
                outcome="error" if failed else "ok",
                result_tokens=100 + i,
                tokenizer="o200k_base",
                budget=2000,
                truncated=i % 5 == 0,
                truncation={"lists": [{"path": "items", "kept": 10, "total": 40}]}
                if i % 5 == 0
                else None,
                error_category="not_found" if failed else None,
                error_code="tool.not_found" if failed else None,
            )
        )
    return out


def _schema_validator() -> Draft202012Validator:
    load = lambda name: json.loads((STATIC / "schemas" / name).read_text())
    base = "https://a2ui.org/specification/v0_9/"
    catalog = Resource.from_contents(load("basic_catalog.json"))
    registry = Registry().with_resources(
        [
            (base + "common_types.json", Resource.from_contents(load("common_types.json"))),
            (base + "catalog.json", catalog),
            (base + "catalogs/basic/catalog.json", catalog),
        ]
    )
    return Draft202012Validator(load("server_to_client.json"), registry=registry)


@pytest.mark.anyio
async def test_reports_are_valid_a2ui_messages():
    report = await run_suite(load_suite(ROOT / "examples" / "ticketing" / "eval.yaml"), repeats=1)
    validator = _schema_validator()
    for messages in (a2ui_messages(_traces()), report.a2ui_messages()):
        assert validate(messages) == []
        for message in messages:
            errors = [e.message for e in validator.iter_errors(message)]
            assert errors == [], errors
        assert messages[0]["createSurface"]["catalogId"] == a2ui.CATALOG_ID


def test_validate_reports_renderer_problems():
    ui = Surface("s")
    ui.text("a", "x")
    ui.add("a", "Column", children=["missing"])
    ui.add("b", "Table", rows=[])
    ui.add("c", "Card")
    problems = validate(ui.messages())
    assert "duplicate id a" in problems
    assert "no component with id root" in problems
    assert "b: Table is not in the basic catalog" in problems
    assert "c: Card needs child" in problems
    assert "a: refers to missing component missing" in problems


def test_page_is_offline_and_escapes_report_values():
    page = render_html(_traces(), title=EVIL)
    assert EVIL not in page
    before_renderer = page.split("<script>")[0]
    assert "<img" not in before_renderer
    assert before_renderer.count("</script>") == 2  # only the two JSON blocks close
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert "default-src 'none'" in csp and "connect-src" not in csp
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.DOTALL)
    assert len(scripts) == 2
    for script in scripts:
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in csp
    assert not re.search(r"""(src|href)=["']https?:""", page)


def test_trace_views_filter_calls():
    traces = _traces()
    _, views = a2ui_surface(traces)
    assert len(views["all"]["/calls"]) == len(traces)
    assert {r["outcome"] for r in views["errors"]["/calls"]} == {"error"}
    assert {r["tool"] for r in views["tool:inv.get"]["/calls"]} == {"inv.get"}
    assert views["errors"]["/calls_label"] == "Errors: 3 calls"


def test_page_shows_the_text_report_without_scripts():
    page = render_page("t", Surface("s").messages(), fallback="1 calls & more")
    assert "<noscript><pre>1 calls &amp; more</pre></noscript>" in page


def test_cli_writes_a2ui_messages(tmp_path: Path):
    path = tmp_path / "calls.jsonl"
    path.write_text("".join(json.dumps(t.to_dict()) + "\n" for t in _traces()))
    out = tmp_path / "trace.a2ui.jsonl"
    assert main(["trace", str(path), "--no-html", "--a2ui", str(out)]) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [next(k for k in m if k != "version") for m in lines] == [
        "createSurface",
        "updateDataModel",
        "updateComponents",
    ]
    assert to_jsonl(lines) == out.read_text()


def test_bundle_matches_the_pinned_renderer():
    pinned = json.loads((ROOT / "ui" / "a2ui" / "package.json").read_text())["dependencies"]
    assert a2ui.RENDERER == f"@a2ui/lit {pinned['@a2ui/lit']}"
    lock = json.loads((ROOT / "ui" / "a2ui" / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/@a2ui/lit"]["version"] == pinned["@a2ui/lit"]
    bundle = (STATIC / "a2ui-render.js").read_text()
    assert "window.OunceA2UI" in bundle and "</script" not in bundle
