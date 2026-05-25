"""paper_qa tool (agentic) — single tool call from the lead agent's view."""

from __future__ import annotations

from ..rag.qa_agentic import answer
from ._schema import PaperQAInput


def paper_qa(input: PaperQAInput) -> dict:
    """Answer a question with cited chunks. Internally agentic
    (intent -> rewrite -> hybrid retrieve -> rerank -> reflect -> iterate)."""
    return answer(input.question, paper_ids=input.paper_ids)


__all__ = [
    "paper_qa",
]
