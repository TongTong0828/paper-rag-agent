"""Smoke test for the skeleton: config loads + paths resolve + ids module works."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_config_loads():
    from paper_rag import config as cfg

    c = cfg.load()
    assert c.embedding.dim == 1024
    assert c.embedding.model_name == "BAAI/bge-m3"
    assert Path(c.paths.data_root).is_absolute()
    assert c.qdrant.collection_chunks == "paper_chunks"
    assert c.qdrant.collection_wiki == "wiki_entries"


def test_paper_id_rules():
    from paper_rag.utils.ids import make_paper_id, normalize_arxiv, to_safe_dirname

    assert make_paper_id(arxiv_id="2310.12345v2") == "arxiv:2310.12345"
    assert make_paper_id(doi="https://doi.org/10.1109/abc.2024.000123") == "doi:10.1109/abc.2024.000123"
    assert normalize_arxiv("https://arxiv.org/abs/2310.12345") == "2310.12345"
    assert to_safe_dirname("arxiv:2310.12345") == "arxiv_2310.12345"


def test_modules_importable():
    import paper_rag.chunk
    import paper_rag.embed
    import paper_rag.ingest
    import paper_rag.parse
    import paper_rag.rag
    import paper_rag.retrieve
    import paper_rag.store
    import paper_rag.tools
    import paper_rag.wiki
