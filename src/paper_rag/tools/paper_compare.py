"""paper_compare tool: for each paper, run paper_qa on each dimension and assemble a matrix.

Note: this fan-outs N papers x M dimensions LLM calls. Use sparingly; the lead
agent should send <=4 papers x <=4 dims.
"""

from __future__ import annotations

from ..rag.qa_agentic import answer
from ..utils.logger import get_logger
from ._schema import PaperCompareInput


log = get_logger("tool.paper_compare")


def paper_compare(input: PaperCompareInput) -> dict:
    matrix: dict[str, dict[str, dict]] = {}
    for pid in input.paper_ids:
        matrix[pid] = {}
        for dim in input.dimensions:
            q = f"What is the {dim} of this paper?"
            res = answer(q, paper_ids=[pid])
            matrix[pid][dim] = {
                "answer": res["answer"],
                "citations": res["citations"],
            }
    return {"papers": input.paper_ids, "dimensions": input.dimensions, "matrix": matrix}
