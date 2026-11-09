"""RES-8: shaped results always conform to the advertised output schema, or are tool errors."""

from typing import Annotated

import anyio
import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from ouncemcp import Budget, Detail, Effect, OunceServer, get_counter, shape_result
from ouncemcp.core.shaping import ResultTooLarge

ID = r"^r-[0-9]+$"


class Item(BaseModel):
    id: str = Field(pattern=ID)
    body: str
    note: str | None = None
    labels: list[str] = Field(default_factory=list)
    extra: Annotated[dict | None, Detail] = None


class Report(BaseModel):
    title: str
    items: list[Item] = Field(min_length=2)
    summary: str | None = None


def schema_validator(model: type[BaseModel]):
    from ouncemcp.core.schema import compact_schema

    return jsonschema.Draft202012Validator(compact_schema(model.model_json_schema()))


def report(n: int, body_len: int, note_len: int, labels: int) -> Report:
    return Report(
        title="t",
        items=[
            Item(
                id=f"r-{i}",
                body="b" * body_len,
                note=("n " * note_len) or None,
                labels=[f"label-{j}" for j in range(labels)],
                extra={"k": "v"},
            )
            for i in range(max(n, 2))
        ],
        summary="s " * 50,
    )


def test_strings_with_constraints_are_not_shortened():
    validator = schema_validator(Report)
    long_id = "r-" + "1" * 3000
    value = Report(title="t", items=[Item(id=long_id, body="x"), Item(id="r-2", body="y")])
    with pytest.raises(ResultTooLarge):
        # Shortening the id would break its pattern, so the only valid result is too big.
        shape_result(
            value, response_format="concise", budget=200, counter=get_counter(), validator=validator
        )


def test_unconstrained_long_strings_are_shortened_validly():
    validator = schema_validator(Report)
    out = shape_result(
        report(2, 5, 400, 0),
        response_format="concise",
        budget=250,
        counter=get_counter(),
        validator=validator,
    )
    validator.validate(out.data)
    assert out.truncation.strings
    assert get_counter().count(out.text) <= 250


def test_min_items_from_schema_respected():
    validator = schema_validator(Report)
    out = shape_result(
        report(200, 50, 0, 0),
        response_format="concise",
        budget=300,
        counter=get_counter(),
        validator=validator,
    )
    assert len(out.data["items"]) >= 2
    validator.validate(out.data)


class Wide(BaseModel):
    id: str
    a: str | None = None
    b: str | None = None
    c: str | None = None


class Pair(BaseModel):
    rows: list[Wide] = Field(min_length=2, max_length=2)


def test_optional_fields_dropped_across_all_items():
    # Strings are too short to shorten and the list cannot shrink, so fields must go.
    text = "word " * 12
    value = Pair(rows=[Wide(id=f"w{i}", a=text, b=text, c=text) for i in range(2)])
    validator = schema_validator(Pair)
    counter = get_counter()
    out = shape_result(
        value, response_format="concise", budget=100, counter=counter, validator=validator
    )
    validator.validate(out.data)
    assert out.truncation.dropped
    for pattern in out.truncation.dropped:
        key = pattern.rsplit(".", 1)[-1]
        assert all(key not in row for row in out.data["rows"])
    assert all("id" in row for row in out.data["rows"])  # required fields stay
    assert "omitted 'rows[*]." in out.text
    assert counter.count(out.text) <= 100


def test_raises_when_minimal_valid_result_is_over_budget():
    validator = schema_validator(Report)
    value = Report(
        title="t" * 0 + "title",
        items=[Item(id=f"r-{i}" + "", body="x") for i in range(2)],
    )
    big_title = value.model_copy(update={"title": "word " * 5})
    with pytest.raises(ResultTooLarge) as exc:
        shape_result(
            big_title,
            response_format="concise",
            budget=20,
            counter=get_counter(),
            validator=validator,
        )
    assert exc.value.budget == 20
    assert exc.value.min_tokens > 20


def test_detailed_results_conform_to_compacted_schema():
    # None optional values must be omitted, since compacted schemas do not allow null.
    validator = schema_validator(Report)
    value = Report(title="t", items=[Item(id="r-1", body="x"), Item(id="r-2", body="y")])
    out = shape_result(
        value, response_format="detailed", budget=2000, counter=get_counter(), validator=validator
    )
    validator.validate(out.data)


@pytest.mark.anyio
async def test_text_only_fallback_only_without_schema():
    long_id = "r-" + "1" * 3000
    for advertise in (True, False):
        server = OunceServer("t", budget=Budget(result_tokens=200), output_schemas=advertise)

        @server.tool(name="r.get", effect=Effect.READ)
        def get() -> Report:
            """Get."""
            return Report(title="t", items=[Item(id=long_id, body="x"), Item(id="r-2", body="y")])

        result = await server.call_tool("r.get", {})
        if advertise:
            assert result.is_error
            assert result.meta["ouncemcp"]["error"] == "result_too_large"
        else:
            assert not result.is_error
            assert result.meta["ouncemcp"]["truncation"]["text_only"] is True


@settings(max_examples=60, deadline=None)
@given(
    n=st.integers(0, 120),
    body_len=st.integers(0, 600),
    note_len=st.integers(0, 300),
    labels=st.integers(0, 20),
    budget=st.integers(60, 1500),
    fmt=st.sampled_from(["concise", "detailed"]),
)
def test_fuzz_server_results_conform_or_error(n, body_len, note_len, labels, budget, fmt):
    server = OunceServer(
        "fuzz", budget=Budget(result_tokens=budget, result_ceiling=max(budget, 8000))
    )

    @server.tool(name="report.get", effect=Effect.READ, result_tokens=budget)
    def get() -> Report:
        """Get the report."""
        return report(n, body_len, note_len, labels)

    async def run():
        (tool,) = await server.list_tools()
        result = await server.call_tool("report.get", {"response_format": fmt})
        return tool, result

    tool, result = anyio.run(run)
    counter = get_counter()
    text = result.content[0].text
    if result.is_error:
        assert "limit" in text or "narrow" in text.lower()
        assert counter.count(text) <= 200
        return
    jsonschema.Draft202012Validator(tool.output_schema).validate(result.structured_content)
    effective = budget if fmt == "concise" else max(budget, 8000)
    assert counter.count(text) <= effective
