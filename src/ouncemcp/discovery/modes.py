"""Discovery modes and the automatic choice between them (spec DISC-1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

DiscoveryMode = Literal["direct", "search"]
DiscoverySetting = Literal["auto", "direct", "search"]
DetailLevel = Literal["name", "summary", "full"]

DISCOVERY_SETTINGS: tuple[DiscoverySetting, ...] = ("auto", "direct", "search")
DETAIL_LEVELS: tuple[DetailLevel, ...] = ("name", "summary", "full")

SEARCH_TOOL = "search_tools"
DESCRIBE_TOOL = "describe_tool"
CALL_TOOL = "call_tool"
META_TOOLS = (SEARCH_TOOL, DESCRIBE_TOOL, CALL_TOOL)


@dataclass(frozen=True)
class DiscoveryDecision:
    """Which discovery mode a server uses, and the measurements behind the choice.

    Attributes:
        setting: What the developer asked for: ``auto``, ``direct`` or ``search``.
        mode: What the server does: ``direct`` or ``search``.
        direct_tokens: Definition tokens of every registered tool, as ``direct`` mode
            would send them at startup.
        search_tokens: Definition tokens of the three meta-tools plus pinned tools, as
            ``search`` mode sends them at startup.
        threshold: The startup budget (``Budget.startup_tokens``) that ``auto`` compares
            ``direct_tokens`` with.
        pinned: Tools listed in ``search`` mode alongside the meta-tools.
        reason: One sentence explaining the choice, for logs and ``check``.
    """

    setting: DiscoverySetting
    mode: DiscoveryMode
    direct_tokens: int
    search_tokens: int
    threshold: int
    pinned: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "setting": self.setting,
            "mode": self.mode,
            "direct_tokens": self.direct_tokens,
            "search_tokens": self.search_tokens,
            "threshold": self.threshold,
            "pinned": list(self.pinned),
            "reason": self.reason,
        }

    def render_text(self) -> str:
        return f"Discovery        {self.mode} ({self.reason})"


def decide_discovery(
    setting: DiscoverySetting,
    *,
    direct_tokens: int,
    search_tokens: int,
    threshold: int,
    pinned: Sequence[str] = (),
) -> DiscoveryDecision:
    """Choose a mode. ``auto`` switches to ``search`` when ``direct`` costs more than
    ``threshold`` tokens and ``search`` is actually smaller; an explicit setting wins."""

    def decision(mode: DiscoveryMode, reason: str) -> DiscoveryDecision:
        return DiscoveryDecision(
            setting=setting,
            mode=mode,
            direct_tokens=direct_tokens,
            search_tokens=search_tokens,
            threshold=threshold,
            pinned=tuple(pinned),
            reason=reason,
        )

    if setting == "direct":
        return decision("direct", f"set explicitly; direct costs {direct_tokens:,} tokens")
    if setting == "search":
        return decision("search", f"set explicitly; search costs {search_tokens:,} tokens")
    if direct_tokens <= threshold:
        return decision(
            "direct",
            f"direct costs {direct_tokens:,} tokens, within the {threshold:,}-token threshold",
        )
    if search_tokens >= direct_tokens:
        return decision(
            "direct",
            f"direct costs {direct_tokens:,} tokens, over the {threshold:,}-token threshold, "
            f"but search ({search_tokens:,}) is not smaller",
        )
    return decision(
        "search",
        f"direct would cost {direct_tokens:,} tokens, over the {threshold:,}-token threshold; "
        f"search costs {search_tokens:,}",
    )
