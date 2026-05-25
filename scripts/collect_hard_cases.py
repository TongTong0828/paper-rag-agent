#!/usr/bin/env python3
"""Collect hard cases from feedback events and append to evaluation set.

ADR-0017 § 7. Triggered weekly by cron (or manually). Reads feedback_events
from the SQLite store, applies hard-case rules, deduplicates against the
existing hard_cases.jsonl, and appends new entries.

Usage:
    python -m paper_rag.scripts.collect_hard_cases \\
        --since 7d \\
        --out tests/eval/hard_cases.jsonl

Hard-case rules (v1)
--------------------
1. thumbs_down with reason in {hallucination, irrelevant} → strongest signal
2. ≥ 2 follow_up_question events within 5 min for the same conversation_id
3. judge_score with faithful < 4 OR complete < 3
4. abstain_followup_ingest event → system missed a paper that was actually
   relevant (user manually backfilled)

This script does NOT need the qa_agentic LLM — it only does set operations
on the events table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------


_DUR_RE = re.compile(r"^(\d+)\s*([smhdw])$")


def parse_since(s: str) -> float:
    """'7d' -> epoch of (now - 7 days). Accepts s/m/h/d/w."""
    m = _DUR_RE.match(s.strip().lower())
    if not m:
        raise ValueError(f"unrecognized duration: {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    seconds = n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return time.time() - seconds


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def collect_hard_cases(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply rules; return unique hard-case dicts."""
    out: list[dict[str, Any]] = []

    # Group by conversation_id for follow-up clustering
    by_convo: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        cid = e.get("conversation_id")
        if cid:
            by_convo[cid].append(e)

    seen_traces: set[str] = set()

    # Rule 1: thumbs_down with hard reason
    for e in events:
        if e["event_type"] != "thumbs_down":
            continue
        reason = (e["payload"] or {}).get("reason")
        if reason not in ("hallucination", "irrelevant", "wrong_citation"):
            continue
        trace = e.get("trace_id") or ""
        if trace in seen_traces:
            continue
        seen_traces.add(trace)
        out.append(_to_hard_case(e, rule=f"thumbs_down_{reason}"))

    # Rule 2: ≥2 follow_up within 5min
    for cid, evs in by_convo.items():
        followups = [e for e in evs if e["event_type"] == "follow_up_question"]
        if len(followups) < 2:
            continue
        followups.sort(key=lambda e: e["created_at"])
        first, last = followups[0]["created_at"], followups[-1]["created_at"]
        if (last - first) > 300:  # >5 min apart, not a hot-streak
            continue
        # Use the first follow-up event as the hard case anchor
        first_ev = followups[0]
        trace = first_ev.get("trace_id") or ""
        if trace in seen_traces:
            continue
        seen_traces.add(trace)
        out.append(_to_hard_case(first_ev, rule="repeat_follow_up", n_followups=len(followups)))

    # Rule 3: low judge_score
    for e in events:
        if e["event_type"] != "judge_score":
            continue
        p = e["payload"] or {}
        f = p.get("faithful")
        c = p.get("complete")
        if (f is not None and float(f) < 4) or (c is not None and float(c) < 3):
            trace = e.get("trace_id") or ""
            if trace in seen_traces:
                continue
            seen_traces.add(trace)
            out.append(_to_hard_case(e, rule="judge_low",
                                     faithful=f, complete=c))

    # Rule 4: abstain_followup_ingest
    for e in events:
        if e["event_type"] != "abstain_followup_ingest":
            continue
        trace = e.get("trace_id") or ""
        if trace in seen_traces:
            continue
        seen_traces.add(trace)
        out.append(_to_hard_case(e, rule="abstain_missed",
                                 ingested_paper_id=(e["payload"] or {}).get("ingested_paper_id")))

    return out


def _to_hard_case(event: dict[str, Any], *, rule: str, **extra) -> dict[str, Any]:
    """Convert an event row into a hard_cases.jsonl entry."""
    return {
        "qid": _hard_qid(event),
        "trace_id": event.get("trace_id"),
        "conversation_id": event.get("conversation_id"),
        "user_id": event.get("user_id"),
        "rule": rule,
        "captured_at": event["created_at"],
        "extra": extra,
    }


def _hard_qid(event: dict[str, Any]) -> str:
    ts = int(event["created_at"])
    short = (event.get("trace_id") or "noTrace")[:10]
    return f"hc_{ts}_{short}"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def read_existing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["qid"])
            except (json.JSONDecodeError, KeyError):
                pass
    return seen


def append_jsonl(path: Path, items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return len(items)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="7d",
                    help="Only consider events newer than this duration (e.g. 7d, 30d, 24h)")
    ap.add_argument("--out", default="tests/eval/hard_cases.jsonl",
                    help="Path to the hard-cases JSONL file (appended)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print proposed hard cases instead of appending")
    args = ap.parse_args()

    out_path = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    cutoff = parse_since(args.since)

    from paper_rag.feedback import store as feedback_store

    events = list(feedback_store.iter_since(cutoff))
    print(f"Loaded {len(events)} events since {args.since} (cutoff={cutoff:.0f})")

    cases = collect_hard_cases(events)
    print(f"Detected {len(cases)} hard cases")

    seen = read_existing(out_path)
    new_cases = [c for c in cases if c["qid"] not in seen]
    print(f"  {len(new_cases)} new (not already in {out_path.name})")

    if args.dry_run:
        for c in new_cases[:20]:
            print("  ", json.dumps(c, ensure_ascii=False))
        if len(new_cases) > 20:
            print(f"  ... ({len(new_cases) - 20} more)")
        return 0

    n = append_jsonl(out_path, new_cases)
    print(f"Appended {n} hard cases to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
