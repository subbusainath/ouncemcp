"""The converter both versions use: markitdown's ``MarkItDown`` when it is installed.

Tests pass a fake with the same ``convert_uri(uri) -> object with .markdown`` shape, so
the pilot runs without markitdown and its optional dependencies.
"""

from __future__ import annotations

from typing import Any


def default_converter() -> Any:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "Install markitdown to run this example: pip install 'markitdown[all]'"
        ) from exc
    return MarkItDown()


class LazyConverter:
    """Creates ``MarkItDown`` on first use, so importing the server needs no markitdown."""

    def __init__(self) -> None:
        self._converter: Any = None

    def convert_uri(self, uri: str) -> Any:
        if self._converter is None:
            self._converter = default_converter()
        return self._converter.convert_uri(uri)
