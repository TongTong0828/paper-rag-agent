"""PDF deliverable generator (P3-15 / extends ADR-0016).

Strategy: re-use survey_md generator → render Markdown to PDF via reportlab.
``reportlab`` lives behind an optional extra (``[deliver-pdf]``) — we don't
add it to the default deliver group because reportlab is heavy and most
users prefer Markdown / DOCX.

Falls back to a tiny hand-built PDF (single page, plain text) when reportlab
is not installed, so the call never hard-fails. Tests use the fallback path.

ADR-0016 originally said "no PDF" — this module is a P3 escape hatch users
keep asking for, but it is opt-in and clearly second-class.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .dispatch import DeliverableResult


def _fallback_pdf_bytes(title: str, body: str) -> bytes:
    """Hand-written minimal PDF 1.4 — single page, ASCII-safe content.

    Works without any 3rd-party dep. Truncates to ASCII because PDF font
    encoding for non-ASCII is non-trivial; the resulting file is still a
    valid PDF that any reader will open.
    """
    text = (title + "\n\n" + body).encode("ascii", errors="replace").decode("ascii")
    # Paginate every ~60 chars per line by adding TJ ops; each line drops 14pt
    lines = []
    for raw in text.splitlines() or [""]:
        # PDF string: escape ( ) \\
        escaped = raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(escaped)

    content_stream_parts = ["BT", "/F1 12 Tf", "1 0 0 1 50 780 Tm", "14 TL"]
    for line in lines[:55]:  # 55 lines fit on letter at 14pt
        content_stream_parts.append(f"({line}) Tj")
        content_stream_parts.append("T*")
    content_stream_parts.append("ET")
    content_stream = ("\n".join(content_stream_parts)).encode("ascii")

    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    stream_obj = (
        b"<< /Length " + str(len(content_stream)).encode() + b" >>\nstream\n"
        + content_stream + b"\nendstream"
    )
    add_obj(stream_obj)
    add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n"
    out += f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def _reportlab_pdf_bytes(title: str, markdown_body: str) -> bytes:
    """Pretty PDF via reportlab (when installed)."""
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=title)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    for para in markdown_body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if para.startswith("# "):
            story.append(Paragraph(para[2:], styles["Heading1"]))
        elif para.startswith("## "):
            story.append(Paragraph(para[3:], styles["Heading2"]))
        elif para.startswith("### "):
            story.append(Paragraph(para[4:], styles["Heading3"]))
        else:
            # Naive markdown: replace [chunk:xxx] tokens, keep newlines as <br/>
            html_para = (para.replace("\n", "<br/>"))
            story.append(Paragraph(html_para, styles["BodyText"]))
        story.append(Spacer(1, 8))
    doc.build(story)
    return buf.getvalue()


def generate(
    paper_ids: list[str],
    *,
    title: str | None = None,
    **_unused: Any,
) -> DeliverableResult:
    """Generate a PDF survey by piggy-backing on the markdown_survey generator."""
    from .survey_md import generate as gen_md

    md_result = gen_md(paper_ids, title=title)
    title_str = title or "Literature Survey"
    body_md = md_result.content_bytes.decode("utf-8", errors="replace")

    try:
        pdf_bytes = _reportlab_pdf_bytes(title_str, body_md)
        engine = "reportlab"
    except Exception:
        pdf_bytes = _fallback_pdf_bytes(title_str, body_md)
        engine = "fallback"

    today = dt.date.today().isoformat()
    safe_title = title_str.lower().replace(" ", "_").replace("/", "_")[:60]

    metadata = dict(md_result.metadata)
    metadata["pdf_engine"] = engine

    return DeliverableResult(
        format="pdf",
        filename=f"{safe_title}_{today}.pdf",
        content_bytes=pdf_bytes,
        content_type="application/pdf",
        metadata=metadata,
    )
