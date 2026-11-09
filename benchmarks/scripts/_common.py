"""Shared helpers for M0 benchmark scripts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "inventory"
RESULTS = ROOT / "benchmarks" / "results"
if str(EXAMPLE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE))
