"""Tools exposed to LLM agents.

Each tool is imported lazily so missing optional deps (e.g. sqlmodel) on a
specific tool don't break the others.
"""

from __future__ import annotations


def __getattr__(name: str):
    if name == "paper_qa":
        from .paper_qa import paper_qa as fn
        return fn
    if name == "paper_search":
        from .paper_search import paper_search as fn
        return fn
    if name == "paper_section":
        from .paper_section import paper_section as fn
        return fn
    if name == "paper_compare":
        from .paper_compare import paper_compare as fn
        return fn
    if name == "wiki_lookup":
        from .wiki_lookup import wiki_lookup as fn
        return fn
    if name == "export_bibtex":
        from .bibtex_export import export_bibtex as fn
        return fn
    raise AttributeError(name)


__all__ = ["export_bibtex", "paper_compare", "paper_qa", "paper_search", "paper_section", "wiki_lookup"]
