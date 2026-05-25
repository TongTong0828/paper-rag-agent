"""Tests for paper_rag.feedback (M11 / ADR-0017).

Pure-logic tests — exercise the events schema, SQLite store, hard-case
collector. No router, no LLM, no Qdrant. The store uses a per-test temp
sqlite file via the FEEDBACK_SQLITE_PATH env var.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


def _fresh_db():
    """Allocate a fresh SQLite path + return its Path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    Path(tmp.name).unlink(missing_ok=True)
    os.environ["FEEDBACK_SQLITE_PATH"] = tmp.name
    return Path(tmp.name)


def _reset_collector_rate_limit():
    """Reset the in-process per-day counter between tests."""
    from paper_rag.feedback import collector

    collector._counter.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_thumbs_down_requires_valid_reason():
    from paper_rag.feedback.events import validate_payload

    # Missing reason
    try:
        validate_payload("thumbs_down", {})
    except ValueError as e:
        assert "reason" in str(e)
    else:
        raise AssertionError("expected ValueError for missing reason")

    # Bad reason
    try:
        validate_payload("thumbs_down", {"reason": "weird_excuse"})
    except ValueError as e:
        assert "reason" in str(e)
    else:
        raise AssertionError("expected ValueError for bad reason")

    # Good reason
    out = validate_payload("thumbs_down", {"reason": "hallucination"})
    assert out["reason"] == "hallucination"


def test_comment_is_stripped_to_length_and_keywords():
    """Privacy: raw comment never persisted, only metadata extracted."""
    from paper_rag.feedback.events import validate_payload

    payload = {
        "reason": "hallucination",
        "comment": "This answer hallucinated a paper that doesn't exist!",
    }
    out = validate_payload("thumbs_down", payload)
    assert "comment" not in out, "raw comment must NOT be persisted"
    assert "comment_length" in out
    assert out["comment_length"] > 0
    assert "hallucination" in out["comment_keywords"]


def test_judge_score_validation():
    from paper_rag.feedback.events import validate_payload

    # In-range
    out = validate_payload("judge_score", {"faithful": 4.5, "complete": 3.0})
    assert out["faithful"] == 4.5

    # Out-of-range
    try:
        validate_payload("judge_score", {"faithful": 99.0})
    except ValueError as e:
        assert "out of" in str(e).lower() or "range" in str(e).lower() or "faithful" in str(e).lower()
    else:
        raise AssertionError("expected ValueError for faithful=99")


def test_record_event_writes_and_dedups():
    """SQLite store: write returns id, second write within same minute returns same id."""
    _fresh_db()
    _reset_collector_rate_limit()
    from paper_rag.feedback import record_event

    rid1 = record_event(
        user_id="user_a",
        event_type="thumbs_down",
        payload={"reason": "irrelevant"},
        trace_id="abc123",
    )
    rid2 = record_event(
        user_id="user_a",
        event_type="thumbs_down",
        payload={"reason": "irrelevant"},
        trace_id="abc123",
    )
    assert rid1 == rid2, f"dedup expected, got {rid1} vs {rid2}"


def test_user_isolation_in_recent_events():
    """recent_events for user A doesn't leak user B's events."""
    _fresh_db()
    _reset_collector_rate_limit()
    from paper_rag.feedback import recent_events, record_event

    record_event(user_id="alice", event_type="thumbs_up", payload={}, trace_id="t1")
    record_event(user_id="bob", event_type="thumbs_up", payload={}, trace_id="t2")

    alice_events = recent_events("alice", limit=10)
    bob_events = recent_events("bob", limit=10)
    assert len(alice_events) == 1
    assert len(bob_events) == 1
    assert alice_events[0]["trace_id"] == "t1"
    assert bob_events[0]["trace_id"] == "t2"


def test_user_stats_aggregates_by_type():
    _fresh_db()
    _reset_collector_rate_limit()
    from paper_rag.feedback import record_event, user_stats

    # Different traces avoid dedup
    record_event(user_id="u1", event_type="thumbs_up", payload={}, trace_id="t1")
    record_event(user_id="u1", event_type="thumbs_up", payload={}, trace_id="t2")
    record_event(user_id="u1", event_type="thumbs_down",
                 payload={"reason": "hallucination"}, trace_id="t3")
    record_event(user_id="u1", event_type="copy_answer",
                 payload={"snippet_chars": 100}, trace_id="t4")

    stats = user_stats("u1")
    assert stats["total_events"] == 4
    assert stats["by_type"]["thumbs_up"] == 2
    assert stats["by_type"]["thumbs_down"] == 1
    assert stats["by_type"]["copy_answer"] == 1


def test_rate_limit_enforced():
    """Per-user daily cap raises PermissionError after threshold."""
    _fresh_db()
    _reset_collector_rate_limit()
    from paper_rag.feedback import collector, record_event

    # Lower cap for the test
    saved_cap = collector._DAILY_CAP_PER_USER
    collector._DAILY_CAP_PER_USER = 3
    try:
        for i in range(3):
            record_event(user_id="spam", event_type="thumbs_up",
                         payload={}, trace_id=f"trace_{i}")
        try:
            record_event(user_id="spam", event_type="thumbs_up",
                         payload={}, trace_id="trace_overflow")
        except PermissionError as e:
            assert "rate limit" in str(e).lower()
            return
        raise AssertionError("expected PermissionError on cap")
    finally:
        collector._DAILY_CAP_PER_USER = saved_cap


def test_hard_case_collector_thumbs_down_hallucination():
    """thumbs_down with hallucination reason becomes a hard case."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    if "collect_hard_cases" in sys.modules:
        importlib.reload(sys.modules["collect_hard_cases"])
    chc = importlib.import_module("collect_hard_cases")

    events = [
        {
            "id": 1,
            "user_id": "alice",
            "trace_id": "t-hallu",
            "conversation_id": "c1",
            "event_type": "thumbs_down",
            "payload": {"reason": "hallucination"},
            "created_at": 1000.0,
        },
        {
            "id": 2,
            "user_id": "alice",
            "trace_id": "t-other",
            "conversation_id": "c2",
            "event_type": "thumbs_down",
            "payload": {"reason": "other"},
            "created_at": 2000.0,
        },
    ]
    cases = chc.collect_hard_cases(events)
    assert len(cases) == 1
    assert cases[0]["rule"] == "thumbs_down_hallucination"
    assert cases[0]["trace_id"] == "t-hallu"


def test_hard_case_collector_repeat_followups_in_5min():
    """≥2 follow-ups within 5min → repeat_follow_up rule."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    if "collect_hard_cases" in sys.modules:
        importlib.reload(sys.modules["collect_hard_cases"])
    chc = importlib.import_module("collect_hard_cases")

    base = 1000.0
    events = [
        {"id": 1, "user_id": "u", "trace_id": "tA",
         "conversation_id": "convo1", "event_type": "follow_up_question",
         "payload": {}, "created_at": base},
        {"id": 2, "user_id": "u", "trace_id": "tB",
         "conversation_id": "convo1", "event_type": "follow_up_question",
         "payload": {}, "created_at": base + 60.0},
    ]
    cases = chc.collect_hard_cases(events)
    repeat = [c for c in cases if c["rule"] == "repeat_follow_up"]
    assert len(repeat) == 1


def test_hard_case_collector_judge_low():
    """judge_score with low faithful triggers judge_low rule."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    if "collect_hard_cases" in sys.modules:
        importlib.reload(sys.modules["collect_hard_cases"])
    chc = importlib.import_module("collect_hard_cases")

    events = [{
        "id": 1, "user_id": "u", "trace_id": "tx",
        "conversation_id": None, "event_type": "judge_score",
        "payload": {"faithful": 2.0, "complete": 4.0},
        "created_at": 1000.0,
    }]
    cases = chc.collect_hard_cases(events)
    assert len(cases) == 1
    assert cases[0]["rule"] == "judge_low"


def test_hard_case_dedup_across_rules():
    """Same trace_id triggering multiple rules only emits once."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    if "collect_hard_cases" in sys.modules:
        importlib.reload(sys.modules["collect_hard_cases"])
    chc = importlib.import_module("collect_hard_cases")

    events = [
        {"id": 1, "user_id": "u", "trace_id": "shared",
         "conversation_id": "c", "event_type": "thumbs_down",
         "payload": {"reason": "hallucination"}, "created_at": 1000.0},
        {"id": 2, "user_id": "u", "trace_id": "shared",
         "conversation_id": "c", "event_type": "judge_score",
         "payload": {"faithful": 1.0}, "created_at": 1100.0},
    ]
    cases = chc.collect_hard_cases(events)
    assert len(cases) == 1, f"expected single hard case, got {cases}"


def main() -> int:
    tests = [
        test_thumbs_down_requires_valid_reason,
        test_comment_is_stripped_to_length_and_keywords,
        test_judge_score_validation,
        test_record_event_writes_and_dedups,
        test_user_isolation_in_recent_events,
        test_user_stats_aggregates_by_type,
        test_rate_limit_enforced,
        test_hard_case_collector_thumbs_down_hallucination,
        test_hard_case_collector_repeat_followups_in_5min,
        test_hard_case_collector_judge_low,
        test_hard_case_dedup_across_rules,
    ]
    ok = fail = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            fail += 1
        except Exception as e:
            import traceback
            print(f"  💥 {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            fail += 1
    print(f"\n{ok}/{ok+fail} passed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
