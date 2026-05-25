"""Token-aware paragraph chunker for text sections.

Splits a section body into ~target_tokens chunks with overlap. Tries to keep
paragraph boundaries: greedily packs paragraphs until target reached.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config as cfg


@dataclass
class TextChunk:
    text: str
    char_start: int
    char_end: int


_ENC = None
_USE_TIKTOKEN = None


def _encoder():
    global _ENC, _USE_TIKTOKEN
    if _USE_TIKTOKEN is False:
        return None
    if _ENC is None:
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding(cfg.load().chunk.text.encoding)
            _USE_TIKTOKEN = True
        except ImportError:
            _USE_TIKTOKEN = False
            return None
    return _ENC


def _count_tokens(s: str) -> int:
    enc = _encoder()
    if enc is None:
        return max(1, len(s) // 4)
    return len(enc.encode(s))


def chunk_text(body: str) -> list[TextChunk]:
    c = cfg.load().chunk.text
    if not body.strip():
        return []

    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    if not paragraphs:
        return [TextChunk(text=body.strip(), char_start=0, char_end=len(body))]

    chunks: list[TextChunk] = []
    buf: list[str] = []
    buf_tokens = 0
    cursor = 0
    chunk_start = 0

    for para in paragraphs:
        ptok = _count_tokens(para)
        if buf and buf_tokens + ptok > c.target_tokens:
            text = "\n\n".join(buf).strip()
            chunks.append(TextChunk(text=text, char_start=chunk_start, char_end=cursor))
            if c.overlap_tokens > 0 and buf:
                tail = buf[-1]
                buf = [tail]
                buf_tokens = _count_tokens(tail)
                chunk_start = cursor - len(tail)
            else:
                buf = []
                buf_tokens = 0
                chunk_start = cursor
        buf.append(para)
        buf_tokens += ptok
        cursor += len(para) + 2

    if buf:
        chunks.append(TextChunk(text="\n\n".join(buf).strip(), char_start=chunk_start, char_end=cursor))
    return chunks
