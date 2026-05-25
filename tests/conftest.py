"""pytest configuration — make `paper_rag` and tests/ shared modules importable.

This file lives at the test-suite root so pytest auto-discovers it.
We also expose a tmp_path-style fixture in case any test wants it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Insert in front so editable installs / system-wide installs do not shadow
# the working tree.
for sub in ("src", "tests"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
