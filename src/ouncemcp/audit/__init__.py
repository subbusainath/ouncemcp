"""Optional audit command: measure any MCP server over the protocol (spec AUD-1 to AUD-13).

Never required to build or run a server. ``ouncemcp audit`` connects as a client, so it
measures servers built with OunceMCP, plain FastMCP or anything else, with the same
token accounting as ``check``:

- startup cost: total, per tool and broken down by field, plus resources and prompts
  (AUD-3, AUD-4), counted with a named tokenizer (AUD-6);
- schema bloat lints (AUD-5);
- discovery latency and connection time (AUD-8);
- a discovery-mode recommendation with its startup cost (AUD-9);
- terminal, JSON and Markdown reports (AUD-10), budget thresholds with exit codes for CI
  (AUD-11), and comparison with an earlier JSON report (AUD-13).

By default it only lists and discovers. It calls a tool only for an explicit
``--sample tool='{"arg": 1}'`` (AUD-12), to measure result sizes (AUD-7).
"""

from ouncemcp.audit.runner import AuditReport, BloatFinding, audit, compare, connect_target

__all__ = ["AuditReport", "BloatFinding", "audit", "compare", "connect_target"]
