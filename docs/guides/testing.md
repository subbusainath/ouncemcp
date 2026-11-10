# Testing a server

**Call tools in-process.** `await server.call_tool("notes.search", {"query": "x"})` returns the same result a client gets, including `_meta.ouncemcp` with tokens and error categories.

**Test as a verified caller.**

```python
from ouncemcp.testing import as_caller

with as_caller(subject="alice", tenant="acme", scopes=["tickets:write"]):
    result = await server.call_tool("tickets.close", {"ticket_id": "T-1"})
```

**Run the security catalog** (EVAL-2). It always probes guessed names for every tool with a policy, plus the cases you add:

```python
from ouncemcp.eval import CrossTenant, CursorReuse, HandleReplay, SSRFProbe, run_security_suite

ACME, GLOBEX = {"subject": "a", "tenant": "acme"}, {"subject": "m", "tenant": "globex"}
report = await run_security_suite(server, [
    CrossTenant("invoices.list", {}, owner=ACME, intruder=GLOBEX),
    CursorReuse("invoices.list", {"limit": 1}, owner=ACME, intruder=GLOBEX),
    HandleReplay("report.start", {}, "report.approve", "handle", "handle", ACME, GLOBEX, single_use=True),
    SSRFProbe("web.fetch", "url"),
])
assert report.passed, report.render_text()
```

`TokenPassthrough(tool, args, recorded=...)` checks that recorded upstream requests never carry the caller's token.

**Protocol conformance** (EVAL-1). `python -m ouncemcp.eval.conformance` serves the fixture tools the official [MCP conformance suite](https://github.com/modelcontextprotocol/conformance) calls; `.github/workflows/conformance.yml` runs the suite for MCP 2026-07-28.

**Budgets in CI.** Run `ouncemcp check server.py`; it exits non-zero when a definition is over its fail threshold or a lint error is found.
