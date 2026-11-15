# 10. Testing

A server is used by a model that will try every argument you did not expect. Tests are how you find out first. Every stage of Shelf is tested in [`tests/test_book_shelf.py`](https://github.com/subbusainath/ouncemcp/blob/main/tests/test_book_shelf.py); this chapter walks through the patterns.

## Calling tools in-process

`await server.call_tool(name, arguments)` runs a tool exactly as a client would, with shaping, policies and errors, but without a network:

```python
result = await server.call_tool("books.search", {"query": "ada"})
assert result.structured_content["total"] > 0
assert result.meta["ouncemcp"]["result_tokens"] < 500
```

Use `pytest` with `anyio` (or `pytest-asyncio`) for async tests. Errors are results, not exceptions: check `result.is_error` and `result.meta["ouncemcp"]["error"]`.

## Writes: approvals and retries

A fresh server per test, and an `approver` that answers for the person:

```python title="tests/test_book_shelf.py"
--8<-- "tests/test_book_shelf.py:writes"
```

This one test checks the four things chapter 6 promised: a declined borrow changes nothing, an approved borrow happens, a repeated borrow is replayed rather than repeated, and returning works.

To test the question the person actually sees, connect a real FastMCP `Client` with an `elicitation_handler` and record the message; the book's tests check the exact preview text.

## Callers and isolation

`as_caller` sets the verified identity for the code inside it, as if a token had been verified:

```python title="tests/test_book_shelf.py"
--8<-- "tests/test_book_shelf.py:callers"
```

Write the intruder test for every tenant boundary: one tenant creates something, another tries to read, change or list it.

## The security catalog

`ouncemcp.eval.run_security_suite` runs standard attacks against your server. It always probes guessed names for every tool with a policy, and runs the cases you add: cross-tenant reads, cursor and handle reuse, SSRF probes for URL arguments, and token passthrough.

```python title="tests/test_book_shelf.py"
--8<-- "tests/test_book_shelf.py:security"
```

## Keeping definitions honest

Two more tests are worth having in every project:

```python
from ouncemcp.cli import main
from ouncemcp.core.lints import lint_server

def test_check_passes():
    assert main(["check", "server.py"]) == 0
    assert lint_server(server) == []
```

Pin token counts too, if you care about them. `await server.cost_report()` measures the tools the current caller can see (for Shelf without a token, 1,109 tokens: the two librarian tools are hidden), and a test that fails when a definition grows makes the cost visible in review:

```python
cost = await server.cost_report()
assert cost.total <= 1_200
assert {t.name: t.total for t in cost.tools}["books.search"] <= 250
```

## Protocol conformance

OunceMCP itself is checked against the official MCP conformance suite in CI (`ouncemcp.eval.conformance` is the target server it runs against), so your server inherits a protocol layer that is known to conform. To test your own transport setup end to end, connect a FastMCP `Client` to the running server over HTTP or stdio in a test; see the [testing guide](../guides/testing.md).

## Summary

- `server.call_tool` runs a tool in-process with everything but the network.
- Build a fresh server per test; answer approvals with `approver=`.
- `as_caller` signs in as anyone; test every tenant boundary from both sides.
- Run the security catalog and `check` in your test suite.
