"""BUD-4: per-tool budget overrides carry a recorded justification."""

from __future__ import annotations

import json
import textwrap
import warnings
from pathlib import Path

import pytest
from pydantic import BaseModel

from ouncemcp import Budget, Effect, OunceServer
from ouncemcp.cli import main
from ouncemcp.core.budget import BudgetOverride, BudgetOverrideWarning
from ouncemcp.core.lints import lint_server

LONG = " ".join(["Returns the full audit log for one account."] * 60)


class Out(BaseModel):
    text: str


def _server(**tool_kwargs) -> OunceServer:
    server = OunceServer("s", budget=Budget(tool_definition_warn=300, tool_definition_fail=400))

    @server.tool(name="logs.read", effect=Effect.READ, description=LONG, **tool_kwargs)
    def read() -> Out:
        return Out(text="x")

    return server


def test_raising_a_budget_without_a_reason_warns():
    with pytest.warns(BudgetOverrideWarning, match="logs.read"):
        _server(result_tokens=6_000)
    with pytest.warns(BudgetOverrideWarning, match="definition_tokens"):
        _server(definition_tokens=900)


def test_a_reason_silences_the_warning_and_is_recorded():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        server = _server(result_tokens=6_000, budget_reason="Audit logs are read whole by design.")
        _server(result_tokens=500)  # lowering a budget needs no reason
    override = server.budget_overrides()["logs.read"]
    assert override == BudgetOverride(
        result_tokens=6_000, reason="Audit logs are read whole by design."
    )


def test_definition_override_moves_the_fail_threshold():
    plain = _server()
    raised = _server(definition_tokens=900, budget_reason="One tool replaces six.")
    import anyio

    plain_cost = anyio.run(plain.cost_report)
    raised_cost = anyio.run(raised.cost_report)
    assert plain_cost.tools[0].status == "fail" and plain_cost.failed
    tool = raised_cost.tools[0]
    assert tool.status == "ok" and not raised_cost.failed
    assert tool.override == {
        "definition_tokens": 900,
        "result_tokens": None,
        "reason": "One tool replaces six.",
    }
    assert "One tool replaces six." in raised_cost.render_text()


def test_override_values_are_checked():
    with pytest.raises(ValueError, match="definition_tokens"):
        _server(definition_tokens=0, budget_reason="x")
    with pytest.raises(ValueError, match="budget_reason"):
        _server(result_tokens=6_000, budget_reason="  ")


def test_check_lints_and_reports_overrides(tmp_path: Path, capsys):
    with pytest.warns(BudgetOverrideWarning):
        findings = lint_server(_server(result_tokens=6_000))
    assert [f.rule for f in findings if f.rule.startswith("budget")] == [
        "budget-override-without-reason"
    ]
    path = tmp_path / "srv.py"
    path.write_text(
        textwrap.dedent(
            f"""
            from pydantic import BaseModel
            from ouncemcp import Budget, Effect, OunceServer

            class Out(BaseModel):
                text: str

            server = OunceServer("s", budget=Budget(tool_definition_warn=300, tool_definition_fail=400))

            @server.tool(name="logs.read", effect=Effect.READ, description={LONG!r},
                         definition_tokens=900, budget_reason="One tool replaces six.")
            def read() -> Out:
                return Out(text="x")
            """
        )
    )
    assert main(["check", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tools"][0]["override"]["reason"] == "One tool replaces six."
