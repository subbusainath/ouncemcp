"""The public API, recorded (preparing the 1.0 API freeze, spec M4).

Every exported name of the public modules and the signature of every public function,
class constructor and method is compared with ``tests/api_snapshot.json``. A change to
the public API fails this test until the snapshot is updated, so API changes are always
deliberate and visible in review. Update it with::

    OUNCEMCP_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_surface.py
"""

from __future__ import annotations

import enum
import importlib
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any

SNAPSHOT = Path(__file__).with_name("api_snapshot.json")
PUBLIC_MODULES = (
    "ouncemcp",
    "ouncemcp.security",
    "ouncemcp.client",
    "ouncemcp.trace",
    "ouncemcp.eval",
    "ouncemcp.eval.agent",
    "ouncemcp.testing",
    "ouncemcp.ui",
)


def _signature(obj: Any) -> str:
    try:
        text = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(?)"
    return re.sub(r" at 0x[0-9a-f]+", "", text)  # default values printed with an address


def _describe(obj: Any) -> Any:
    if inspect.isclass(obj) and issubclass(obj, enum.Enum):
        # Enum constructor signatures differ between Python versions; the values are the API.
        return {"kind": "enum", "values": sorted(str(m.value) for m in obj)}
    if inspect.isclass(obj):
        members = {}
        for name, member in sorted(vars(obj).items()):
            if name.startswith("_") and name != "__init__":
                continue
            target = member.__func__ if isinstance(member, (classmethod, staticmethod)) else member
            if inspect.isfunction(target):
                members[name] = _signature(target)
            elif isinstance(member, property):
                members[name] = "property"
        return {"kind": "class", "init": _signature(obj), "members": members}
    if callable(obj):
        return {"kind": "function", "signature": _signature(obj)}
    return {"kind": type(obj).__name__}


def surface() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        names = getattr(module, "__all__", None)
        if names is None:
            names = [
                n for n in vars(module) if not n.startswith("_") and callable(getattr(module, n))
            ]
            names = [
                n
                for n in names
                if getattr(getattr(module, n), "__module__", "").startswith("ouncemcp")
            ]
        out[module_name] = {
            name: _describe(getattr(module, name))
            for name in sorted(names)
            if name != "__version__"
        }
    return out


def test_public_api_matches_the_snapshot():
    current = surface()
    if os.environ.get("OUNCEMCP_UPDATE_API_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    recorded = json.loads(SNAPSHOT.read_text())
    changes = []
    for module in sorted(set(recorded) | set(current)):
        old, new = recorded.get(module, {}), current.get(module, {})
        changes += [f"{module}.{n}: removed" for n in sorted(set(old) - set(new))]
        changes += [f"{module}.{n}: added" for n in sorted(set(new) - set(old))]
        changes += [
            f"{module}.{n}: changed" for n in sorted(set(old) & set(new)) if old[n] != new[n]
        ]
    assert not changes, (
        "The public API changed. If that is intended, update the snapshot "
        "(OUNCEMCP_UPDATE_API_SNAPSHOT=1) and describe the change in the PR:\n  "
        + "\n  ".join(changes)
    )
