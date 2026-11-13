"""State checks for the inventory eval suite: judged on what the tools returned."""

from __future__ import annotations

from typing import Any


def _ids(run: Any, tool: str) -> list[str]:
    ids: list[str] = []
    for call in run.calls:
        if call.tool == tool and call.ok and call.structured:
            ids += [item["id"] for item in call.structured.get("items", [])]
    return ids


def every_database_once(server: Any, run: Any) -> bool:
    """Paging through us-east-1 databases returned all 20, none twice."""
    ids = _ids(run, "inventory.search")
    return len(ids) == 20 and len(set(ids)) == 20 and all(i.startswith("da-") for i in ids)


def degraded_lbs_found(server: Any, run: Any) -> bool:
    """Every degraded load balancer in eu-west-1 was returned."""
    return {"lo-00034", "lo-00134", "lo-00234"} <= set(_ids(run, "inventory.search"))
