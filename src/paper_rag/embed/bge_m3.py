"""BGE-M3 embedder.

Wraps FlagEmbedding.BGEM3FlagModel. Lazy-loaded singleton so import time
stays cheap. Only `dense` vectors are exposed for now.
"""

from __future__ import annotations

from typing import Iterable

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("embed.bge_m3")
_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from FlagEmbedding import BGEM3FlagModel

        c = cfg.load()
        device = c.embedding.device
        if device == "auto":
            # macOS MPS has aggressive memory allocation issues with bge-m3
            # (seen 23GB allocations); fall back to CPU there.
            import platform

            import torch

            if platform.system() == "Darwin":
                device = "cpu"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        # use_fp16 is unsafe on CPU (numerical issues)
        use_fp16 = device != "cpu"
        log.info(f"loading {c.embedding.model_name} on {device} (fp16={use_fp16}, cache={c.paths.models_dir})")
        _MODEL = BGEM3FlagModel(
            c.embedding.model_name,
            use_fp16=use_fp16,
            cache_dir=c.paths.models_dir,
            devices=[device] if device != "auto" else None,
        )
    return _MODEL


def encode(texts: Iterable[str]) -> list[list[float]]:
    c = cfg.load().embedding
    texts = list(texts)
    if not texts:
        return []
    out = _model().encode(
        texts,
        batch_size=c.batch_size,
        max_length=c.max_length,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = out["dense_vecs"]
    return [vec.tolist() for vec in dense]


def encode_one(text: str) -> list[float]:
    return encode([text])[0]
