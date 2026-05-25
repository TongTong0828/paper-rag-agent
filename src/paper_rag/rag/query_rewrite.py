"""Query rewriting and HyDE.

Given a user question, produce:
  - 2-4 paraphrase variants for dense retrieval
  - 1 HyDE pseudo-answer for dense retrieval
  - extracted keyword string for BM25
"""

from __future__ import annotations

import json
import re

from .. import config as cfg
from ..utils.logger import get_logger
from .llm import chat

log = get_logger("rag.query_rewrite")

_PROMPT = """You help an academic paper RAG system. Given a question, output a JSON
object with three keys:

  "variants":  array of 2-3 paraphrases that may match different wording in papers
  "keywords":  short string of 3-8 lowercase keywords (BM25 input)
  "hyde":      a 2-3 sentence hypothetical answer if you had to guess (used as
               an extra dense query). Be plausible; do NOT fabricate citations.

Question: {q}

Return only JSON.
"""


def rewrite(question: str) -> dict:
    c = cfg.load()
    enable = c.rag.enable_hyde
    try:
        raw = chat(
            [{"role": "user", "content": _PROMPT.replace("{q}", question)}],
            temperature=c.llm.temperatures.rewrite,
            max_tokens=400,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning(f"rewrite failed: {e}; degrade to original only")
        data = {}

    variants = data.get("variants") or []
    keywords = data.get("keywords") or question
    hyde = data.get("hyde") if enable else None
    queries_dense = [question, *variants]
    if hyde:
        queries_dense.append(hyde)
    return {"dense_queries": queries_dense, "bm25_query": keywords, "raw": data}


__all__ = [
    "rewrite",
]
