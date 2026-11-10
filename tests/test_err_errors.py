"""ERR-1 to ERR-3: error categories, short validation messages, and no leaks."""

import logging
from typing import Any, Literal

import pytest
from fastmcp import Client
from fastmcp.exceptions import AuthorizationError, ToolError
from pydantic import BaseModel, Field, ValidationError

from ouncemcp import Effect, OunceServer
from ouncemcp.core.errors import (
    CATEGORIES,
    AmbiguousWrite,
    AuthenticationRequired,
    InvalidInput,
    NotFound,
    OunceError,
    PermissionDenied,
    RateLimited,
    TransientError,
    classify,
    clip,
    format_validation,
    redact,
)

SECRET = "hunter2-S3cr3t"
HOST = "db-primary.corp.internal"
IP = "10.20.30.40"
KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0"


class Out(BaseModel):
    ok: bool = True


class Args(BaseModel):
    region: str = Field(description="Region.")
    limit: int = Field(10, ge=1, le=100)
    mode: Literal["fast", "slow"] = "fast"


# Names match the HTTP client exceptions they stand in for, because OunceMCP recognizes
# them by shape and has no HTTP client dependency.
class ConnectError(Exception):
    pass


class ReadTimeout(Exception):
    pass


class Response:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}


class HTTPStatusError(Exception):
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status} from https://{HOST}/v1?key={KEY}")
        self.response = Response(status, headers)


def server_raising(exc: Exception | None, effect: Effect = Effect.READ) -> OunceServer:
    from ouncemcp.execution import RetryPolicy

    server = OunceServer("t", retry=RetryPolicy(max_attempts=1))

    @server.tool(name="t.go", effect=effect)
    def go(args: Args) -> Out:
        """Do the thing."""
        if exc is not None:
            raise exc
        return Out()

    return server


async def run(server: OunceServer, args: dict[str, Any] | None = None, tool: str = "t.go") -> Any:
    async with Client(server.fastmcp) as client:
        return await client.call_tool(
            tool, args if args is not None else {"region": "x"}, raise_on_error=False
        )


def info(result: Any) -> dict[str, Any]:
    return result.meta["ouncemcp"]


# ------------------------------------------------------------------ ERR-1: categories

CASES = [
    ("validation", InvalidInput("The date must be in the past."), Effect.READ),
    ("authentication", AuthenticationRequired("Sign in again."), Effect.READ),
    ("authentication", HTTPStatusError(401), Effect.READ),
    ("permission", PermissionDenied("You cannot see this account."), Effect.READ),
    ("permission", PermissionError("/etc/shadow"), Effect.READ),
    ("permission", AuthorizationError("needs scope admin"), Effect.READ),
    ("permission", HTTPStatusError(403), Effect.READ),
    ("not_found", NotFound("No invoice with that id."), Effect.READ),
    ("not_found", HTTPStatusError(404), Effect.READ),
    ("rate_limit", RateLimited("Slow down.", retry_after=3), Effect.READ),
    ("rate_limit", HTTPStatusError(429, {"retry-after": "7"}), Effect.READ),
    ("transient", TransientError("Upstream is restarting."), Effect.READ),
    ("transient", ConnectError("connection refused"), Effect.READ),
    ("transient", ConnectError("connection refused"), Effect.WRITE),
    ("transient", HTTPStatusError(503), Effect.READ),
    ("transient", ReadTimeout("slow"), Effect.READ),
    ("transient", TimeoutError("slow"), Effect.READ),
    ("ambiguous_write", AmbiguousWrite("Outcome unknown."), Effect.WRITE),
    ("ambiguous_write", ReadTimeout("slow"), Effect.WRITE),
    ("ambiguous_write", TimeoutError("slow"), Effect.DESTRUCTIVE),
    ("ambiguous_write", HTTPStatusError(504), Effect.WRITE),
    ("internal", RuntimeError("boom"), Effect.READ),
    ("internal", KeyError("missing"), Effect.WRITE),
    ("internal", HTTPStatusError(500), Effect.READ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("category", "exc", "effect"), CASES)
async def test_every_failure_lands_in_its_category(category, exc, effect):
    result = await run(server_raising(exc, effect))
    assert result.is_error is True
    assert info(result)["category"] == category
    assert result.content[0].text.startswith("[ouncemcp] ")


def test_every_category_is_covered_by_a_case():
    assert {case[0] for case in CASES} == set(CATEGORIES)
    assert len(CATEGORIES) == 8


@pytest.mark.anyio
async def test_unknown_tool_is_not_found():
    result = await run(server_raising(None), tool="t.nope")
    assert info(result)["category"] == "not_found"
    assert "nope" not in result.content[0].text or "List the tools" in result.content[0].text


@pytest.mark.anyio
async def test_invalid_arguments_are_validation_errors():
    result = await run(server_raising(None), {"limit": 500})
    assert info(result)["category"] == "validation"
    assert info(result)["retryable"] is False


@pytest.mark.anyio
async def test_retryable_flag_follows_the_category():
    expected = {
        "validation": False,
        "authentication": False,
        "permission": False,
        "not_found": False,
        "rate_limit": True,
        "transient": True,
        "ambiguous_write": False,
        "internal": False,
    }
    for category, exc, effect in CASES:
        result = await run(server_raising(exc, effect))
        assert info(result)["retryable"] is expected[category], category


@pytest.mark.anyio
async def test_rate_limit_reports_when_to_retry():
    result = await run(server_raising(HTTPStatusError(429, {"Retry-After": "7"})))
    assert info(result)["retry_after_s"] == 7
    result = await run(server_raising(RateLimited("Slow down.", retry_after=2.5)))
    assert info(result)["retry_after_s"] == 2.5
    result = await run(server_raising(RateLimited("Slow down.")))
    assert "retry_after_s" not in info(result)


@pytest.mark.anyio
async def test_tool_author_messages_and_hints_reach_the_caller():
    exc = NotFound("No invoice with that id.", hint="Search invoices by customer first.")
    result = await run(server_raising(exc))
    text = result.content[0].text
    assert "Not found: No invoice with that id." in text
    assert "Search invoices by customer first." in text


@pytest.mark.anyio
async def test_client_raises_tool_error_with_the_message():
    async with Client(server_raising(NotFound("No invoice with that id.")).fastmcp) as client:
        with pytest.raises(ToolError, match="No invoice with that id"):
            await client.call_tool("t.go", {"region": "x"})


def test_error_classes_are_ouncemcp_errors():
    for cls in (
        InvalidInput,
        AuthenticationRequired,
        PermissionDenied,
        NotFound,
        RateLimited,
        TransientError,
        AmbiguousWrite,
    ):
        assert issubclass(cls, OunceError)
        assert cls("x").category in CATEGORIES


# ------------------------------------------------------------------ ERR-2: validation messages


def validation_error(**bad: Any) -> ValidationError:
    try:
        Args(**bad)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        ({}, "`region` is required"),
        ({"region": "x", "limit": 500}, "`limit` must be at most 100"),
        ({"region": "x", "limit": 0}, "`limit` must be at least 1"),
        ({"region": "x", "limit": "many"}, "`limit` must be a whole number"),
        ({"region": "x", "mode": "medium"}, "`mode` must be one of: fast or slow"),
        ({"region": 5}, "`region` must be text"),
    ],
)
def test_validation_messages_say_how_to_fix_the_input(bad, expected):
    message = format_validation(validation_error(**bad))
    assert expected in message
    assert message.startswith("Fix the arguments and call again:")


def test_validation_messages_never_echo_the_values_sent():
    message = format_validation(validation_error(region="x", limit=987654, mode="s3cret-mode"))
    assert "987654" not in message
    assert "s3cret-mode" not in message
    assert "pydantic" not in message
    assert "http" not in message


def test_validation_messages_list_a_few_problems_then_count_the_rest():
    class Many(BaseModel):
        a: int
        b: int
        c: int
        d: int
        e: int

    with pytest.raises(ValidationError) as exc:
        Many()
    message = format_validation(exc.value)
    assert message.count("is required") == 3
    assert "and 2 more" in message


def test_validation_messages_are_capped():
    class Many(BaseModel):
        very_long_parameter_name_number_one: int
        very_long_parameter_name_number_two: int
        very_long_parameter_name_number_three: int

    with pytest.raises(ValidationError) as exc:
        Many()
    assert len(format_validation(exc.value, 60)) == 60
    assert format_validation(exc.value, 60).endswith("\u2026")


@pytest.mark.anyio
async def test_validation_errors_over_the_wire_are_short_and_actionable():
    result = await run(server_raising(None), {"region": "x", "limit": 500, "mode": "medium"})
    text = result.content[0].text
    assert "`limit` must be at most 100" in text
    assert "`mode` must be one of" in text
    assert len(text) < 300
    assert "500" not in text and "medium" not in text
    assert "errors.pydantic.dev" not in text and "input_value" not in text


@pytest.mark.anyio
async def test_configured_length_caps_error_messages():
    from ouncemcp.execution import Limits

    server = OunceServer("t", limits=Limits(max_error_chars=80))

    @server.tool(name="t.go", effect=Effect.READ)
    def go(args: Args) -> Out:
        """Do the thing."""
        return Out()

    result = await run(server, {"limit": 500, "mode": "medium"})
    prefix = len("[ouncemcp] Invalid input: ")
    assert len(result.content[0].text) <= 80 + prefix


# ------------------------------------------------------------------ ERR-3: no leaks

LEAKS = [SECRET, HOST, IP, KEY, "Traceback", 'File "', "/srv/app/secrets.py", "postgres://"]


def assert_clean(result: Any) -> None:
    everything = result.content[0].text + repr(result.meta)
    for leak in LEAKS:
        assert leak not in everything, leak


@pytest.mark.anyio
async def test_unexpected_exceptions_never_show_their_text():
    exc = RuntimeError(
        f'Traceback (most recent call last): File "/srv/app/secrets.py", line 9 '
        f"postgres://admin:{SECRET}@{HOST}:5432/prod {IP} {KEY}"
    )
    result = await run(server_raising(exc))
    assert_clean(result)
    assert info(result)["category"] == "internal"
    assert "reference" in result.content[0].text


@pytest.mark.anyio
async def test_internal_errors_log_the_details_under_the_reference(caplog):
    caplog.set_level(logging.ERROR, logger="ouncemcp")
    result = await run(server_raising(RuntimeError(f"db at {HOST} password={SECRET}")))
    ref = info(result)["ref"]
    assert ref in result.content[0].text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert ref in logged
    assert HOST in logged
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.anyio
@pytest.mark.parametrize("effect", [Effect.READ, Effect.WRITE])
async def test_upstream_exception_text_is_not_forwarded(effect):
    for exc in (HTTPStatusError(401), HTTPStatusError(500), ConnectError(f"{HOST} {IP}")):
        assert_clean(await run(server_raising(exc, effect)))


@pytest.mark.anyio
async def test_messages_written_by_tool_authors_are_redacted():
    exc = InvalidInput(
        f"Could not reach https://{HOST}/v1?token={SECRET} at {IP}:5432 using {KEY}; "
        f'see File "/srv/app/secrets.py", line 9'
    )
    result = await run(server_raising(exc))
    assert_clean(result)
    assert "Could not reach" in result.content[0].text


@pytest.mark.anyio
async def test_hints_are_redacted_too():
    result = await run(server_raising(NotFound("Missing.", hint=f"Try {HOST} with {KEY}")))
    assert_clean(result)


@pytest.mark.anyio
async def test_tool_errors_raised_by_handlers_are_redacted_and_internal():
    result = await run(server_raising(ToolError(f"cannot connect to {HOST}")))
    assert_clean(result)
    assert info(result)["category"] == "internal"


@pytest.mark.anyio
async def test_a_handler_that_returns_the_wrong_shape_is_an_internal_error():
    server = OunceServer("t")

    @server.tool(name="t.bad", effect=Effect.READ)
    def bad() -> Out:
        """Return the wrong thing."""
        return {"ok": f"not a bool {SECRET}"}  # type: ignore[return-value]

    result = await run(server, {}, tool="t.bad")
    assert info(result)["category"] == "internal"
    assert_clean(result)


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        (f"POST https://{HOST}/v1/items failed", HOST),
        (f"connect to {IP} refused", IP),
        (f"connect to {IP}:5432 refused", "5432"),
        (f"timeout talking to {HOST}:6379", HOST),
        ("timeout talking to localhost:8080", "localhost"),
        ("host api.example.com unreachable", "api.example.com"),
        (f"Authorization: Bearer {KEY}", KEY),
        (f"password={SECRET}", SECRET),
        (f"api_key: '{SECRET}'", SECRET),
        (f"the token is {SECRET}", SECRET),
        ("AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE"),
        (
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789 leaked",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        ),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJl", "eyJhbGci"),
        ("0123456789abcdef0123456789abcdef0123", "0123456789abcdef0123456789abcdef"),
        ("open /home/ubuntu/.aws/credentials failed", "/home/ubuntu"),
        ('File "/srv/app/x.py", line 3, in handler', "/srv/app"),
        ("fe80:0:0:0:204:61ff:fe9d:f156 down", "fe80"),
    ],
)
def test_redact_removes_sensitive_text(raw, gone):
    assert gone not in redact(raw)


@pytest.mark.parametrize(
    "plain",
    [
        "The region must be one of ap-south-1 or us-east-1.",
        "Call inventory.search first, then inventory.get with the id.",
        "Use limit of 50 or less (page:2 of 9).",
        "No invoice with id INV-2041.",
        "Try again in 3 seconds.",
    ],
)
def test_redact_leaves_ordinary_guidance_alone(plain):
    assert redact(plain) == plain


def test_redact_drops_everything_after_a_traceback_header():
    text = redact("Failed. Traceback (most recent call last):\n  File x\nValueError: nope")
    assert text == "Failed."


def test_clip_adds_an_ellipsis():
    assert clip("abcdef", 4) == "abc\u2026"
    assert clip("abc", 4) == "abc"


def test_classify_is_quiet_when_asked(caplog):
    caplog.set_level(logging.ERROR, logger="ouncemcp")
    classify(RuntimeError("x"), quiet=True)
    assert not caplog.records
    classify(RuntimeError("x"))
    assert caplog.records
