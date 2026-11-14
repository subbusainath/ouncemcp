"""DX-3: schema, list and naming lints in ouncemcp check."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field

from ouncemcp import Cursor, Effect, OunceServer, Paginated
from ouncemcp.core.lints import lint_server

NEW = {"undocumented-parameter", "free-form-input", "unpaginated-list", "inconsistent-tool-names"}


def rules(server: OunceServer) -> list[tuple[str, str, str]]:
    return [(f.rule, f.tool, f.message) for f in lint_server(server) if f.rule in NEW]


class Row(BaseModel):
    id: str


class Rows(BaseModel):
    rows: list[Row]


class Page(BaseModel):
    rows: Annotated[list[Row], Paginated]
    next_cursor: Cursor = None


class Top(BaseModel):
    rows: list[Row] = Field(max_length=5)
    names: list[str]


class Filters(BaseModel):
    labels: dict[str, Any]
    extra: Any = Field(None, description="Anything")


def test_parameters_need_descriptions_unless_their_type_explains_them():
    server = OunceServer("s")

    @server.tool(name="a.get", effect=Effect.READ, description="Get one row by id from a.search.")
    def get(
        id: str,
        kind: Literal["x", "y"] = "x",
        verbose: bool = False,
        limit: int = Field(10, ge=1, le=50),
        offset: int = 0,
        note: str = Field("", description="Free text"),
    ) -> Top:
        return Top(rows=[], names=[])

    assert rules(server) == [
        (
            "undocumented-parameter",
            "a.get",
            "id, offset have no description; say what the value is and where it comes from",
        )
    ]


def test_free_form_inputs_are_flagged():
    server = OunceServer("s")

    @server.tool(name="a.find", effect=Effect.READ, description="Find rows matching filters.")
    def find(filters: Filters) -> Top:
        return Top(rows=[], names=[])

    found = rules(server)
    assert [r[0] for r in found] == ["free-form-input"]
    assert found[0][2].startswith("labels, extra accept any structure")


def test_lists_of_records_need_a_cursor_or_a_bound():
    server = OunceServer("s")

    @server.tool(name="a.all", effect=Effect.READ, description="List every row in the table.")
    def all_rows() -> Rows:
        return Rows(rows=[])

    @server.tool(name="a.page", effect=Effect.READ, description="List rows a page at a time.")
    def page() -> Page:
        return Page(rows=[])

    @server.tool(name="a.top", effect=Effect.READ, description="List the five largest rows.")
    def top() -> Top:
        return Top(rows=[], names=[])

    assert [(r[0], r[1]) for r in rules(server)] == [("unpaginated-list", "a.all")]


def test_tool_names_use_one_style():
    server = OunceServer("s", handles=True)
    for name in ("tickets.search", "tickets.close", "create_ticket"):

        @server.tool(name=name, effect=Effect.READ, description="Search the open tickets by text.")
        def tool() -> Top:
            return Top(rows=[], names=[])

    found = rules(server)
    assert [(r[0], r[1]) for r in found] == [("inconsistent-tool-names", "(server)")]
    assert "snake_case: create_ticket" in found[0][2]
    assert "handles.read" not in found[0][2]  # framework tools do not count


def test_framework_tools_are_documented():
    server = OunceServer("s", handles=True)
    assert rules(server) == []


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    [
        "examples/inventory/server.py",
        "examples/inventory/workflow.py",
        "examples/ticketing/server.py",
        "examples/report/server.py",
        "examples/markitdown/server.py",
    ],
)
def test_examples_have_no_lint_findings(path):
    from ouncemcp.cli.loader import load_server

    findings = lint_server(load_server(str(ROOT / path)))
    assert [f.render_text() for f in findings] == []


def test_scaffold_has_no_lint_findings(tmp_path):
    from ouncemcp.cli import main
    from ouncemcp.cli.loader import load_server

    assert main(["init", str(tmp_path / "demo")]) == 0
    assert [
        f.render_text() for f in lint_server(load_server(str(tmp_path / "demo" / "server.py")))
    ] == []
