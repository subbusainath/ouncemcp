"""Search recall (DISC-6): does the search surface still find the tools agents need?

A ``RecallCase`` pairs a task phrased the way an agent might ask with the tools that
should come back. ``measure_recall`` ranks the whole registered catalog with the same
index ``search_tools`` uses and reports, per case and overall, how many expected tools
appear in the top ``k``. It does not depend on the eval kit, which will call it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecallCase:
    """One search task: ``query`` should surface every tool in ``expected``."""

    query: str
    expected: frozenset[str] | set[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", frozenset(self.expected))


@dataclass(frozen=True)
class CaseRecall:
    query: str
    expected: tuple[str, ...]
    returned: tuple[str, ...]
    found: tuple[str, ...]
    missed: tuple[str, ...]

    @property
    def recall(self) -> float:
        return len(self.found) / len(self.expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "expected": list(self.expected),
            "returned": list(self.returned),
            "found": list(self.found),
            "missed": list(self.missed),
            "recall": round(self.recall, 4),
        }


@dataclass(frozen=True)
class RecallReport:
    """Recall at ``k`` over a set of cases."""

    k: int
    cases: tuple[CaseRecall, ...]

    @property
    def mean_recall(self) -> float:
        return sum(case.recall for case in self.cases) / len(self.cases)

    @property
    def missed_tools(self) -> list[str]:
        return sorted({name for case in self.cases for name in case.missed})

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "mean_recall": round(self.mean_recall, 4),
            "missed_tools": self.missed_tools,
            "cases": [case.to_dict() for case in self.cases],
        }

    def render_text(self) -> str:
        lines = [f"Search recall at k={self.k}: mean recall {self.mean_recall:.0%}"]
        for case in self.cases:
            status = "ok" if not case.missed else "missed " + ", ".join(case.missed)
            lines.append(f"  {case.recall:>4.0%}  {case.query!r}  {status}")
        return "\n".join(lines)


def build_report(
    cases: Sequence[RecallCase], rankings: Iterable[Sequence[str]], k: int
) -> RecallReport:
    results = []
    for case, ranked in zip(cases, rankings, strict=True):
        returned = tuple(ranked[:k])
        expected = tuple(sorted(case.expected))
        results.append(
            CaseRecall(
                query=case.query,
                expected=expected,
                returned=returned,
                found=tuple(name for name in expected if name in returned),
                missed=tuple(name for name in expected if name not in returned),
            )
        )
    return RecallReport(k=k, cases=tuple(results))
