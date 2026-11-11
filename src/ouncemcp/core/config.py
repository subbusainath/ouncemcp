"""``ouncemcp.toml``: server settings in one file (proposal 0017).

``OunceServer.from_config("inventory", "ouncemcp.toml")`` reads budgets, limits,
discovery, tokenizer, output-schema and security settings from the file; keyword
arguments override it. Unknown sections and keys are errors, so a typo cannot silently
leave a default in place.

```toml
[budget]
tool_definition_warn = 500
tool_definition_fail = 1000
startup_tokens = 4000        # startup_warn also accepted
result_tokens = 2000         # result_default also accepted
result_ceiling = 8000

[limits]
default_deadline_ms = 30000
max_concurrency = 8

[discovery]
mode = "auto"                # auto | direct | search
pinned = ["inventory.search"]

[tokenizer]
default = "o200k_base"

[output]
schemas = "on"               # on | off | auto

[security]
http_bind = "127.0.0.1"
stdio_policy = "trust"       # trust | enforce
audience = "https://mcp.example.com"
tenant_claims = ["tenant", "org_id"]
```
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ouncemcp.core.budget import Budget
from ouncemcp.execution.limits import Limits

_BUDGET_ALIASES = {"startup_warn": "startup_tokens", "result_default": "result_tokens"}
_BUDGET_KEYS = set(Budget.__dataclass_fields__) | set(_BUDGET_ALIASES)
_LIMIT_KEYS = set(Limits.__dataclass_fields__)
_SECTIONS: dict[str, set[str]] = {
    "budget": _BUDGET_KEYS,
    "limits": _LIMIT_KEYS,
    "discovery": {"mode", "pinned", "search_results"},
    "tokenizer": {"default"},
    "output": {"schemas"},
    "security": {"http_bind", "stdio_policy", "audience", "tenant_claims"},
}


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> dict[str, Any]:
    """Read ``path`` and return ``OunceServer`` keyword arguments."""
    file = Path(path)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{file}: no such file") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file}: {exc}") from exc
    for section, values in data.items():
        if section not in _SECTIONS:
            raise ConfigError(
                f"{file}: unknown section [{section}]; use {', '.join(sorted(_SECTIONS))}"
            )
        if not isinstance(values, dict):
            raise ConfigError(f"{file}: [{section}] must be a table")
        unknown = sorted(set(values) - _SECTIONS[section])
        if unknown:
            raise ConfigError(
                f"{file}: unknown key(s) in [{section}]: {', '.join(unknown)}; allowed: "
                f"{', '.join(sorted(_SECTIONS[section]))}"
            )
    kwargs: dict[str, Any] = {}
    if "budget" in data:
        values = {_BUDGET_ALIASES.get(k, k): v for k, v in data["budget"].items()}
        kwargs["budget"] = Budget(**values)
    if "limits" in data:
        kwargs["limits"] = Limits(**data["limits"])
    discovery = data.get("discovery", {})
    if "mode" in discovery:
        kwargs["discovery"] = discovery["mode"]
    if "pinned" in discovery:
        kwargs["pinned"] = list(discovery["pinned"])
    if "search_results" in discovery:
        kwargs["search_results"] = discovery["search_results"]
    if "default" in data.get("tokenizer", {}):
        kwargs["tokenizer"] = data["tokenizer"]["default"]
    if "schemas" in data.get("output", {}):
        kwargs["output_schemas"] = data["output"]["schemas"]
    security = data.get("security", {})
    for key, arg in (
        ("http_bind", "http_host"),
        ("stdio_policy", "stdio_policy"),
        ("audience", "audience"),
        ("tenant_claims", "tenant_claims"),
    ):
        if key in security:
            kwargs[arg] = security[key]
    return kwargs
