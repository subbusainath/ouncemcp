"""Definition cost measurement (spec BUD-1, and the basis for AUD-3 and AUD-4).

Definition cost is measured on the compact JSON of each tool's ``name``,
``description``, ``inputSchema``, and ``outputSchema``, exactly as they appear
on the wire.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ouncemcp.core.budget import Budget
from ouncemcp.core.tokens import TokenCounter, get_counter

Status = Literal["ok", "warn", "fail"]

DEFINITION_KEYS = ("name", "description", "inputSchema", "outputSchema")

# BUD-7: the fields a definition's token cost is measured on, and the ones that are not.
COUNTED_FIELDS = DEFINITION_KEYS
UNCOUNTED_FIELDS = ("title", "annotations", "_meta")


def counting_scope() -> dict[str, Any]:
    """What a definition's token count includes, for reports (spec BUD-7)."""
    return {
        "counted": list(COUNTED_FIELDS),
        "not_counted": list(UNCOUNTED_FIELDS),
        "other_fields": "not counted",
        "measured_on": (
            "compact JSON of each tool definition as an MCP client receives it in "
            "tools/list, fields in wire order"
        ),
        "verified_hosts": [],
        "note": (
            "Hosts may show the model more or fewer fields than these. Which fields reach "
            "the model has not been verified for any host yet, so counts are a consistent "
            "estimate, not an exact host cost."
        ),
    }


def definition_payload(tool: Any) -> dict[str, Any]:
    """Return the model-relevant part of a tool definition as a wire-format dict.

    Accepts an MCP SDK ``Tool``, a FastMCP tool (anything with ``to_mcp_tool``),
    or an already-serialized dict.
    """
    if hasattr(tool, "to_mcp_tool"):
        tool = tool.to_mcp_tool()
    if hasattr(tool, "model_dump"):
        data = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    elif isinstance(tool, dict):
        data = tool
    else:  # pragma: no cover - defensive
        raise TypeError(f"Cannot measure tool definition of type {type(tool).__name__}")
    return {key: data[key] for key in DEFINITION_KEYS if data.get(key) is not None}


@dataclass(frozen=True)
class ToolCost:
    """Token cost of one tool definition, with a breakdown that sums to ``total``."""

    name: str
    total: int
    name_tokens: int
    description: int
    input_schema: int
    output_schema: int
    structure: int
    status: Status
    output_schema_advertised: bool = False
    output_schema_mode: str | None = None
    output_schema_cost: int = 0


@dataclass(frozen=True)
class ServerCost:
    """Token cost of everything a server exposes before the user's first message."""

    tokenizer: str
    budget: Budget
    tools: list[ToolCost] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(tool.total for tool in self.tools)

    @property
    def output_schema_tokens(self) -> int:
        """Definition tokens spent on advertised output schemas (BUD-9): the schema and
        its key, which is what ``output_schemas="off"`` would save."""
        return sum(tool.output_schema_cost for tool in self.tools)

    @property
    def output_schema_share(self) -> float:
        """Output schemas' share of all definition tokens, 0.0 to 1.0, to 4 places."""
        return round(self.output_schema_tokens / self.total, 4) if self.total else 0.0

    @property
    def startup_status(self) -> Literal["ok", "warn"]:
        return "warn" if self.total > self.budget.startup_tokens else "ok"

    @property
    def failed(self) -> bool:
        return any(tool.status == "fail" for tool in self.tools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokenizer": self.tokenizer,
            "budget": asdict(self.budget),
            "startup_tokens": self.total,
            "startup_status": self.startup_status,
            "output_schema_tokens": self.output_schema_tokens,
            "output_schema_share": self.output_schema_share,
            "counting_scope": counting_scope(),
            "tool_count": len(self.tools),
            "tools": [asdict(tool) for tool in self.tools],
        }

    def render_text(self) -> str:
        rows = sorted(self.tools, key=lambda t: t.total, reverse=True)
        width = max([len(t.name) for t in rows] + [4])
        lines = [
            f"Tokenizer        {self.tokenizer}",
            (
                f"Startup context  {self.total:,} tokens across {len(self.tools)} tools"
                f"  [{self.startup_status.upper()}, budget {self.budget.startup_tokens:,}]"
            ),
            (
                f"Output schemas   {self.output_schema_tokens:,} of {self.total:,} tokens "
                f"({round(self.output_schema_share * 100)}%)"
            ),
            f"Counted          {', '.join(COUNTED_FIELDS)}",
            f"Not counted      {', '.join(UNCOUNTED_FIELDS)}",
            "",
            (
                f"{'Tool'.ljust(width)}  {'Total':>6}  {'Desc':>5}  {'Input':>5}  "
                f"{'Output':>6}  {'Other':>5}  Status"
            ),
        ]
        for t in rows:
            lines.append(
                f"{t.name.ljust(width)}  {t.total:>6}  {t.description:>5}  "
                f"{t.input_schema:>5}  {t.output_schema:>6}  "
                f"{t.name_tokens + t.structure:>5}  {t.status.upper()}"
            )
        lines.append("")
        lines.append(
            f"Per-tool budget: target {self.budget.tool_definition_target}, "
            f"warn {self.budget.tool_definition_warn}, fail {self.budget.tool_definition_fail}"
        )
        return "\n".join(lines)


def _status(total: int, budget: Budget) -> Status:
    if total > budget.tool_definition_fail:
        return "fail"
    if total > budget.tool_definition_warn:
        return "warn"
    return "ok"


def measure_tool(
    tool: Any,
    budget: Budget,
    counter: TokenCounter | None = None,
    output_schema_mode: str | None = None,
) -> ToolCost:
    """Measure one tool definition. ``output_schema_mode`` is recorded in the result."""
    counter = counter or get_counter()
    payload = definition_payload(tool)
    total = counter.count_json(payload)
    name_tokens = counter.count(payload.get("name", ""))
    description = counter.count(payload.get("description", ""))
    input_schema = counter.count_json(payload["inputSchema"]) if "inputSchema" in payload else 0
    output_schema = counter.count_json(payload["outputSchema"]) if "outputSchema" in payload else 0
    structure = total - name_tokens - description - input_schema - output_schema
    without_output = {k: v for k, v in payload.items() if k != "outputSchema"}
    output_schema_cost = total - counter.count_json(without_output) if output_schema else 0
    return ToolCost(
        name=payload.get("name", "?"),
        total=total,
        name_tokens=name_tokens,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        structure=structure,
        status=_status(total, budget),
        output_schema_advertised="outputSchema" in payload,
        output_schema_mode=output_schema_mode,
        output_schema_cost=output_schema_cost,
    )


def measure_tools(
    tools: Iterable[Any],
    budget: Budget,
    counter: TokenCounter | None = None,
    output_schema_modes: Mapping[str, str] | None = None,
) -> ServerCost:
    """Measure a set of tool definitions.

    ``output_schema_modes`` maps tool names to the output-schema mode each was
    registered with (``on``, ``off`` or ``auto``), for servers built with OunceMCP.
    """
    modes = output_schema_modes or {}
    counter = counter or get_counter()
    costs = []
    for tool in tools:
        name = definition_payload(tool).get("name", "")
        costs.append(measure_tool(tool, budget, counter, modes.get(name)))
    return ServerCost(tokenizer=counter.name, budget=budget, tools=costs)
