"""Evaluation kit (spec section 8.5).

- ``ouncemcp.eval.conformance``: the target server for the official MCP conformance
  suite, run in CI by ``.github/workflows/conformance.yml`` (EVAL-1).
- ``ouncemcp.eval.security``: the security test catalog: guessed names, cross-tenant
  access, cursor and handle reuse, replay, token passthrough and SSRF (EVAL-2).

Agent task suites, the ``eval`` command and recall in eval output (DISC-6) are M3.
"""

from ouncemcp.eval.security import (
    CrossTenant,
    CursorReuse,
    GuessedName,
    HandleReplay,
    SecurityReport,
    SSRFProbe,
    TokenPassthrough,
    run_security_suite,
)

__all__ = [
    "CrossTenant",
    "CursorReuse",
    "GuessedName",
    "HandleReplay",
    "SSRFProbe",
    "SecurityReport",
    "TokenPassthrough",
    "run_security_suite",
]
