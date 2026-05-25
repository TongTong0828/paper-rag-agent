"""Configuration loader for paper_rag.

Reads `config/default.yaml`, expands `$ENV_VAR` placeholders, and exposes a
typed config object via `load()`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"


class _Paths(BaseModel):
    data_root: str
    papers_dir: str
    parsed_dir: str
    index_dir: str
    sqlite_path: str
    bm25_path: str
    models_dir: str


class _Embedding(BaseModel):
    provider: str = "bge-m3"
    model_name: str = "BAAI/bge-m3"
    dim: int = 1024
    batch_size: int = 32
    max_length: int = 8192
    device: str = "auto"


class _Reranker(BaseModel):
    enabled: bool = True
    model_name: str = "BAAI/bge-reranker-v2-m3"
    cache_dir: str | None = None
    use_fp16: bool = True
    top_k: int = 8


class _Qdrant(BaseModel):
    url: str = "http://localhost:6333"
    local_path: str | None = None  # if set, use embedded Qdrant (no docker)
    collection_chunks: str = "paper_chunks"
    collection_wiki: str = "wiki_entries"
    distance: str = "Cosine"


class _MinerU(BaseModel):
    mode: str = "local"
    cli: str = "mineru"
    timeout_sec: int = 600
    fallback_to_pymupdf: bool = True


class _ChunkText(BaseModel):
    target_tokens: int = 500
    overlap_tokens: int = 50
    encoding: str = "cl100k_base"


class _Chunk(BaseModel):
    text: _ChunkText = Field(default_factory=_ChunkText)
    context_prefix: str = "[Title: {title}] [Section: {section}]\n"


class _Retrieve(BaseModel):
    top_k_dense: int = 20
    top_k_bm25: int = 20
    rrf_k: int = 60
    rerank_top_k: int = 8
    sparse_backend: str = "fts5"


class _Abstain(BaseModel):
    """Three-way abstain decision based on retrieval evidence quality.

    Calibrated by ``scripts/calibrate_abstain.py`` against a labeled eval run
    (positives + no-answer negatives). Defaults err on the conservative side
    (enabled but with low/high thresholds that only catch the most obvious
    no-evidence cases — the typical fpr=0 operating point on the M6 33-question
    set).
    """
    enabled: bool = True
    threshold_low: float = 0.20      # < low      -> no_evidence (LLM skipped)
    threshold_high: float = 0.40     # >= high    -> confident (normal flow)
    min_chunks: int = 3              # avg top-N chunk scores for decision
    no_evidence_message: str = (
        "未在已索引文献中找到与该问题相关的内容。请确认问题与已入库的论文主题"
        "相符，或考虑通过 paper_ingest_tool 扩充语料库。"
    )


class _Rag(BaseModel):
    max_inner_iters: int = 3
    max_inner_tokens: int = 8000
    enable_hyde: bool = True
    enable_reflect: bool = True
    qa_cache_enabled: bool = False
    qa_cache_ttl_hours: int = 24
    abstain: _Abstain = Field(default_factory=_Abstain)


class _LlmTemperatures(BaseModel):
    """Per-role LLM temperatures.

    Picked once based on offline calibration; centralised here so that a
    single config tweak rolls out to every call site (qa_agentic, qa_stream,
    deliver/survey, deliver/latex_bib, wiki/flow, query_rewrite).
    """
    answer: float = 0.2          # qa_agentic main answer
    stream: float = 0.2          # qa_stream main answer
    rewrite: float = 0.3         # query_rewrite — wider for paraphrase diversity
    wiki: float = 0.2            # wiki concept create / patch
    survey: float = 0.3          # deliver/survey_md narrative
    latex_bib: float = 0.3       # deliver/latex_bib narrative


class _Llm(BaseModel):
    provider: str = "openai_compatible"
    base_url: str | None = None
    api_key: str | None = None
    chat_model: str | None = None
    small_model: str | None = None
    temperatures: _LlmTemperatures = Field(default_factory=_LlmTemperatures)


class _Wiki(BaseModel):
    enabled: bool = False
    similarity_threshold: float = 0.85
    rate_limit_hours: int = 24
    self_eval_threshold: float = 0.7


class _Logging(BaseModel):
    level: str = "INFO"
    json_format: bool = Field(default=False, alias="json")

    model_config = {"populate_by_name": True}


class AppConfig(BaseModel):
    paths: _Paths
    embedding: _Embedding = Field(default_factory=_Embedding)
    reranker: _Reranker = Field(default_factory=_Reranker)
    qdrant: _Qdrant = Field(default_factory=_Qdrant)
    mineru: _MinerU = Field(default_factory=_MinerU)
    chunk: _Chunk = Field(default_factory=_Chunk)
    retrieve: _Retrieve = Field(default_factory=_Retrieve)
    rag: _Rag = Field(default_factory=_Rag)
    llm: _Llm = Field(default_factory=_Llm)
    wiki: _Wiki = Field(default_factory=_Wiki)
    logging: _Logging = Field(default_factory=_Logging)


def _expand_env(value: Any) -> Any:
    """Recursively replace `$VAR` strings with environment values (or None)."""
    if isinstance(value, str) and value.startswith("$"):
        return os.environ.get(value[1:])
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _resolve_paths(raw: dict[str, Any]) -> dict[str, Any]:
    """Make path strings absolute relative to project root."""
    paths = raw.get("paths", {})
    for k, v in list(paths.items()):
        if isinstance(v, str) and v.startswith("./"):
            paths[k] = str((PROJECT_ROOT / v[2:]).resolve())
    raw["paths"] = paths
    return raw


@lru_cache(maxsize=1)
def load(path: str | Path | None = None) -> AppConfig:
    """Load and cache application config.

    Override order:
      1. Explicit `path` argument
      2. Env var `PAPER_RAG_CONFIG` (absolute or relative to project root)
      3. `config/default.yaml`
    """
    if path is not None:
        cfg_path = Path(path)
    elif os.environ.get("PAPER_RAG_CONFIG"):
        env_path = Path(os.environ["PAPER_RAG_CONFIG"])
        cfg_path = env_path if env_path.is_absolute() else (PROJECT_ROOT / env_path)
    else:
        cfg_path = DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw = _expand_env(raw)
    raw = _resolve_paths(raw)
    return AppConfig.model_validate(raw)


__all__ = ["AppConfig", "load", "PROJECT_ROOT"]
