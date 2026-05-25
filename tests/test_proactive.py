"""Tests for paper_rag.proactive (M9 / ADR-0018).

Pure-logic tests — no LLM, no Qdrant, no arxiv API. We stub bge-m3 encode,
arxiv search, and ingest to keep the suite fast and CI-friendly.

Coverage:
  - subscriptions CRUD + dedup + cross-user isolation
  - inbox write/read/dismiss + unread count
  - paper_access touch + stale detection
  - matcher: cosine + threshold by strength + ingester skip
  - auto_ingest_hook: arxiv URL extraction + inbox card on success/failure
  - digest: render structure + dedup
  - stale: card content
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    Path(tmp.name).unlink(missing_ok=True)
    os.environ["FEEDBACK_SQLITE_PATH"] = tmp.name
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------


def test_subscription_add_and_dedup():
    _fresh_db()
    from paper_rag.proactive import subscriptions

    sid1 = subscriptions.add("alice", "keyword", "Self-RAG", strength="high")
    sid2 = subscriptions.add("alice", "keyword", "Self-RAG", strength="normal")
    assert sid1 == sid2, "dedup expected"
    rows = subscriptions.list_for_user("alice")
    assert len(rows) == 1
    assert rows[0]["strength"] == "normal"  # later add wins


def test_subscription_user_isolation():
    _fresh_db()
    from paper_rag.proactive import subscriptions

    subscriptions.add("alice", "keyword", "RAG")
    subscriptions.add("bob", "keyword", "FlashAttention")
    a = subscriptions.list_for_user("alice")
    b = subscriptions.list_for_user("bob")
    assert len(a) == 1 and a[0]["value"] == "RAG"
    assert len(b) == 1 and b[0]["value"] == "FlashAttention"


def test_subscription_delete_is_user_scoped():
    _fresh_db()
    from paper_rag.proactive import subscriptions

    sid = subscriptions.add("alice", "keyword", "RAG")
    # Bob can't delete Alice's sub
    assert subscriptions.delete(sid, user_id="bob") is False
    # Alice can
    assert subscriptions.delete(sid, user_id="alice") is True
    # After delete, list_for_user (only_enabled=True) returns empty
    assert subscriptions.list_for_user("alice") == []


def test_subscription_validation():
    _fresh_db()
    from paper_rag.proactive import subscriptions

    try:
        subscriptions.add("u", "weird_kind", "x")
    except ValueError as e:
        assert "kind" in str(e)
    else:
        raise AssertionError("expected ValueError on bad kind")


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------


def test_inbox_write_and_unread_count():
    _fresh_db()
    from paper_rag.proactive import inbox

    inbox.write("alice", "daily_digest", "Test card", body_md="x")
    inbox.write("alice", "sub_match", "Match!", body_md="y")
    items = inbox.list_for_user("alice", unread_only=True)
    assert len(items) == 2
    assert inbox.unread_count("alice") == 2


def test_inbox_mark_read_and_dismiss():
    _fresh_db()
    from paper_rag.proactive import inbox

    iid = inbox.write("alice", "daily_digest", "Test", body_md="x")
    assert inbox.mark_read(iid, user_id="alice") is True
    assert inbox.unread_count("alice") == 0
    # Mark-read is idempotent
    assert inbox.mark_read(iid, user_id="alice") is False
    # Dismiss removes from listing
    assert inbox.dismiss(iid, user_id="alice") is True
    items = inbox.list_for_user("alice", unread_only=False)
    assert all(it["id"] != iid for it in items), "dismissed item leaked"


def test_inbox_user_isolation():
    _fresh_db()
    from paper_rag.proactive import inbox

    iid = inbox.write("alice", "daily_digest", "x")
    # Bob can't mark Alice's item read
    assert inbox.mark_read(iid, user_id="bob") is False


# ---------------------------------------------------------------------------
# paper_access + stale
# ---------------------------------------------------------------------------


def test_paper_access_touch_and_stale_detection():
    _fresh_db()
    from paper_rag.proactive import paper_access

    # Touch a paper 31 days ago, another 1 day ago
    long_ago = time.time() - 31 * 86400
    recent = time.time() - 86400
    paper_access.touch("alice", "arxiv:1111.11111", ts=long_ago)
    paper_access.touch("alice", "arxiv:2222.22222", ts=recent)

    stale = paper_access.stale_for_user("alice", older_than_days=30)
    assert len(stale) == 1
    assert stale[0]["paper_id"] == "arxiv:1111.11111"


def test_paper_access_increments_count():
    _fresh_db()
    from paper_rag.proactive import paper_access

    paper_access.touch("alice", "arxiv:1111.11111")
    paper_access.touch("alice", "arxiv:1111.11111")
    paper_access.touch("alice", "arxiv:1111.11111")
    rows = paper_access.stale_for_user("alice", older_than_days=-1)  # negative = all
    assert rows[0]["access_count"] == 3


def test_stale_scan_writes_inbox():
    _fresh_db()
    from paper_rag.proactive import paper_access, stale

    long_ago = time.time() - 60 * 86400
    paper_access.touch("alice", "arxiv:test", ts=long_ago)

    # Stub get_paper to avoid hitting paper_rag main DB
    import paper_rag.store.sqlite_store as ss

    class _StubPaper:
        def model_dump(self_):
            return {"paper_id": "arxiv:test", "title": "Stub Paper",
                    "abstract": "Some abstract."}
    ss.get_paper = lambda pid: _StubPaper()

    n = stale.stale_scan_for_user("alice", older_than_days=30, max_cards=3)
    assert n == 1


# ---------------------------------------------------------------------------
# matcher
# ---------------------------------------------------------------------------


def test_matcher_skips_ingester():
    _fresh_db()
    from paper_rag.proactive import matcher, subscriptions

    subscriptions.add("alice", "keyword", "Self-RAG")
    subscriptions.add("bob", "keyword", "Self-RAG")

    # Stub bge-m3: alice's keyword and the paper are identical vectors → high sim
    stub_emb = [1.0, 0.0, 0.0]
    matcher._encode = lambda text: stub_emb

    matches = matcher.match_paper_to_subs(
        paper_id="arxiv:test",
        title="Self-RAG",
        abstract="...",
        ingester_user_id="alice",
    )
    user_ids = [m["subscription"]["user_id"] for m in matches]
    assert "alice" not in user_ids, "ingester should be skipped"
    assert "bob" in user_ids


def test_matcher_threshold_by_strength():
    _fresh_db()
    from paper_rag.proactive import matcher, subscriptions

    subscriptions.add("alice", "keyword", "RAG", strength="low")    # threshold 0.75
    subscriptions.add("bob", "keyword", "RAG", strength="high")     # threshold 0.55

    # 0.65 sim: too low for alice (low strength), high enough for bob
    paper_emb = [1.0, 0.5]
    sub_emb = [0.5, 0.5]
    # Cosine ≈ (1*0.5+0.5*0.5)/(sqrt(1.25)*sqrt(0.5)) ≈ 0.949 -> too high
    # Use vectors that produce ~0.65 similarity
    paper_emb = [1.0, 1.0, 1.0, 0.0]
    sub_emb = [1.0, 0.0, 0.5, 0.5]
    sim = matcher._cosine(paper_emb, sub_emb)
    # Verify our test data lands in expected band
    assert 0.55 < sim < 0.75, f"test vectors give sim={sim}, expected ~0.65 band"

    matcher._encode = lambda text: paper_emb if "paper" in text else sub_emb
    matches = matcher.match_paper_to_subs(
        paper_id="x", title="paper title", abstract="paper body"
    )
    user_ids = [m["subscription"]["user_id"] for m in matches]
    assert "bob" in user_ids
    assert "alice" not in user_ids, f"alice (low strength) should NOT match at sim={sim}"


# ---------------------------------------------------------------------------
# auto_ingest_hook
# ---------------------------------------------------------------------------


def test_detect_arxiv_ids_various_formats():
    from paper_rag.proactive.auto_ingest_hook import detect_arxiv_ids

    text = (
        "Look at https://arxiv.org/abs/2310.11511 and "
        "also https://arxiv.org/pdf/2305.06983v2.pdf, "
        "plus arxiv:2401.01313."
    )
    ids = detect_arxiv_ids(text)
    assert "2310.11511" in ids
    assert "2305.06983" in ids
    assert "2401.01313" in ids
    # Dedup
    text2 = "arxiv.org/abs/2310.11511 arxiv:2310.11511"
    assert detect_arxiv_ids(text2) == ["2310.11511"]


def test_auto_ingest_writes_success_card():
    _fresh_db()
    from paper_rag.proactive import auto_ingest_hook, inbox

    auto_ingest_hook._ingest_one = lambda aid, uid: {
        "paper_id": f"arxiv:{aid}",
        "title": "Stub Title",
        "n_chunks": 42,
        "status": "ingested",
    }
    item_id = auto_ingest_hook.background_ingest_sync("2310.11511", "alice")
    assert item_id > 0
    items = inbox.list_for_user("alice")
    assert any("已入库" in it["title"] for it in items)


def test_auto_ingest_writes_failure_card():
    _fresh_db()
    from paper_rag.proactive import auto_ingest_hook, inbox

    auto_ingest_hook._ingest_one = lambda aid, uid: {
        "status": "error", "error": "arxiv 429 rate limited"
    }
    item_id = auto_ingest_hook.background_ingest_sync("2999.99999", "alice")
    assert item_id > 0
    items = inbox.list_for_user("alice")
    assert any("入库失败" in it["title"] for it in items)


# ---------------------------------------------------------------------------
# digest render
# ---------------------------------------------------------------------------


def test_digest_render_card_structure():
    from paper_rag.proactive.digest import render_digest_card

    bullets = [
        {"paper": {"title": "Paper A", "arxiv_id": "1111.11111"},
         "tldr": "TL;DR for A.", "matched_keyword": "RAG"},
        {"paper": {"title": "Paper B", "arxiv_id": "2222.22222"},
         "tldr": "TL;DR for B.", "matched_keyword": "Self-RAG"},
    ]
    title, body = render_digest_card(bullets)
    assert "2 篇" in title
    assert "Paper A" in body
    assert "Paper B" in body
    assert "TL;DR for A" in body
    assert "RAG" in body


def test_digest_empty_returns_marker():
    from paper_rag.proactive.digest import render_digest_card

    title, body = render_digest_card([])
    assert "暂无" in title or "no" in title.lower() or "暂无" in body


# ---------------------------------------------------------------------------
# webhook (P3-13)
# ---------------------------------------------------------------------------


def test_webhook_crud():
    _fresh_db()
    from paper_rag.proactive import webhook

    webhook.add_webhook("alice", "dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=xxx", secret="topsecret")
    webhook.add_webhook("alice", "feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/yyy")
    webhook.add_webhook("bob", "wecom", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=zzz")

    a = webhook.list_webhooks("alice")
    assert len(a) == 2
    assert {h["channel"] for h in a} == {"dingtalk", "feishu"}

    b = webhook.list_webhooks("bob")
    assert len(b) == 1
    assert b[0]["channel"] == "wecom"

    ok = webhook.disable_webhook("alice", "feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/yyy")
    assert ok
    a2 = webhook.list_webhooks("alice")
    assert len(a2) == 1
    assert a2[0]["channel"] == "dingtalk"


def test_webhook_fan_out_no_hooks():
    _fresh_db()
    from paper_rag.proactive import webhook

    out = webhook.fan_out({"user_id": "ghost", "title": "test", "kind": "daily_digest"})
    assert out["sent"] == 0
    assert "no webhooks" in out.get("skipped", "")


def test_webhook_fan_out_dispatch_called():
    """fan_out routes to channel adapter; stub the adapter to verify wiring."""
    _fresh_db()
    from paper_rag.proactive import webhook

    webhook.add_webhook("alice", "dingtalk", "https://example.com/hook")

    calls = []
    original = webhook._DISPATCH["dingtalk"]
    webhook._DISPATCH["dingtalk"] = (
        lambda endpoint, secret, item: (calls.append((endpoint, item["title"])) or (True, "stub-ok"))
    )
    try:
        out = webhook.fan_out({
            "user_id": "alice",
            "kind": "daily_digest",
            "title": "Daily 2026-05-21",
            "body_md": "## morning summary",
        })
    finally:
        webhook._DISPATCH["dingtalk"] = original

    assert out["sent"] == 1, out
    assert len(calls) == 1
    assert calls[0][1] == "Daily 2026-05-21"


def main() -> int:
    tests = [
        test_subscription_add_and_dedup,
        test_subscription_user_isolation,
        test_subscription_delete_is_user_scoped,
        test_subscription_validation,
        test_inbox_write_and_unread_count,
        test_inbox_mark_read_and_dismiss,
        test_inbox_user_isolation,
        test_paper_access_touch_and_stale_detection,
        test_paper_access_increments_count,
        test_stale_scan_writes_inbox,
        test_matcher_skips_ingester,
        test_matcher_threshold_by_strength,
        test_detect_arxiv_ids_various_formats,
        test_auto_ingest_writes_success_card,
        test_auto_ingest_writes_failure_card,
        test_digest_render_card_structure,
        test_digest_empty_returns_marker,
        test_webhook_crud,
        test_webhook_fan_out_no_hooks,
        test_webhook_fan_out_dispatch_called,
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
