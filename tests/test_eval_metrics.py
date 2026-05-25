"""Tests for eval metric primitives."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def test_recall_basic():
    from eval.metrics import recall_at_k

    assert recall_at_k(["a", "b", "c"], ["a", "c"], k=3) == 1.0
    assert recall_at_k(["a", "b", "c"], ["a", "c"], k=2) == 0.5
    assert recall_at_k(["x"], ["a"], k=5) == 0.0
    assert recall_at_k([], ["a"], k=5) == 0.0
    assert recall_at_k(["a"], [], k=5) == 0.0


def test_recall_dedup():
    from eval.metrics import recall_at_k

    # duplicates in predicted shouldn't inflate
    assert recall_at_k(["a", "a", "a", "b"], ["a", "b"], k=2) == 1.0


def test_precision_at_k():
    from eval.metrics import precision_at_k

    assert precision_at_k(["a", "b", "c"], ["a"], k=3) == 1 / 3
    assert precision_at_k(["a"], ["a"], k=1) == 1.0
    assert precision_at_k([], ["a"], k=3) == 0.0


def test_mrr():
    from eval.metrics import mrr

    assert mrr(["x", "y", "a"], ["a"]) == 1 / 3
    assert mrr(["a"], ["a"]) == 1.0
    assert mrr(["x"], ["a"]) == 0.0


def test_citation_precision_and_existence():
    from eval.metrics import citation_existence_rate, citation_precision

    # GT not provided -> None
    assert citation_precision(["c1"], []) is None
    # GT provided
    assert citation_precision(["c1", "c2"], ["c1"]) == 0.5
    # existence: against retrieved set
    assert citation_existence_rate(["c1", "c2"], ["c1", "c2", "c3"]) == 1.0
    assert citation_existence_rate(["c1", "ghost"], ["c1"]) == 0.5
    # empty citations -> 1.0 (no fakes)
    assert citation_existence_rate([], ["c1"]) == 1.0


def test_must_contain_and_violations():
    from eval.metrics import must_contain_score, must_not_contain_violations

    assert must_contain_score("Hello World", ["hello", "world"]) == 1.0
    assert must_contain_score("Hello", ["world"]) == 0.0
    assert must_contain_score("anything", []) == 1.0
    assert must_not_contain_violations("Hello dog", ["dog", "cat"]) == 1
    assert must_not_contain_violations("clean", ["dog", "cat"]) == 0


def test_loader_smoke(tmp_path: Path):
    from eval.loader import load_jsonl

    p = tmp_path / "qa.jsonl"
    p.write_text(
        '{"qid":"q1","question":"Q?","relevant_paper_ids":["arxiv:1"]}\n'
        "# this is a comment\n"
        '{"qid":"q2","question":"Q2?","intent":"explore"}\n',
        encoding="utf-8",
    )
    items = load_jsonl(p)
    assert [i.qid for i in items] == ["q1", "q2"]
    assert items[1].intent == "explore"
