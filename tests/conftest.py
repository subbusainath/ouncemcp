import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "inventory"
sys.path.insert(0, str(EXAMPLE))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """``load_server`` (used by check, audit and dev) puts the server's folder first on
    ``sys.path`` so the server can import its siblings. Undo that after each test, so
    example modules with the same name (``server.py``) cannot shadow each other."""
    saved = list(sys.path)
    yield
    sys.path[:] = saved
