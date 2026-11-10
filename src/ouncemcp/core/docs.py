"""Documentation links for every error category and lint rule (spec DX-6).

Error results carry ``_meta.ouncemcp.docs`` and every ``check`` finding names its page,
so whoever reads an error or a lint can find out what caused it and how to fix it. The
link check in ``tests/test_dx6_doc_links.py`` keeps each target page present.
"""

from __future__ import annotations

DOCS_BASE = "https://github.com/subbusainath/ouncemcp/blob/main/docs/"


def error_doc(category: str) -> str:
    """The page that explains an error category and its codes."""
    return f"{DOCS_BASE}errors/{category}.md"


def rule_doc(rule: str) -> str:
    """The page that explains a ``check`` rule."""
    return f"{DOCS_BASE}rules/{rule}.md"
