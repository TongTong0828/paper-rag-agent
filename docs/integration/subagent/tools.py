"""LangChain tool wrappers around the standalone paper_rag package.

Lazy import so this module loads cheaply even when paper_rag is not on
PYTHONPATH (the lead agent only triggers the import on first tool call).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from langchain.tools import tool


def _ensure_paper_rag_importable() -> None:
    """Find the standalone paper_rag package even if it's not pip-installed.

    Looks at the env var PAPER_RAG_HOME first, then walks upward from this
    file to find a sibling `paper_rag/` directory at the deer-flow root.
    """
    try:
        import paper_rag  # noqa: F401

        return
    except ImportError:
        pass

    home = os.environ.get("PAPER_RAG_HOME")
    if home:
        candidate = Path(home).expanduser().resolve()
    else:
        here = Path(__file__).resolve()
        candidate = None
        for parent in here.parents:
            maybe = parent / "paper_rag" / "src" / "paper_rag"
            if maybe.is_dir():
                candidate = parent / "paper_rag" / "src"
                break
    if candidate and candidate.is_dir():
        sys.path.insert(0, str(candidate))


@tool("paper_qa", parse_docstring=True)
def paper_qa_tool(question: str, paper_ids: str | None = None) -> str:
    """Answer a research question using the indexed paper corpus.

    PRIORITY TRIGGER (lead agent must prefer this tool when ANY of these apply):
      - User mentions "paper" / "论文" / "arxiv" / "doi" / "学术" / "综述" / "literature"
      - User uploads or references a PDF / academic resource
      - Question asks "what does the paper say", "compare two papers", "explain method X from the paper"
      - The conversation is in the paper-research subagent context

    Internally agentic (intent classification, query rewrite, hybrid retrieval,
    rerank, post-retrieval reflection, iterative search). Returns a JSON
    string with `answer`, `citations` (list of chunk_ids), `chunks` (top
    evidence snippets), and `suspicious_citations` (alert if the model used
    [1] or (Author 2020) instead of the required [chunk:<id>] form).

    Use this for any question about indexed papers — single-paper deep dive,
    cross-paper comparison, or topical synthesis. Do NOT pre-search and feed
    snippets in; this tool does its own retrieval.

    Citation discipline: the answer text uses ONLY `[chunk:<id>]` markers; if
    the response contains numeric `[1]` or author-year `(Vaswani 2017)` forms,
    treat them as hallucinations and re-issue with stricter constraints.

    Usage:
      User: "What's the main contribution of this paper?"
        -> paper_qa(question="What is the main contribution?",
                    paper_ids="arxiv:2310.12345")

      User: "How is RAG different from fine-tuning?"
        -> paper_qa(question="How does RAG differ from fine-tuning?")
           # paper_ids omitted = global corpus

      User: "Compare these two methods on accuracy"  (after user uploaded 2)
        -> paper_qa(question="Compare the methods of paper A and paper B "
                             "on classification accuracy",
                    paper_ids="arxiv:2310.12345,arxiv:2308.00352")

    Args:
        question: Natural-language question.
        paper_ids: Optional comma-separated list of paper_ids to restrict search (e.g. arxiv:2310.12345,doi:10.1109/abc). Omit for global search.
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools._schema import PaperQAInput
    from paper_rag.tools.paper_qa import paper_qa

    pids = [p.strip() for p in paper_ids.split(",")] if paper_ids else None
    out = paper_qa(PaperQAInput(question=question, paper_ids=pids))
    out_safe = {
        "answer": out.get("answer", ""),
        "citations": out.get("citations", []),
        "chunks": [
            {k: c.get(k) for k in ("chunk_id", "paper_id", "section", "modality", "text")}
            for c in out.get("chunks", [])[:8]
        ],
    }
    return json.dumps(out_safe, ensure_ascii=False, indent=2)


@tool("paper_search", parse_docstring=True)
def paper_search_tool(query: str, top_k: int = 8) -> str:
    """Find papers in the indexed corpus relevant to a query.

    Returns paper-level results (deduped by paper_id), each with a title and
    a short snippet from the best-matching chunk. Use as a lightweight first
    step before deciding which papers to inspect with paper_qa.

    Usage:
      User: "any recent paper on long-context attention"
        -> paper_search(query="long-context attention transformer", top_k=8)

      User: "list me 3 papers about contrastive learning for vision"
        -> paper_search(query="contrastive learning vision", top_k=3)

    Args:
        query: Search query.
        top_k: Maximum number of papers to return (default 8).
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools._schema import PaperSearchInput
    from paper_rag.tools.paper_search import paper_search

    res = paper_search(PaperSearchInput(query=query, top_k=top_k))
    return json.dumps(res, ensure_ascii=False, indent=2)


@tool("paper_section", parse_docstring=True)
def paper_section_tool(paper_id: str, section_name: str) -> str:
    """Retrieve a specific section of a paper (Method, Experiments, etc.).

    Useful when the user wants to "read the methods" of a specific paper
    rather than ask a free-form question.

    Usage:
      User: "Show me the experiments of the FlashAttention paper"
        -> paper_section(paper_id="arxiv:2205.14135", section_name="experiment")

      User: "What's the methodology section say?"
        -> paper_section(paper_id="<current paper>", section_name="method")

    Args:
        paper_id: Paper id, e.g. "arxiv:2310.12345" or "doi:10.1109/...".
        section_name: Substring of the section title (case-insensitive).
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools._schema import PaperSectionInput
    from paper_rag.tools.paper_section import paper_section

    res = paper_section(PaperSectionInput(paper_id=paper_id, section_name=section_name))
    return json.dumps(res, ensure_ascii=False, indent=2)


@tool("paper_compare", parse_docstring=True)
def paper_compare_tool(paper_ids: str, dimensions: str = "motivation,method,results,limitations") -> str:
    """Compare multiple papers across given dimensions.

    EXPENSIVE: runs N x M agentic QA calls (one per paper x dimension).
    Keep N <= 4 and M <= 4.

    Usage:
      User: "Compare these 3 retrieval methods on accuracy and speed"
        -> paper_compare(paper_ids="arxiv:1,arxiv:2,arxiv:3",
                         dimensions="method,accuracy,speed,limitations")

    Args:
        paper_ids: Comma-separated paper_ids.
        dimensions: Comma-separated dimensions to compare on. Default
            "motivation,method,results,limitations".
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools._schema import PaperCompareInput
    from paper_rag.tools.paper_compare import paper_compare

    pids = [p.strip() for p in paper_ids.split(",") if p.strip()]
    dims = [d.strip() for d in dimensions.split(",") if d.strip()]
    res = paper_compare(PaperCompareInput(paper_ids=pids, dimensions=dims))
    return json.dumps(res, ensure_ascii=False, indent=2)


@tool("wiki_lookup", parse_docstring=True)
def wiki_lookup_tool(concept: str) -> str:
    """Look up a concept in the self-evolving paper wiki.

    Returns the canonical entry (definition, key papers, variants, related
    concepts, open problems) when found. When not found, returns near-miss
    candidates so the agent can refine the lookup or fall back to paper_qa.

    Usage:
      User: "What is contrastive learning?"
        -> wiki_lookup(concept="Contrastive Learning")

      User: "什么是 FlashAttention？"
        -> wiki_lookup(concept="FlashAttention")  # aliases include 中英

    Args:
        concept: Concept name, e.g. "Contrastive Learning" or "FlashAttention".
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools._schema import WikiLookupInput
    from paper_rag.tools.wiki_lookup import wiki_lookup

    res = wiki_lookup(WikiLookupInput(concept=concept))
    return json.dumps(res, ensure_ascii=False, indent=2)


@tool("export_bibtex", parse_docstring=True)
def export_bibtex_tool(paper_ids: str) -> str:
    """Export paper metadata as a BibTeX block, ready to paste into a LaTeX or Markdown References section.

    Reads from the local SQLite store (offline). Use after a research session
    to assemble the References for a generated report.

    Usage:
      User: "Export BibTeX for the papers we discussed."
        -> export_bibtex(paper_ids="arxiv:2310.11511,arxiv:2005.11401")

    Args:
        paper_ids: Comma-separated paper_ids to include in the BibTeX block.
    """
    _ensure_paper_rag_importable()
    from paper_rag.tools.bibtex_export import BibtexExportInput, export_bibtex

    pids = [p.strip() for p in paper_ids.split(",") if p.strip()]
    res = export_bibtex(BibtexExportInput(paper_ids=pids))
    return json.dumps(res, ensure_ascii=False, indent=2)


@tool("paper_deliver", parse_docstring=True)
def paper_deliver_tool(
    format: str,
    paper_ids: str,
    title: str = "",
    options_json: str = "{}",
) -> str:
    """Generate a ready-to-use deliverable from indexed papers.

    Supported formats (M10 / ADR-0016):
      - markdown_survey  Multi-paper Markdown literature survey with cites
      - pptx             12-slide reading-group / academic deck (requires .[deliver])
      - docx             Formatted Word document (requires .[deliver])
      - latex_bib        Zip of references.bib + related_work.tex

    Returns a JSON object with `filename`, `content_base64`, `content_type`,
    `size_bytes`, and `metadata` (n_papers, n_citations, abstain_decisions,
    papers_skipped, etc.). Decode base64 to obtain the binary file.

    Usage:
      User: "Make a survey of the RAG papers we ingested."
        -> paper_deliver(format="markdown_survey", paper_ids="arxiv:...,arxiv:...")

      User: "Generate reading group slides for Self-RAG."
        -> paper_deliver(format="pptx", paper_ids="arxiv:2310.11511",
                         title="Self-RAG Reading Group")

    Args:
        format: One of markdown_survey, pptx, docx, latex_bib.
        paper_ids: Comma-separated paper_ids.
        title: Optional human-readable title (used for filename + cover).
        options_json: JSON object with format-specific options. e.g. for
            markdown_survey: '{"max_words": 5000}'.
    """
    import base64

    _ensure_paper_rag_importable()
    from paper_rag.deliver import dispatch

    pids = [p.strip() for p in paper_ids.split(",") if p.strip()]
    try:
        options = json.loads(options_json) if options_json else {}
    except json.JSONDecodeError:
        options = {}

    result = dispatch(
        format,
        pids,
        title=title or None,
        options=options,
    )
    return json.dumps({
        "format": result.format,
        "filename": result.filename,
        "content_base64": base64.b64encode(result.content_bytes).decode("ascii"),
        "content_type": result.content_type,
        "size_bytes": len(result.content_bytes),
        "metadata": result.metadata,
    }, ensure_ascii=False, indent=2)
