#!/usr/bin/env python3
"""Abstain auto-calibration with human-in-the-loop review (M11 / ADR-0017).

Runs the existing ``calibrate_abstain.py`` against the union of:
  - tests/eval/qa_set.real.jsonl   (curated static set)
  - tests/eval/hard_cases.jsonl    (auto-collected from feedback events)

Compares the recommended thresholds with the live config; if they differ
beyond a configurable margin, emits a JSON ready for a GitHub PR. Does NOT
edit default.yaml directly — that's the human reviewer's job (ADR-0017
decision 3).

Usage
-----
    python -m paper_rag.scripts.abstain_autocalibrate \\
        --hard-cases tests/eval/hard_cases.jsonl \\
        --diff-threshold 0.05 \\
        --out data/index/abstain_recommendation.json

Exit codes
----------
    0 = thresholds within tolerance, no action required
    1 = thresholds drift > tolerance, recommendation written
    2 = error (no eval data, calibrate failed)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_current_thresholds() -> tuple[float, float]:
    """Read live thresholds from paper_rag config."""
    from paper_rag import config as cfg

    a = cfg.load().rag.abstain
    return float(a.threshold_low), float(a.threshold_high)


def _merge_qa_sets(curated: Path, hard_cases: Path, merged_out: Path) -> int:
    """Combine static + hard_cases into one jsonl. Returns count."""
    n = 0
    seen_qids: set[str] = set()
    merged_out.parent.mkdir(parents=True, exist_ok=True)
    with merged_out.open("w", encoding="utf-8") as f:
        for src in (curated, hard_cases):
            if not src.exists():
                continue
            with src.open("r", encoding="utf-8") as g:
                for line in g:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    qid = item.get("qid")
                    if not qid or qid in seen_qids:
                        continue
                    seen_qids.add(qid)
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    n += 1
    return n


def _run_calibrate(qa_set: Path, out: Path, top_k: int = 8) -> dict:
    """Invoke the existing calibrate_abstain.py routine in-process."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib

    if "calibrate_abstain" in sys.modules:
        importlib.reload(sys.modules["calibrate_abstain"])
    cab = importlib.import_module("calibrate_abstain")

    items = cab._load_qa_set(qa_set)
    if not items:
        raise RuntimeError(f"empty merged qa_set at {qa_set}")
    rows = cab._gather_scores(items, top_k=top_k)
    rec = cab._pick_thresholds(rows, target_fpr=0.0)

    payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "qa_set": str(qa_set),
        "n_items": len(items),
        "n_negatives": sum(1 for it in items if cab._is_negative(it)),
        "recommendation": rec,
        "rows": rows,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return rec


def _compare(current: tuple[float, float], rec: dict, diff_thresh: float) -> dict:
    """Return decision payload."""
    cur_low, cur_high = current
    new_low = float(rec["threshold_low"])
    new_high = float(rec["threshold_high"])
    drift_low = abs(new_low - cur_low)
    drift_high = abs(new_high - cur_high)
    needs_pr = drift_low > diff_thresh or drift_high > diff_thresh
    return {
        "current": {"low": cur_low, "high": cur_high},
        "recommended": {"low": new_low, "high": new_high},
        "drift": {"low": round(drift_low, 4), "high": round(drift_high, 4)},
        "diff_threshold": diff_thresh,
        "needs_pr": needs_pr,
        "stats": rec.get("stats"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--curated", default="tests/eval/qa_set.real.jsonl")
    ap.add_argument("--hard-cases", default="tests/eval/hard_cases.jsonl")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--diff-threshold", type=float, default=0.05)
    ap.add_argument("--out", default="data/index/abstain_recommendation.json")
    args = ap.parse_args()

    curated = (ROOT / args.curated) if not Path(args.curated).is_absolute() else Path(args.curated)
    hard_cases = (ROOT / args.hard_cases) if not Path(args.hard_cases).is_absolute() else Path(args.hard_cases)
    out_path = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)

    if not curated.exists():
        print(f"❌ curated qa_set not found: {curated}", file=sys.stderr)
        return 2

    merged_path = out_path.parent / "abstain_merged_qa.jsonl"
    n = _merge_qa_sets(curated, hard_cases, merged_path)
    if n == 0:
        print("❌ no qa items after merge", file=sys.stderr)
        return 2
    print(f"Merged qa_set: {n} items at {merged_path}")

    print("Running calibration (this calls retrieve once per question)...")
    t0 = time.time()
    rec = _run_calibrate(merged_path, out_path, top_k=args.top_k)
    elapsed = time.time() - t0
    print(f"Calibration done in {elapsed:.1f}s")
    print(f"  threshold_low  = {rec['threshold_low']}")
    print(f"  threshold_high = {rec['threshold_high']}")

    cur = _load_current_thresholds()
    decision = _compare(cur, rec, args.diff_threshold)

    # Prepend decision to the JSON file for easy PR review
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    raw["decision"] = decision
    out_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False))

    print()
    print("=== Decision ===")
    print(f"  current  low={decision['current']['low']:.3f} high={decision['current']['high']:.3f}")
    print(f"  proposed low={decision['recommended']['low']:.3f} high={decision['recommended']['high']:.3f}")
    print(f"  drift    low={decision['drift']['low']} high={decision['drift']['high']}")

    if decision["needs_pr"]:
        print()
        print("⚠️  Drift exceeds threshold — please open a PR updating "
              "config/default.yaml `rag.abstain` block. Suggested values:")
        print()
        print("  rag:")
        print("    abstain:")
        print(f"      threshold_low: {rec['threshold_low']}")
        print(f"      threshold_high: {rec['threshold_high']}")
        print()
        print(f"Full data: {out_path}")
        return 1

    print()
    print("✅ Drift within tolerance — no action required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
