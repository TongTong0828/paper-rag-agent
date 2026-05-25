"""Walk all paper_rag.* modules and report importability.

Usage:
    PYTHONPATH=src python scripts/_run_smoke.py
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import paper_rag

    ok = fail = 0
    fails: list[tuple[str, str, str]] = []
    for mi in pkgutil.walk_packages(paper_rag.__path__, prefix="paper_rag."):
        try:
            importlib.import_module(mi.name)
            ok += 1
        except Exception as e:
            fail += 1
            fails.append((mi.name, type(e).__name__, str(e)[:100]))
    for name, cls, msg in fails:
        print(f"FAIL {name} | {cls}: {msg}")
    print(f"\n{ok} ok / {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
