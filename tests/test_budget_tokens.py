import pytest

from ouncemcp import Budget, get_counter
from ouncemcp.core.tokens import compact_json


def test_budget_defaults_follow_spec():
    b = Budget()
    assert (b.tool_definition_target, b.tool_definition_warn, b.tool_definition_fail) == (
        250,
        500,
        1000,
    )
    assert b.result_tokens <= b.result_ceiling


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tool_definition_warn": 100},
        {"tool_definition_fail": 400},
        {"result_tokens": 9000},
        {"result_tokens": 0},
    ],
)
def test_budget_rejects_inconsistent_limits(kwargs):
    with pytest.raises(ValueError):
        Budget(**kwargs)


def test_compact_json_has_no_whitespace():
    assert compact_json({"a": [1, 2], "b": "é"}) == '{"a":[1,2],"b":"é"}'


def test_counter_matches_tiktoken():
    import tiktoken

    text = "Find cloud resources in a region, optionally filtered by kind."
    enc = tiktoken.get_encoding("o200k_base")
    assert get_counter().count(text) == len(enc.encode(text))


def test_truncate_text_respects_limit():
    counter = get_counter()
    text = "word " * 500
    cut = counter.truncate_text(text, 50)
    assert counter.count(cut) <= 50
    assert counter.truncate_text("short", 50) == "short"
