"""Agent task suites in YAML (spec EVAL-3, EVAL-6, DISC-6; proposal 0019).

```yaml
name: inventory
server: server.py                 # relative to this file; or a URL, or "-- command args"
repeats: 3
tasks:
  - id: running-vms
    split: train                  # train (default) or heldout
    prompt: List running VMs in ap-south-1.
    expect_tools: [inventory.search]
    checks:
      - called: inventory.search
        args: {region: ap-south-1}
      - result: inventory.search
        path: items.0.id
        equals: vm-00040
    script:                       # what the replay model does
      - call: inventory.search
        args: {region: ap-south-1, kind: vm, status: running}
      - say: There are 12 running VMs.
recall:                           # DISC-6, for servers with search
  - query: running virtual machines
    expect: [inventory.search]
```

Checks are judged on what happened on the server, not on what the model says:

| Check | Passes when |
| --- | --- |
| ``called: tool`` (+ ``args``) | the tool, listed by the server, was called with at least these argument values |
| ``not_called: tool`` | the tool was never called |
| ``result: tool`` + ``path`` + ``equals``/``contains``/``exists`` | the tool's last successful structured result matches |
| ``error: tool`` + ``category`` (+ ``hidden: true``) | the tool's last call failed in that category; the tool must be listed for the caller, or, with ``hidden: true``, must be hidden from them |
| ``state: module:function`` | ``function(server, run)`` returns true; the module path is relative to the suite |

A task may also set ``caller`` (fields for ``ouncemcp.testing.make_token``, in-process
servers only), ``approve`` (the answer to an approval request; default true) and
``max_turns`` (default 8).
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Split = Literal["train", "heldout"]
CHECK_KINDS = ("called", "not_called", "result", "error", "state")


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Check:
    kind: str
    tool: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)
    path: str | None = None
    equals: Any = None
    contains: Any = None
    exists: bool | None = None
    category: str | None = None
    hidden: bool = False
    function: Callable[..., Any] | None = None
    label: str = ""


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    split: Split = "train"
    expect_tools: tuple[str, ...] = ()
    checks: tuple[Check, ...] = ()
    script: tuple[Mapping[str, Any], ...] = ()
    caller: Mapping[str, Any] | None = None
    approve: bool = True
    max_turns: int = 8


@dataclass(frozen=True)
class RecallItem:
    query: str
    expect: tuple[str, ...]


@dataclass(frozen=True)
class Suite:
    name: str
    server: str
    tasks: tuple[Task, ...]
    repeats: int = 1
    recall: tuple[RecallItem, ...] = ()
    path: Path | None = None
    digest: str = ""

    def scripts(self) -> dict[str, list[Mapping[str, Any]]]:
        return {t.id: list(t.script) for t in self.tasks}

    def select(self, split: str) -> tuple[Task, ...]:
        if split == "all":
            return self.tasks
        return tuple(t for t in self.tasks if t.split == split)


def _function(ref: str, base: Path) -> Callable[..., Any]:
    module_ref, _, attr = ref.partition(":")
    if not attr:
        raise SuiteError(
            f"state check '{ref}' must look like 'file.py:function' or 'module:function'"
        )
    if module_ref.endswith(".py"):
        file = (base / module_ref).resolve()
        spec = importlib.util.spec_from_file_location(f"_ouncemcp_eval_{file.stem}", file)
        if spec is None or spec.loader is None:
            raise SuiteError(f"cannot load {file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_ref)
    function = getattr(module, attr, None)
    if not callable(function):
        raise SuiteError(f"'{attr}' in {module_ref} is not a function")
    return function  # type: ignore[no-any-return]


def _check(raw: Mapping[str, Any], base: Path, where: str) -> Check:
    kinds = [k for k in CHECK_KINDS if k in raw]
    if len(kinds) != 1:
        raise SuiteError(f"{where}: a check needs exactly one of {', '.join(CHECK_KINDS)}")
    kind = kinds[0]
    if kind == "state":
        return Check(kind, function=_function(str(raw["state"]), base), label=str(raw["state"]))
    tool = str(raw[kind])
    if kind == "result" and not any(k in raw for k in ("equals", "contains", "exists")):
        raise SuiteError(f"{where}: a result check needs equals, contains or exists")
    if kind == "error" and "category" not in raw:
        raise SuiteError(f"{where}: an error check needs a category")
    return Check(
        kind,
        tool=tool,
        args=dict(raw.get("args") or {}),
        path=str(raw["path"]) if "path" in raw else None,
        equals=raw.get("equals"),
        contains=raw.get("contains"),
        exists=raw.get("exists"),
        category=raw.get("category"),
        hidden=bool(raw.get("hidden", False)),
        label=f"{kind} {tool}" + (f" {raw['path']}" if "path" in raw else ""),
    )


def load_suite(path: str | Path) -> Suite:
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except FileNotFoundError as exc:
        raise SuiteError(f"{file}: no such file") from exc
    except yaml.YAMLError as exc:
        raise SuiteError(f"{file}: {exc}") from exc
    if not isinstance(data, dict):
        raise SuiteError(f"{file}: a suite is a mapping with name, server and tasks")
    for key in ("name", "server", "tasks"):
        if key not in data:
            raise SuiteError(f"{file}: missing '{key}'")
    base = file.resolve().parent
    tasks: list[Task] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["tasks"] or []):
        where = f"{file}: task {i + 1}"
        if not isinstance(raw, dict) or "id" not in raw or "prompt" not in raw:
            raise SuiteError(f"{where}: needs id and prompt")
        task_id = str(raw["id"])
        if task_id in seen:
            raise SuiteError(f"{where}: duplicate id '{task_id}'")
        seen.add(task_id)
        split = raw.get("split", "train")
        if split not in ("train", "heldout"):
            raise SuiteError(f"{where}: split must be train or heldout")
        tasks.append(
            Task(
                id=task_id,
                prompt=str(raw["prompt"]),
                split=split,
                expect_tools=tuple(raw.get("expect_tools") or ()),
                checks=tuple(
                    _check(c, base, f"{where} ({task_id})") for c in raw.get("checks") or ()
                ),
                script=tuple(raw.get("script") or ()),
                caller=raw.get("caller"),
                approve=bool(raw.get("approve", True)),
                max_turns=int(raw.get("max_turns", 8)),
            )
        )
    if not tasks:
        raise SuiteError(f"{file}: no tasks")
    recall = tuple(
        RecallItem(str(r["query"]), tuple(r["expect"])) for r in data.get("recall") or ()
    )
    server = str(data["server"])
    if not (server.startswith(("http://", "https://", "-- "))):
        ref, colon, attr = server.partition(":")
        server = str((base / ref).resolve()) + (colon + attr if colon else "")
    return Suite(
        name=str(data["name"]),
        server=server,
        tasks=tuple(tasks),
        repeats=int(data.get("repeats", 1)),
        recall=recall,
        path=file,
        digest=hashlib.sha256(text.encode()).hexdigest()[:12],
    )


def lookup(value: Any, path: str | None) -> tuple[bool, Any]:
    """Follow a dotted path (``items.0.id``) into JSON data."""
    if not path:
        return True, value
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, str) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def subset(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    return all(k in actual and actual[k] == v for k, v in expected.items())
