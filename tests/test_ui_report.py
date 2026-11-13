"""Proposal 0020: trace and eval reports are one offline HTML page (React, shadcn/ui)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from ouncemcp.cli import main
from ouncemcp.eval.agent import load_suite, run_suite
from ouncemcp.trace import CallTrace
from ouncemcp.trace.report import MAX_CALLS, render_html, trace_data
from ouncemcp.ui import render_report

ROOT = Path(__file__).resolve().parents[1]
EVIL = "</script><img src=x onerror=alert(1)>"


def _traces(n: int = 12) -> list[CallTrace]:
    return [
        CallTrace(
            tool=["inv.search", "inv.get", EVIL][i % 3],
            started_at=1_700_000_000 + i,
            duration_ms=1.5 + i,
            outcome="error" if i % 4 == 0 else "ok",
            result_tokens=100 + i,
            tokenizer="o200k_base",
            budget=2000,
            truncated=i % 5 == 0,
            error_category="not_found" if i % 4 == 0 else None,
            error_code="tool.not_found" if i % 4 == 0 else None,
        )
        for i in range(n)
    ]


def _data(page: str) -> dict:
    raw = re.search(
        r'<script type="application/json" id="report-data">(.*?)</script>', page, re.DOTALL
    )
    assert raw is not None
    return json.loads(raw.group(1))


def test_trace_page_carries_the_data():
    page = render_html(_traces(), title="Trace: x")
    data = _data(page)
    assert data["kind"] == "trace" and data["title"] == "Trace: x"
    assert data["summary"]["calls"] == 12 and data["summary"]["errors"] == 3
    assert {t["tool"] for t in data["tools"]} == {"inv.search", "inv.get", EVIL}
    assert data["calls"][0]["started_at"] == 1_700_000_000


def test_long_traces_list_the_last_calls_but_summarize_all():
    data = trace_data(_traces(MAX_CALLS + 3))
    assert len(data["calls"]) == MAX_CALLS and data["calls_total"] == MAX_CALLS + 3
    assert data["summary"]["calls"] == MAX_CALLS + 3


def test_page_is_offline_and_escapes_report_values():
    page = render_html(_traces(), title=EVIL)
    assert EVIL not in page
    assert page.count("</script>") == 2  # the app script and the data element
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page)
    assert csp is not None
    policy = csp.group(1)
    assert "default-src 'none'" in policy and "connect-src" not in policy
    scripts = re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", page, re.DOTALL)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert f"'sha256-{digest}'" in policy
    assert not re.search(r"""(src|href)=["']https?:""", page)


def test_report_kind_is_required():
    with pytest.raises(ValueError):
        render_report({"title": "x"})


@pytest.mark.anyio
async def test_eval_page_carries_the_report_and_regressions():
    report = await run_suite(load_suite(ROOT / "examples" / "ticketing" / "eval.yaml"), repeats=1)
    data = _data(report.render_html(["all: tokens per task rose"]))
    assert data["kind"] == "eval" and data["title"] == "Eval: ticketing"
    assert data["splits"]["all"]["runs"] and data["tasks"]
    assert data["regressions"] == ["all: tokens per task rose"]


def test_cli_writes_the_page(tmp_path: Path):
    path = tmp_path / "calls.jsonl"
    path.write_text("".join(json.dumps(t.to_dict()) + "\n" for t in _traces()))
    assert main(["trace", str(path)]) == 0
    assert _data((tmp_path / "calls.jsonl.html").read_text())["summary"]["calls"] == 12


def test_build_inputs_are_pinned():
    app = ROOT / "ui" / "report"
    package = json.loads((app / "package.json").read_text())
    for deps in (package["dependencies"], package["devDependencies"]):
        for name, version in deps.items():
            assert version[0].isdigit(), f"{name} is not pinned: {version}"
    lock = json.loads((app / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/react"]["version"] == package["dependencies"]["react"]
