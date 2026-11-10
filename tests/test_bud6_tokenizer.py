"""BUD-6: a pluggable tokenizer, named in every report."""

import json
import math
from typing import Annotated

import pytest
import tiktoken
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from ouncemcp import (
    Budget,
    CallableCounter,
    Cursor,
    Effect,
    OunceServer,
    Paginated,
    TiktokenCounter,
    TokenCounter,
    ToolContext,
    get_counter,
    measure_tools,
    shape_result,
)
from ouncemcp.cli import main

TEXT = "नमस्ते दुनिया 日本語のテキスト, plus some ordinary English words."


def words(text: str) -> int:
    return len(text.split())


def quarter_chars(text: str) -> int:
    return math.ceil(len(text) / 4)


# ---------------------------------------------------------------- the interface


def test_default_is_o200k_and_named():
    counter = get_counter()
    assert isinstance(counter, TiktokenCounter)
    assert counter.name == "o200k_base"
    assert counter.encoding_name == "o200k_base"


@pytest.mark.parametrize("encoding", ["o200k_base", "cl100k_base", "gpt2"])
def test_any_tiktoken_encoding_matches_tiktoken(encoding):
    counter = get_counter(encoding)
    assert counter.name == encoding
    assert counter.count(TEXT) == len(tiktoken.get_encoding(encoding).encode(TEXT))


def test_encodings_differ():
    assert get_counter("cl100k_base").count(TEXT) != get_counter("o200k_base").count(TEXT)


def test_unknown_encoding_lists_the_valid_ones():
    with pytest.raises(ValueError, match="cl100k_base") as info:
        get_counter("not-an-encoding")
    assert "not-an-encoding" in str(info.value)


def test_encoding_counters_are_cached_and_instances_pass_through():
    assert get_counter("cl100k_base") is get_counter("cl100k_base")
    counter = CallableCounter(words, name="words")
    assert get_counter(counter) is counter


def test_a_callable_needs_a_name():
    with pytest.raises(ValueError, match="name"):
        get_counter(words)
    with pytest.raises(ValueError, match="name"):
        get_counter(words, name="  ")
    with pytest.raises(ValueError, match="name"):
        CallableCounter(words, name="")


def test_name_only_applies_to_callables():
    with pytest.raises(ValueError, match="callable"):
        get_counter("o200k_base", name="x")
    with pytest.raises(ValueError, match="callable"):
        get_counter(CallableCounter(words, name="w"), name="x")


def test_non_callables_are_rejected():
    with pytest.raises(TypeError, match="tokenizer"):
        get_counter(42)  # type: ignore[arg-type]


def test_a_callable_counter_counts_and_is_named():
    counter = get_counter(words, name="words-v1")
    assert isinstance(counter, TokenCounter)
    assert counter.name == "words-v1"
    assert counter.count("one two  three") == 3
    assert counter.count_json({"a": "b c"}) == words('{"a":"b c"}')


@pytest.mark.parametrize("bad", [-1, 1.5, "3", None])
def test_a_misbehaving_callable_is_reported_by_name(bad):
    counter = get_counter(lambda text: bad, name="broken")
    with pytest.raises(ValueError, match="broken"):
        counter.count("x")


def test_integer_like_results_are_accepted():
    class Count:
        def __index__(self) -> int:
            return 7

    assert get_counter(lambda text: Count(), name="idx").count("x") == 7  # type: ignore[arg-type,return-value]


def test_subclasses_only_need_name_and_count():
    class Chars(TokenCounter):
        name = "chars"

        def count(self, text: str) -> int:
            return len(text)

    counter = Chars()
    assert counter.count("abc") == 3
    assert counter.truncate_text("abcdef", 4) == "abcd"
    assert counter.truncate_text("abc", 4) == "abc"


@settings(max_examples=100, deadline=None)
@given(text=st.text(max_size=300), limit=st.integers(0, 80))
def test_truncation_with_a_callable_is_the_longest_prefix_that_fits(text, limit):
    counter = CallableCounter(quarter_chars, name="q")
    cut = counter.truncate_text(text, limit)
    assert text.startswith(cut)
    assert counter.count(cut) <= limit
    if cut != text:
        assert counter.count(text[: len(cut) + 1]) > limit


def test_tiktoken_truncation_is_unchanged():
    counter = get_counter("cl100k_base")
    text = "word " * 500
    cut = counter.truncate_text(text, 50)
    assert counter.count(cut) <= 50
    assert counter.truncate_text("short", 50) == "short"


# ---------------------------------------------------------------- reports name it


class Item(BaseModel):
    id: str
    text: str


class Page(BaseModel):
    items: Annotated[list[Item], Paginated]
    next_cursor: Cursor = None


def build(**kwargs) -> OunceServer:
    server = OunceServer("t", **kwargs)

    @server.tool(name="items.list", effect=Effect.READ)
    def list_items(ctx: ToolContext, limit: int = 50) -> Page:
        """List items in a region, newest first. Use items.get for one."""
        rows = [Item(id=f"i-{n}", text="lorem ipsum dolor " * 6) for n in range(200)]
        page = ctx.page(rows, cursor=None, limit=limit)
        return Page(items=page.items, next_cursor=page.next_cursor)

    return server


@pytest.mark.anyio
async def test_the_same_server_is_reported_with_two_tokenizers():
    a = await build(tokenizer="o200k_base").cost_report()
    b = await build(tokenizer="cl100k_base").cost_report()
    c = await build(tokenizer=words, tokenizer_name="words").cost_report()
    assert (a.tokenizer, b.tokenizer, c.tokenizer) == ("o200k_base", "cl100k_base", "words")
    assert len({a.total, b.total, c.total}) == 3
    for report in (a, b, c):
        data = report.to_dict()
        assert data["tokenizer"] == report.tokenizer
        assert f"Tokenizer        {report.tokenizer}" in report.render_text()
        for tool in report.tools:
            parts = tool.name_tokens + tool.description + tool.input_schema + tool.output_schema
            assert parts + tool.structure == tool.total


@pytest.mark.anyio
async def test_measure_tools_names_the_counter_it_was_given():
    server = build()
    tools = await server.list_tools()
    counter = CallableCounter(quarter_chars, name="quarter-chars")
    report = measure_tools(tools, Budget(), counter)
    assert report.tokenizer == "quarter-chars"
    assert report.to_dict()["tokenizer"] == "quarter-chars"


def test_tokenizer_name_and_instance_on_the_server():
    assert build().counter.name == "o200k_base"
    custom = CallableCounter(words, name="mine")
    assert build(tokenizer=custom).counter is custom
    with pytest.raises(ValueError, match="name"):
        build(tokenizer=words)


@pytest.mark.anyio
async def test_results_name_the_tokenizer_that_counted_them():
    server = build(tokenizer=quarter_chars, tokenizer_name="quarter-chars")
    result = await server.call_tool("items.list", {"limit": 5})
    assert result.meta["ouncemcp"]["tokenizer"] == "quarter-chars"
    default = await build().call_tool("items.list", {"limit": 5})
    assert default.meta["ouncemcp"]["tokenizer"] == "o200k_base"


@pytest.mark.anyio
async def test_too_large_errors_name_the_tokenizer():
    server = build(budget=Budget(result_tokens=20), tokenizer="cl100k_base")
    result = await server.call_tool("items.list", {})
    assert result.is_error
    assert result.meta["ouncemcp"]["error"] == "result_too_large"
    assert result.meta["ouncemcp"]["tokenizer"] == "cl100k_base"


@pytest.mark.anyio
async def test_budgets_are_enforced_in_the_chosen_tokenizer():
    counted = {}
    for label, kwargs in {
        "o200k": {},
        "chars": {"tokenizer": lambda s: len(s), "tokenizer_name": "chars"},
    }.items():
        server = build(budget=Budget(result_tokens=900), **kwargs)
        result = await server.call_tool("items.list", {})
        counted[label] = len(result.structured_content["items"])
        assert result.meta["ouncemcp"]["result_tokens"] <= 900
    assert counted["chars"] < counted["o200k"]


@settings(max_examples=60, deadline=None)
@given(n=st.integers(1, 80), budget=st.integers(200, 1500), size=st.integers(1, 12))
def test_shaping_fits_the_budget_for_any_monotone_counter(n, budget, size):
    counter = CallableCounter(lambda s: math.ceil(len(s) / size), name=f"chars/{size}")
    value = Page(items=[Item(id=f"i-{k}", text="x" * (k % 40)) for k in range(n)])
    out = shape_result(value, response_format="concise", budget=budget, counter=counter)
    assert counter.count(out.text) <= budget
    assert out.tokens == counter.count(out.text)
    assert out.meta()["tokenizer"] == counter.name


# ---------------------------------------------------------------- the CLI


SERVER_SOURCE = """
from pydantic import BaseModel
from ouncemcp import Effect, OunceServer

server = OunceServer("t", tokenizer="cl100k_base")

class Out(BaseModel):
    value: str

@server.tool(name="t.get", effect=Effect.READ)
def get(key: str) -> Out:
    '''Get a value by key.'''
    return Out(value=key)
"""


@pytest.fixture
def server_file(tmp_path):
    path = tmp_path / "srv.py"
    path.write_text(SERVER_SOURCE)
    return str(path)


def test_check_uses_the_servers_own_tokenizer(server_file, capsys):
    assert main(["check", server_file, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tokenizer"] == "cl100k_base"


def test_check_flag_overrides_the_tokenizer(server_file, capsys):
    assert main(["check", server_file, "--json", "--tokenizer", "o200k_base"]) == 0
    assert json.loads(capsys.readouterr().out)["tokenizer"] == "o200k_base"
    assert main(["check", server_file, "--tokenizer", "gpt2"]) == 0
    assert "Tokenizer        gpt2" in capsys.readouterr().out


def test_check_names_the_default_for_a_plain_fastmcp_server(capsys):
    from conftest import EXAMPLE

    assert main(["check", f"{EXAMPLE / 'plain_server.py'}:mcp", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tokenizer"] == "o200k_base"


def test_unknown_tokenizer_flag_is_an_error(server_file, capsys):
    assert main(["check", server_file, "--tokenizer", "nope"]) == 2
    assert "cl100k_base" in capsys.readouterr().err


def test_context_names_the_tokenizer(server_file, capsys):
    assert main(["context", server_file, "t.get"]) == 0
    assert "cl100k_base" in capsys.readouterr().err
    assert main(["context", server_file, "t.get", "--tokenizer", "gpt2"]) == 0
    assert "gpt2" in capsys.readouterr().err
