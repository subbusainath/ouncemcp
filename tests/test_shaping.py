from typing import Annotated

import pytest
from pydantic import BaseModel

from ouncemcp import Detail, get_counter, shape_result


class Row(BaseModel):
    id: str
    note: str | None = None
    extra: Annotated[dict | None, Detail] = None


class Page(BaseModel):
    items: list[Row]
    next_cursor: str | None = None


def page(n: int, cursor: str | None = None) -> Page:
    return Page(
        items=[Row(id=f"r-{i}", extra={"k": "v" * 20}) for i in range(n)], next_cursor=cursor
    )


def test_concise_drops_detail_and_none_fields():
    out = shape_result(page(2), response_format="concise", budget=2000, counter=get_counter())
    assert out.data == {"items": [{"id": "r-0"}, {"id": "r-1"}]}
    assert not out.truncation


def test_detailed_keeps_detail_fields():
    out = shape_result(page(1), response_format="detailed", budget=2000, counter=get_counter())
    assert out.data["items"][0]["extra"] == {"k": "v" * 20}


@pytest.mark.parametrize("budget", [100, 150, 300, 700])
def test_result_never_exceeds_budget(budget):
    counter = get_counter()
    out = shape_result(page(500), response_format="detailed", budget=budget, counter=counter)
    assert counter.count(out.text) <= budget
    assert out.tokens == counter.count(out.text)


def test_list_truncation_note_is_accurate():
    counter = get_counter()
    out = shape_result(page(500), response_format="concise", budget=300, counter=counter)
    kept, total = out.truncation.lists["items"]
    assert total == 500
    assert kept == len(out.data["items"])
    assert f"kept {kept} of 500 items in 'items'" in out.text


def test_cursor_warning_when_page_trimmed():
    counter = get_counter()
    out = shape_result(page(500, "abc"), response_format="concise", budget=300, counter=counter)
    assert out.truncation.cursor_field == "next_cursor"
    assert "page without gaps" in out.text


def test_long_strings_are_shortened():
    counter = get_counter()
    value = Page(items=[Row(id="x", note="lorem ipsum " * 2000)])
    out = shape_result(value, response_format="concise", budget=200, counter=counter)
    assert out.truncation.strings
    assert out.data["items"][0]["note"].endswith("…")
    assert counter.count(out.text) <= 200


def test_meta_reports_shape():
    out = shape_result(page(500), response_format="concise", budget=300, counter=get_counter())
    meta = out.meta()
    assert meta["truncated"] is True
    assert meta["budget"] == 300
    assert meta["result_tokens"] <= 300
