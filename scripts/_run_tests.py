"""Run all pure-logic tests without pytest.

Usage:
    PYTHONPATH=src:tests python scripts/_run_tests.py
"""

from __future__ import annotations

import importlib
import inspect
import sys
import tempfile
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here / "src"))
    sys.path.insert(0, str(here / "tests"))

    mod_names = [
        "test_pure",
        "test_retrieve_pure",
        "test_eval_metrics",
        "test_wiki_pure",
        "test_m5_fixes",
        "test_m5_p1",
        "test_m5_p2",
        "test_finalization",
        "test_chaos",
        "test_abstain",
        "test_deliver",
        "test_feedback",
        "test_proactive",
        # The next three depend on a sibling deer-flow checkout and are skipped
        # in the standalone repo CI. Set DEER_FLOW_ROOT and add them back when
        # running inside the parent monorepo.
        # "test_gateway_paper_rag",
        # "test_middleware",
        # "test_langgraph_middleware",
    ]
    mods = [importlib.import_module(m) for m in mod_names]
    ok = fail = 0
    fails: list[str] = []
    for mod in mods:
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            sig = inspect.signature(fn)
            try:
                if "tmp_path" in sig.parameters:
                    with tempfile.TemporaryDirectory() as td:
                        fn(Path(td))
                else:
                    fn()
                ok += 1
            except Exception as e:
                fail += 1
                fails.append(f"FAIL {mod.__name__}.{name} | {type(e).__name__}: {str(e)[:200]}")
    for f in fails:
        print(f)
    print(f"\n{ok} pass / {fail} fail (total {ok+fail})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
