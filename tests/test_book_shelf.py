"""The Shelf project from the OunceMCP book: every stage runs, passes check and does
what its chapter says. The book includes these files, so this keeps the book true."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from ouncemcp.cli import main
from ouncemcp.cli.loader import load_server
from ouncemcp.core.lints import lint_server
from ouncemcp.testing import as_caller

SHELF = Path(__file__).resolve().parents[1] / "examples" / "book" / "shelf"
STEPS = ["step1_catalog", "step2_shaped", "step3_writes", "step4_secure", "step5_complete"]
ANA = {"subject": "ana", "tenant": "north"}
BO = {"subject": "bo", "tenant": "south"}
LIBRARIAN = {"subject": "lee", "tenant": "north", "scopes": ["librarian"]}


def module(step: str) -> Any:
    load_server(str(SHELF / f"{step}.py"))
    return sys.modules[f"_ouncemcp_target_{step}"]


async def call(server: Any, tool: str, args: dict | None = None) -> Any:
    return await server.call_tool(tool, args or {})


def error_code(result: Any) -> str | None:
    return (result.meta or {}).get("ouncemcp", {}).get("error") if result.is_error else None


@pytest.mark.parametrize("step", STEPS)
def test_every_step_passes_check_without_lints(step: str, capsys):
    assert main(["check", str(SHELF / f"{step}.py")]) == 0
    assert "Lints:" not in capsys.readouterr().out
    assert lint_server(load_server(str(SHELF / f"{step}.py"))) == []


@pytest.mark.anyio
async def test_step1_search_and_get():
    server = module("step1_catalog").server
    found = (await call(server, "books.search", {"query": "ada"})).structured_content
    assert found["total"] > 0 and all("Ada" in b["author"] for b in found["books"])
    book = (await call(server, "books.get", {"id": found["books"][0]["id"]})).structured_content
    assert book["author"] == "Ada Moreau"
    assert error_code(await call(server, "books.get", {"id": "b-999"})) == "not_found"


@pytest.mark.anyio
async def test_step2_pages_shapes_and_skips_work():
    step = module("step2_shaped")
    server = step.server
    first = (await call(server, "books.search", {"query": "the", "limit": 10})).structured_content
    assert len(first["books"]) == 10 and first["next_cursor"] and first["total"] == 60
    assert "year" not in first["books"][0]  # detail-only field, concise result
    rest = (
        await call(
            server, "books.search", {"query": "the", "limit": 50, "cursor": first["next_cursor"]}
        )
    ).structured_content
    assert len({b["id"] for b in first["books"] + rest["books"]}) == 60
    before = step.LIBRARY.review_lookups
    concise = (await call(server, "books.get", {"id": "b-001"})).structured_content
    assert "reviews" not in concise and step.LIBRARY.review_lookups == before
    detailed = (
        await call(server, "books.get", {"id": "b-001", "response_format": "detailed"})
    ).structured_content
    assert detailed["reviews"] and step.LIBRARY.review_lookups == before + 1
    assert error_code(await call(server, "books.search", {"query": "  "})) == "validation"


@pytest.mark.anyio
async def test_step3_borrowing_needs_a_yes_and_happens_once():
    step = module("step3_writes")
    declined = step.build(approver=lambda request: False)
    assert (
        error_code(await call(declined, "loans.borrow", {"book_id": "b-001"}))
        == "approval_declined"
    )
    assert declined.deps.writes == []

    library = step.Library()
    server = step.build(library, approver=lambda request: True)
    loan = (await call(server, "loans.borrow", {"book_id": "b-002"})).structured_content
    again = (await call(server, "loans.borrow", {"book_id": "b-002"})).structured_content
    assert loan == again and library.writes == ["borrow b-002 by demo-member"]  # replayed
    mine = (await call(server, "loans.mine")).structured_content["loans"]
    assert [l["loan_id"] for l in mine] == [loan["loan_id"]]
    returned = (await call(server, "loans.return", {"loan_id": loan["loan_id"]})).structured_content
    assert returned["returned"] and library.writes[-1] == f"return {loan['loan_id']}"


@pytest.mark.anyio
async def test_step3_the_approval_shows_the_exact_book():
    step = module("step3_writes")
    seen: list[str] = []

    async def answer(message: str, response_type: Any, params: Any, context: Any) -> dict:
        seen.append(message)
        return {"approve": True}

    server = step.build()
    async with Client(server.fastmcp, elicitation_handler=answer) as client:
        await client.call_tool("loans.borrow", {"book_id": "b-003"})
    title = server.deps.books["b-003"].title
    assert seen == [f"Borrow '{title}' (b-003) for 3 weeks? It will be due back on the 21st day."]


@pytest.mark.anyio
async def test_step4_members_see_only_their_branch_and_loans():
    step = module("step4_secure")
    server = step.build(approver=lambda request: True)
    north_book = next(b.id for b in server.deps.books.values() if b.branch == "north")
    with as_caller(**ANA):
        loan = (await call(server, "loans.borrow", {"book_id": north_book})).structured_content
        assert error_code(await call(server, "loans.open")) == "unknown_tool"  # hidden from members
    with as_caller(**BO):
        assert error_code(await call(server, "books.get", {"id": north_book})) == "not_found"
        assert (
            error_code(await call(server, "loans.return", {"loan_id": loan["loan_id"]}))
            == "not_found"
        )
    with as_caller(**LIBRARIAN):
        open_loans = (await call(server, "loans.open")).structured_content["loans"]
    assert [l["loan_id"] for l in open_loans] == [loan["loan_id"]]
    assert server.deps.writes == [f"borrow {north_book} by ana"]


@pytest.mark.anyio
async def test_step5_report_runs_in_the_background():
    step = module("step5_complete")
    server = step.build(approver=lambda request: True)
    with as_caller(**ANA):
        await call(server, "loans.borrow", {"book_id": "b-002"})
    with as_caller(**LIBRARIAN):
        job = (await call(server, "reports.start")).structured_content["job"]
        await server.jobs.drain()
        report = (await call(server, "jobs.get", {"job": job})).structured_content
        rows = (
            await call(server, "handles.read", {"handle": report["handle"], "limit": 50})
        ).structured_content
    assert report["state"] == "done" and report["result"]["on_loan"] == 1
    assert report["result"]["busiest"] == "b-002" and report["items"] == 30
    assert len(rows["items"]) == 30


@pytest.mark.anyio
async def test_step5_resources_and_prompt():
    server = module("step5_complete").build()
    async with Client(server.fastmcp) as client:
        rules = await client.read_resource("shelf://rules")
        card = await client.read_resource("shelf://books/b-002")
        prompt = await client.get_prompt("recommend", {"subject": "history"})
    assert "3 weeks" in rules[0].text and '"id":"b-002"' in card[0].text.replace(" ", "")
    assert "about history" in prompt.messages[0].content.text


def test_the_book_task_suite_passes_with_the_replay_model():
    assert main(["eval", str(SHELF / "eval.yaml"), "--repeats", "1"]) == 0
