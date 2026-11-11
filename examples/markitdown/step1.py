"""Migration step 1: the original server, unchanged, wrapped by OunceMCP.

No tool code changes. The server now gets token accounting (``ouncemcp check``,
``ouncemcp dev``), categorized and redacted errors, deadlines, request limits, traces
and automatic discovery. ``convert_to_markdown`` is declared a read.
"""

from __future__ import annotations

from original import mcp

from ouncemcp import Effect, OunceServer

server = OunceServer.wrap(mcp, effects={"convert_to_markdown": Effect.READ})

if __name__ == "__main__":
    server.run()
