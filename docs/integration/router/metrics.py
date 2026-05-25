"""Prometheus-format metrics endpoint.

Exposes the in-process counters & histograms recorded by
``paper_rag.observability.metrics``. We avoid the ``prometheus_client``
runtime dependency — paper_rag already implements the Prometheus text
format itself.

Access control
--------------
Unlike the API endpoints, ``/metrics`` does NOT require a BetterAuth
session — it's an ops endpoint. The auth middleware bypasses it via
``_BYPASS_PREFIXES``. In production, restrict via the reverse proxy
(nginx) by allowing only internal CIDRs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """Render Prometheus text format from paper_rag.observability."""
    try:
        # Lazy import — paper_rag may live in a sibling package
        from app.gateway.routers.paper_rag import _ensure_paper_rag_importable

        _ensure_paper_rag_importable()
        from paper_rag.observability.metrics import render

        body = render()
    except Exception as e:
        logger.warning("paper_rag metrics unavailable: %s", e)
        body = (
            "# HELP gateway_metrics_status indicates whether paper_rag metrics "
            "could be rendered\n"
            "# TYPE gateway_metrics_status gauge\n"
            "gateway_metrics_status 0\n"
        )

    return Response(content=body, media_type="text/plain; version=0.0.4")
