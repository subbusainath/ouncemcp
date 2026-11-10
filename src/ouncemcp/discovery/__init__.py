"""Discovery modes: direct, search, and opt-in code mode, selected automatically from measured cost.

Spec: docs/spec.md section 8.1.3. Milestone: M1 (direct, search), post-1.0 (code).
"""

from ouncemcp.discovery.modes import (
    CALL_TOOL,
    DESCRIBE_TOOL,
    DETAIL_LEVELS,
    DISCOVERY_SETTINGS,
    META_TOOLS,
    SEARCH_TOOL,
    DetailLevel,
    DiscoveryDecision,
    DiscoveryMode,
    DiscoverySetting,
    decide_discovery,
)
from ouncemcp.discovery.recall import CaseRecall, RecallCase, RecallReport, build_report
from ouncemcp.discovery.search import OunceSearchTransform, render_tool

__all__ = [
    "CALL_TOOL",
    "DESCRIBE_TOOL",
    "DETAIL_LEVELS",
    "DISCOVERY_SETTINGS",
    "META_TOOLS",
    "SEARCH_TOOL",
    "CaseRecall",
    "DetailLevel",
    "DiscoveryDecision",
    "DiscoveryMode",
    "DiscoverySetting",
    "OunceSearchTransform",
    "RecallCase",
    "RecallReport",
    "build_report",
    "decide_discovery",
    "render_tool",
]
