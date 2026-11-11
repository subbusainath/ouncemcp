"""DX-6: every error and lint links to a page that exists, and docs links resolve."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ouncemcp.core.docs import DOCS_BASE, error_doc, rule_doc
from ouncemcp.core.errors import CATEGORIES, Failure
from ouncemcp.core.lints import RULES

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def local(url: str) -> Path:
    assert url.startswith(DOCS_BASE)
    return DOCS / url[len(DOCS_BASE) :]


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_error_category_has_a_page(category):
    assert local(error_doc(category)).is_file()


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_lint_rule_has_a_page(rule):
    assert local(rule_doc(rule)).is_file()


def emitted_codes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text()
        for cat, code in re.findall(r'Failure\(\s*"([a-z_]+)",\s*"([a-z_]+)"', text):
            found.add((cat, code))
        for cat, code in re.findall(
            r'category: ErrorCategory = "([a-z_]+)"\s+code = "([a-z_]+)"', text
        ):
            found.add((cat, code))
    found |= {("validation", "invalid_cursor"), ("validation", "result_too_large")}
    from ouncemcp.execution import idempotency

    for cls in (
        idempotency.WriteInProgress,
        idempotency.WriteStillRunning,
        idempotency.WriteOutcomeUnknown,
        idempotency.KeyReused,
    ):
        found.add((cls.category, str(cls.code)))
    found |= {(c, c) for c in CATEGORIES if c != "internal"}
    return found


@pytest.mark.parametrize(("category", "code"), sorted(emitted_codes()))
def test_every_error_code_is_documented_in_its_category_page(category, code):
    assert f"`{code}`" in local(error_doc(category)).read_text()


def test_error_results_carry_the_docs_link():
    meta = Failure("rate_limit", "too_many_concurrent_calls", "x").meta()
    assert meta["docs"] == error_doc("rate_limit")


LINK = re.compile(r"\]\(([^)\s]+)\)")


def markdown_files() -> list[Path]:
    return [ROOT / "README.md", *sorted(DOCS.rglob("*.md")), *sorted(ROOT.glob("examples/**/*.md"))]


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_markdown_links_resolve(path):
    broken = []
    for target in LINK.findall(path.read_text()):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (path.parent / file_part).exists():
            broken.append(target)
    assert broken == []
