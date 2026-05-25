# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 01 — QA basic
#
# End-to-end walk-through:
#
# 1. Ingest one paper from arXiv.
# 2. Ask a question.
# 3. Inspect the abstain decision and citations.
#
# **Prerequisites**
#
# ```bash
# pip install -e ".[full,dev]"
# make qdrant-up
# make init-store
# export OPENAI_API_KEY=...
# export OPENAI_BASE_URL=...
# export CHAT_MODEL=...
# ```

# %%
import json
import os
import sys
from pathlib import Path

# Allow running this file from the repo root without installing the package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# %% [markdown]
# ## 1. Ingest one paper
#
# `arxiv:2310.11511` is Self-RAG. We use the CLI tool here so the script is
# safe to re-run — it is idempotent (dedup catches the second call).

# %%
from paper_rag.tools.paper_index import ingest as ingest_tool  # type: ignore

result = ingest_tool({"arxiv_id": "2310.11511", "user_id": "demo"})
print(json.dumps(result, indent=2, ensure_ascii=False)[:500])

# %% [markdown]
# ## 2. Ask a question
#
# `paper_qa` runs the full agentic loop:
# rewrite → retrieve → rerank → reflect → abstain → answer → citation check.

# %%
from paper_rag.rag.qa_agentic import answer  # noqa: E402

out = answer("What is Self-RAG and how is it different from vanilla RAG?",
             paper_ids=["arxiv:2310.11511"])
print("=== answer ===")
print(out["answer"])
print()
print("=== citations ===")
print(out["citations"])

# %% [markdown]
# ## 3. Inspect the abstain decision
#
# This is the core safety mechanism (ADR-0014). For an in-domain question
# we should see `decision == "confident"` and `signal_quality == "high"`.

# %%
print(json.dumps(out["trace"]["abstain"], indent=2))

# %% [markdown]
# ## 4. Try an out-of-domain question
#
# Same paper, but a question that has nothing to do with it. Expected:
# `decision == "no_evidence"` and the LLM is **skipped** entirely.

# %%
out2 = answer("How do I bake sourdough bread?",
              paper_ids=["arxiv:2310.11511"])
print(out2["answer"])
print()
print(json.dumps(out2["trace"]["abstain"], indent=2))
