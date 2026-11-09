"""Context budgets (spec section 8.1.2).

The defaults are proposed starting points from the research and must be
validated in the benchmark phase before being presented as recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Token limits for tool definitions and results.

    Attributes:
        tool_definition_target: Ideal upper size for one tool definition.
        tool_definition_warn: A tool definition above this size produces a warning.
        tool_definition_fail: A tool definition above this size fails ``ouncemcp check``.
        startup_tokens: Total definition tokens above which the server warns
            (and, from M1, switches to search-based discovery).
        result_tokens: Default maximum tokens for a concise result.
        result_ceiling: Hard maximum for any result, including detailed ones.
    """

    tool_definition_target: int = 250
    tool_definition_warn: int = 500
    tool_definition_fail: int = 1_000
    startup_tokens: int = 4_000
    result_tokens: int = 2_000
    result_ceiling: int = 8_000

    def __post_init__(self) -> None:
        for name in (
            "tool_definition_target",
            "tool_definition_warn",
            "tool_definition_fail",
            "startup_tokens",
            "result_tokens",
            "result_ceiling",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"Budget.{name} must be a positive integer, got {value!r}")
        if not (
            self.tool_definition_target <= self.tool_definition_warn <= self.tool_definition_fail
        ):
            raise ValueError(
                "Budget requires tool_definition_target <= tool_definition_warn "
                "<= tool_definition_fail"
            )
        if self.result_tokens > self.result_ceiling:
            raise ValueError("Budget.result_tokens must not exceed Budget.result_ceiling")
