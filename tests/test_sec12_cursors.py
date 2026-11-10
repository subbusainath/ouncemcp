"""SEC-12: signed, versioned, principal-bound, expiring cursors."""

import logging
from typing import Annotated

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from ouncemcp import (
    Cursor,
    CursorSigner,
    Effect,
    InvalidCursor,
    OunceServer,
    Paginated,
    ToolContext,
)

KEY = b"k" * 32
OLD_KEY = b"o" * 32


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def bound(signer: CursorSigner, tool="t", principal="alice", query=None):
    return signer.bind(tool=tool, principal=principal, query=query or {})


def reason_of(fn) -> str:
    with pytest.raises(InvalidCursor) as info:
        fn()
    return info.value.reason


# ---------------------------------------------------------------- the cursor itself


@settings(max_examples=100, deadline=None)
@given(offset=st.integers(0, 2**32 - 1))
def test_round_trip(offset):
    cursors = bound(CursorSigner([KEY]))
    assert cursors.decode(cursors.encode(offset)) == offset


def test_none_means_first_page():
    assert bound(CursorSigner([KEY])).decode(None) == 0


def test_format_is_versioned_compact_and_url_safe():
    token = bound(CursorSigner([KEY])).encode(12_345)
    assert token.startswith("s1.")
    assert "=" not in token and "+" not in token and "/" not in token
    assert len(token) == 59


def test_offsets_outside_range_are_rejected():
    cursors = bound(CursorSigner([KEY]))
    with pytest.raises(ValueError):
        cursors.encode(-1)
    with pytest.raises(ValueError):
        cursors.encode(2**32)


@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_any_single_character_change_is_rejected(data):
    cursors = bound(CursorSigner([KEY]))
    token = cursors.encode(data.draw(st.integers(0, 10_000)))
    i = data.draw(st.integers(0, len(token) - 1))
    other = data.draw(st.sampled_from([c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ-_" if c != token[i]]))
    with pytest.raises(InvalidCursor):
        cursors.decode(token[:i] + other + token[i + 1 :])


@pytest.mark.parametrize("junk", ["", "garbage", "s1.", "s1.@@@", "s1.AAAA", "x1.AAAA", "s2.AAAA"])
def test_junk_is_rejected(junk):
    assert reason_of(lambda: bound(CursorSigner([KEY])).decode(junk)) == "malformed"


def test_truncated_and_extended_cursors_are_rejected():
    cursors = bound(CursorSigner([KEY]))
    token = cursors.encode(7)
    assert reason_of(lambda: cursors.decode(token[:-4])) == "malformed"
    assert reason_of(lambda: cursors.decode(token + "AAAA")) == "malformed"


def test_padded_or_re_encoded_cursors_are_rejected():
    cursors = bound(CursorSigner([KEY]))
    token = cursors.encode(7)
    assert reason_of(lambda: cursors.decode(token + "=")) == "malformed"
    assert reason_of(lambda: cursors.decode(token + "==")) == "malformed"
    standard = token.replace("-", "+").replace("_", "/")
    if standard != token:
        assert reason_of(lambda: cursors.decode(standard)) == "malformed"


def test_a_forged_cursor_without_the_key_is_rejected():
    forged = bound(CursorSigner([OLD_KEY])).encode(5)
    assert reason_of(lambda: bound(CursorSigner([KEY])).decode(forged)) == "signature"


def test_legacy_unsigned_cursor_is_rejected_with_a_restart_hint():
    cursors = bound(CursorSigner([KEY]))
    with pytest.raises(InvalidCursor) as info:
        cursors.decode("o1.MjA")
    assert info.value.reason == "legacy"
    assert "omit cursor" in str(info.value).lower()


def test_expiry():
    clock = Clock()
    cursors = bound(CursorSigner([KEY], ttl_s=600, clock=clock))
    token = cursors.encode(3)
    clock.now += 599
    assert cursors.decode(token) == 3
    clock.now += 2
    with pytest.raises(InvalidCursor) as info:
        cursors.decode(token)
    assert info.value.reason == "expired"
    assert "omit cursor" in str(info.value).lower()


def test_cursor_is_bound_to_principal():
    signer = CursorSigner([KEY])
    token = bound(signer, principal="alice").encode(4)
    assert reason_of(lambda: bound(signer, principal="bob").decode(token)) == "principal"


def test_cursor_is_bound_to_tool():
    signer = CursorSigner([KEY])
    token = bound(signer, tool="a.list").encode(4)
    assert reason_of(lambda: bound(signer, tool="b.list").decode(token)) == "tool"


def test_cursor_is_bound_to_query_arguments():
    signer = CursorSigner([KEY])
    token = bound(signer, query={"region": "ap-south-1"}).encode(4)
    assert bound(signer, query={"region": "ap-south-1"}).decode(token) == 4
    assert reason_of(lambda: bound(signer, query={"region": "eu-west-1"}).decode(token)) == "query"


def test_query_normalization_ignores_key_order_and_unset_values():
    signer = CursorSigner([KEY])
    token = bound(signer, query={"a": 1, "b": [1, 2], "c": None}).encode(9)
    assert bound(signer, query={"b": [1, 2], "a": 1}).decode(token) == 9


def test_query_values_that_differ_only_in_type_do_not_collide():
    signer = CursorSigner([KEY])
    token = bound(signer, query={"n": 1}).encode(1)
    assert reason_of(lambda: bound(signer, query={"n": "1"}).decode(token)) == "query"


# ---------------------------------------------------------------- keys and rotation


def test_rotation_new_key_signs_and_old_key_still_verifies():
    old_only = CursorSigner([OLD_KEY])
    rotated = CursorSigner([KEY, OLD_KEY])
    issued_before = bound(old_only).encode(8)
    issued_after = bound(rotated).encode(9)
    assert bound(rotated).decode(issued_before) == 8
    assert bound(rotated).decode(issued_after) == 9
    assert bound(CursorSigner([KEY])).decode(issued_after) == 9
    assert reason_of(lambda: bound(CursorSigner([KEY])).decode(issued_before)) == "signature"


def test_keys_must_be_long_enough_and_present():
    with pytest.raises(ValueError, match="16 bytes"):
        CursorSigner([b"short"])
    with pytest.raises(ValueError, match="at least one"):
        CursorSigner([])
    with pytest.raises(ValueError, match="ttl_s"):
        CursorSigner([KEY], ttl_s=0)


def test_string_keys_are_accepted():
    assert bound(CursorSigner(["s" * 32])).decode(bound(CursorSigner(["s" * 32])).encode(2)) == 2


def test_keys_come_from_the_environment_newest_first():
    signer = CursorSigner.from_env({"OUNCEMCP_CURSOR_KEYS": f"{'n' * 32}, {'o' * 32}"})
    assert not signer.is_ephemeral
    issued_by_old = bound(CursorSigner(["o" * 32])).encode(1)
    assert bound(signer).decode(issued_by_old) == 1
    assert bound(CursorSigner(["n" * 32])).decode(bound(signer).encode(2)) == 2


def test_without_a_key_the_signer_is_ephemeral_and_warns_once(caplog):
    signer = CursorSigner.from_env({})
    assert signer.is_ephemeral
    with caplog.at_level(logging.WARNING, logger="ouncemcp"):
        bound(signer).encode(1)
        bound(signer).encode(2)
    warnings = [r for r in caplog.records if "OUNCEMCP_CURSOR_KEYS" in r.getMessage()]
    assert len(warnings) == 1
    assert "random" in warnings[0].getMessage()


def test_ephemeral_keys_differ_between_signers():
    a, b = CursorSigner.ephemeral(), CursorSigner.ephemeral()
    assert reason_of(lambda: bound(b).decode(bound(a).encode(1))) == "signature"


def test_configured_keys_do_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="ouncemcp"):
        bound(CursorSigner([KEY])).encode(1)
    assert not caplog.records


# ---------------------------------------------------------------- through a server


class Row(BaseModel):
    id: str


class Page(BaseModel):
    items: Annotated[list[Row], Paginated]
    next_cursor: Cursor = None


class In(BaseModel):
    region: str = "ap-south-1"
    kind: str | None = None
    limit: int = Field(2, ge=1, le=100)
    cursor: str | None = None


ROWS = [Row(id=f"r-{i}") for i in range(10)]


def make_server(**kwargs) -> OunceServer:
    kwargs.setdefault("cursors", CursorSigner([KEY]))
    server = OunceServer("s", **kwargs)

    @server.tool(name="rows.list", effect=Effect.READ)
    def list_rows(args: In, ctx: ToolContext) -> Page:
        """List rows."""
        page = ctx.page(ROWS, cursor=args.cursor, limit=args.limit)
        return Page(items=page.items, next_cursor=page.next_cursor)

    @server.tool(name="rows.other", effect=Effect.READ)
    def other_rows(args: In, ctx: ToolContext) -> Page:
        """List rows, other tool."""
        page = ctx.page(ROWS, cursor=args.cursor, limit=args.limit)
        return Page(items=page.items, next_cursor=page.next_cursor)

    return server


async def first_cursor(server: OunceServer, **args) -> str:
    result = await server.call_tool("rows.list", args)
    assert not result.is_error
    return result.structured_content["next_cursor"]


@pytest.mark.anyio
async def test_server_issues_signed_cursors_that_resume_exactly():
    server = make_server()
    cursor = await first_cursor(server)
    assert cursor.startswith("s1.")
    result = await server.call_tool("rows.list", {"cursor": cursor})
    assert [i["id"] for i in result.structured_content["items"]] == ["r-2", "r-3"]


@pytest.mark.anyio
async def test_limit_and_response_format_may_change_between_pages():
    server = make_server()
    cursor = await first_cursor(server)
    result = await server.call_tool("rows.list", {"cursor": cursor, "limit": 5})
    assert not result.is_error
    ids = [i["id"] for i in result.structured_content["items"]]
    assert ids == ["r-2", "r-3", "r-4", "r-5", "r-6"]


@pytest.mark.anyio
async def test_explicit_defaults_do_not_count_as_changed_filters():
    server = make_server()
    cursor = await first_cursor(server)
    result = await server.call_tool(
        "rows.list", {"cursor": cursor, "region": "ap-south-1", "kind": None}
    )
    assert not result.is_error


@pytest.mark.anyio
async def test_changed_filters_are_rejected_with_a_fix():
    server = make_server()
    cursor = await first_cursor(server, region="ap-south-1")
    result = await server.call_tool("rows.list", {"cursor": cursor, "region": "eu-west-1"})
    assert result.is_error
    assert result.meta["ouncemcp"]["error"] == "invalid_cursor"
    assert result.meta["ouncemcp"]["reason"] == "query"
    text = result.content[0].text
    assert "filters" in text and "omit cursor" in text


@pytest.mark.anyio
async def test_cursor_from_another_tool_is_rejected():
    server = make_server()
    cursor = await first_cursor(server)
    result = await server.call_tool("rows.other", {"cursor": cursor})
    assert result.is_error
    assert result.meta["ouncemcp"]["reason"] == "tool"


@pytest.mark.anyio
async def test_cursor_from_another_principal_is_rejected():
    current = {"who": "alice"}
    server = make_server(principal=lambda ctx: current["who"])
    cursor = await first_cursor(server)
    current["who"] = "bob"
    result = await server.call_tool("rows.list", {"cursor": cursor})
    assert result.is_error
    assert result.meta["ouncemcp"]["reason"] == "principal"
    assert "alice" not in result.content[0].text
    current["who"] = "alice"
    assert not (await server.call_tool("rows.list", {"cursor": cursor})).is_error


@pytest.mark.anyio
async def test_expired_cursor_is_rejected():
    clock = Clock()
    server = make_server(cursors=CursorSigner([KEY], ttl_s=60, clock=clock))
    cursor = await first_cursor(server)
    clock.now += 61
    result = await server.call_tool("rows.list", {"cursor": cursor})
    assert result.is_error
    assert result.meta["ouncemcp"]["reason"] == "expired"


@pytest.mark.anyio
async def test_tampered_and_legacy_cursors_are_tool_errors():
    server = make_server()
    cursor = await first_cursor(server)
    flipped = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    for bad, reason in ((flipped, {"signature", "malformed"}), ("o1.Mg", {"legacy"})):
        result = await server.call_tool("rows.list", {"cursor": bad})
        assert result.is_error
        assert result.meta["ouncemcp"]["error"] == "invalid_cursor"
        assert result.meta["ouncemcp"]["reason"] in reason


@pytest.mark.anyio
async def test_default_principal_is_anonymous_and_stable():
    server = make_server()
    cursor = await first_cursor(server)
    assert not (await server.call_tool("rows.list", {"cursor": cursor})).is_error


@pytest.mark.anyio
async def test_ctx_offset_and_window_use_the_same_signed_format():
    server = OunceServer("w", cursors=CursorSigner([KEY]))

    @server.tool(name="db.list", effect=Effect.READ)
    def db_list(args: In, ctx: ToolContext) -> Page:
        """List rows from a database."""
        start = ctx.offset(args.cursor)
        items = ROWS[start : start + args.limit]
        more = start + len(items) < len(ROWS)
        page = ctx.window(items, start=start, has_more=more, total=len(ROWS))
        return Page(items=page.items, next_cursor=page.next_cursor)

    first = await server.call_tool("db.list", {})
    cursor = first.structured_content["next_cursor"]
    assert cursor.startswith("s1.")
    second = await server.call_tool("db.list", {"cursor": cursor})
    assert [i["id"] for i in second.structured_content["items"]] == ["r-2", "r-3"]
    forged = await server.call_tool("db.list", {"cursor": "s1." + cursor[3:-2] + "AA"})
    assert forged.is_error


@pytest.mark.anyio
async def test_ignore_lets_a_tool_name_its_own_paging_arguments():
    server = OunceServer("i", cursors=CursorSigner([KEY]))

    class Args(BaseModel):
        q: str = ""
        page_size: int = 2
        page_token: str | None = None

    @server.tool(name="q.list", effect=Effect.READ)
    def q_list(args: Args, ctx: ToolContext) -> Page:
        """Search rows."""
        page = ctx.page(
            ROWS,
            cursor=args.page_token,
            limit=args.page_size,
            ignore=("page_size", "page_token"),
        )
        return Page(items=page.items, next_cursor=page.next_cursor)

    first = await server.call_tool("q.list", {"q": "x"})
    token = first.structured_content["next_cursor"]
    same = await server.call_tool("q.list", {"q": "x", "page_token": token, "page_size": 4})
    assert not same.is_error
    changed = await server.call_tool("q.list", {"q": "y", "page_token": token})
    assert changed.is_error


@pytest.mark.anyio
async def test_server_without_configured_keys_still_pages_and_warns(monkeypatch, caplog):
    monkeypatch.delenv("OUNCEMCP_CURSOR_KEYS", raising=False)
    server = make_server(cursors=None)
    with caplog.at_level(logging.WARNING, logger="ouncemcp"):
        cursor = await first_cursor(server)
        result = await server.call_tool("rows.list", {"cursor": cursor})
    assert not result.is_error
    assert any("OUNCEMCP_CURSOR_KEYS" in r.getMessage() for r in caplog.records)


class FakeToken:
    def __init__(self, subject=None, claims=None, client_id="client-1"):
        self.subject = subject
        self.claims = claims or {}
        self.client_id = client_id


@pytest.mark.anyio
async def test_default_principal_comes_from_the_verified_access_token(monkeypatch):
    token = {"value": FakeToken(subject="alice")}
    monkeypatch.setattr("ouncemcp.core.server.get_access_token", lambda: token["value"])
    server = make_server()
    cursor = await first_cursor(server)
    assert not (await server.call_tool("rows.list", {"cursor": cursor})).is_error
    token["value"] = FakeToken(subject="bob")
    result = await server.call_tool("rows.list", {"cursor": cursor})
    assert result.meta["ouncemcp"]["reason"] == "principal"
    token["value"] = FakeToken(subject=None, claims={"sub": "alice"})
    assert not (await server.call_tool("rows.list", {"cursor": cursor})).is_error
    token["value"] = None
    assert (await server.call_tool("rows.list", {"cursor": cursor})).is_error


@pytest.mark.anyio
async def test_resolver_returning_nothing_means_anonymous():
    server = make_server(principal=lambda ctx: None)
    cursor = await first_cursor(server)
    assert not (await server.call_tool("rows.list", {"cursor": cursor})).is_error


def test_a_hand_built_context_can_page_for_handler_unit_tests():
    ctx = ToolContext(tool="rows.list", response_format="concise", result_budget=1000)
    first = ctx.page(ROWS, cursor=None, limit=3)
    second = ctx.page(ROWS, cursor=first.next_cursor, limit=3)
    assert [r.id for r in second.items] == ["r-3", "r-4", "r-5"]
