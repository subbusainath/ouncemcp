import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "inventory"
sys.path.insert(0, str(EXAMPLE))


@pytest.fixture
def anyio_backend():
    return "asyncio"
