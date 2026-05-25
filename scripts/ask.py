"""Ask a question against the RAG store.

Usage:
    python scripts/ask.py "What is the main contribution?"
    python scripts/ask.py "..." --paper-id arxiv:2310.12345 --top-k 6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_rag import config as cfg
from paper_rag.utils.logger import get_logger

log = get_logger("ask")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ask a question against the paper RAG store.")
    p.add_argument("question")
    p.add_argument("--paper-id", action="append", help="Restrict search to specific paper_id(s)")
    p.add_argument("--top-k", type=int, default=8)
    p.add_argument("--no-llm", action="store_true",
                   help="Only show retrieved chunks (no LLM answer)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg.load()

    if args.no_llm:
        from paper_rag.retrieve.dense import retrieve
        from paper_rag.retrieve.format import format_evidence

        chunks = retrieve(args.question, top_k=args.top_k, paper_ids=args.paper_id)
        print(format_evidence(chunks))
        return 0

    from paper_rag.rag.qa_simple import answer

    out = answer(args.question, top_k=args.top_k, paper_ids=args.paper_id)
    print("\n=== ANSWER ===\n")
    print(out["answer"])
    print(f"\n=== CITATIONS ({len(out['citations'])}) ===")
    for cid in out["citations"]:
        print(f"  - {cid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
