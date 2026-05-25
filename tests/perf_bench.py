"""Quick latency benchmark for retrieval and end-to-end qa.

Usage:
    PAPER_RAG_CONFIG=config/local.yaml python tests/perf_bench.py --queries 10
    PAPER_RAG_CONFIG=config/local.yaml python tests/perf_bench.py --queries 5 --with-llm

Outputs P50/P95/P99 for each stage. Not a load test — single-process,
serial. Useful for tracking regressions across releases.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


_QUERIES = [
    "What is Self-RAG?",
    "How does the original RAG combine parametric and non-parametric memory?",
    "What is BEIR?",
    "What is Naive RAG vs Advanced RAG?",
    "How does FLARE decide when to retrieve?",
    "What is FactScore?",
    "What is reranking and why use it?",
    "What is HyDE?",
    "What are reflection tokens in Self-RAG?",
    "What is the difference between RAG-Sequence and RAG-Token?",
]


def _quantile(samples, q):
    if not samples:
        return 0.0
    s = sorted(samples)
    return s[min(len(s) - 1, int(q * len(s)))]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--queries", type=int, default=10)
    p.add_argument("--with-llm", action="store_true",
                   help="Also benchmark full qa_agentic (slow, requires LLM)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    qs = _QUERIES[: args.queries]

    print("Warm-up (loading bge-m3 + Qdrant)...")
    from paper_rag.retrieve.hybrid import hybrid_search

    hybrid_search(qs[0], top_k=5)

    print(f"\nRetrieval benchmark ({len(qs)} queries):")
    timings = []
    for q in qs:
        t0 = time.time()
        chunks = hybrid_search(q, top_k=8)
        timings.append(time.time() - t0)
        print(f"  {q[:50]:50s}  {timings[-1]*1000:6.0f}ms  hits={len(chunks)}")

    print(f"\n=== Retrieval (n={len(timings)}) ===")
    print(f"  mean  {mean(timings)*1000:.1f}ms")
    print(f"  p50   {_quantile(timings, 0.50)*1000:.1f}ms")
    print(f"  p95   {_quantile(timings, 0.95)*1000:.1f}ms")
    print(f"  p99   {_quantile(timings, 0.99)*1000:.1f}ms")

    if args.with_llm:
        from paper_rag.rag.qa_agentic import answer

        print(f"\nFull qa_agentic benchmark ({len(qs)} queries, ~2 min each):")
        timings = []
        for q in qs:
            t0 = time.time()
            out = answer(q)
            elapsed = time.time() - t0
            timings.append(elapsed)
            print(f"  {q[:50]:50s}  {elapsed:5.1f}s  cites={len(out.get('citations', []))}")

        print(f"\n=== qa_agentic (n={len(timings)}) ===")
        print(f"  mean  {mean(timings):.1f}s")
        print(f"  p50   {_quantile(timings, 0.50):.1f}s")
        print(f"  p95   {_quantile(timings, 0.95):.1f}s")

    # Render Prometheus-format metrics
    from paper_rag.observability import render

    print("\n=== Prometheus metrics ===")
    print(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
