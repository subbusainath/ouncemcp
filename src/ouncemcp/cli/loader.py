"""Load a server object from ``path/to/file.py[:attribute]`` or ``package.module[:attribute]``."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from ouncemcp.core.server import OunceServer


class LoadError(Exception):
    pass


def load_server(target: str) -> OunceServer | FastMCP:
    """Import ``target`` and return the OunceServer or FastMCP instance it names.

    Without ``:attribute``, the first OunceServer (or else FastMCP) instance in the
    module is used.
    """
    ref, _, attr = target.partition(":")
    if ref.endswith(".py") or Path(ref).exists():
        path = Path(ref).resolve()
        if not path.exists():
            raise LoadError(f"File not found: {ref}")
        sys.path.insert(0, str(path.parent))
        module_name = f"_ouncemcp_target_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise LoadError(f"Cannot import {ref}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(ref)

    if attr:
        obj: Any = getattr(module, attr, None)
        if not isinstance(obj, (OunceServer, FastMCP)):
            raise LoadError(f"'{attr}' in {ref} is not an OunceServer or FastMCP server")
        return obj
    values = list(vars(module).values())
    for kind in (OunceServer, FastMCP):
        for value in values:
            if isinstance(value, kind):
                return value
    raise LoadError(f"No OunceServer or FastMCP server found in {ref}; use {ref}:<name>")
