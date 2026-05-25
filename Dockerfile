# paper_rag — embeddable Python package (M8 / ADR-0015, hardened in M9)
#
# Role
# ----
# This Dockerfile is OPTIONAL. The primary integration with DeerFlow is via
# the gateway container importing paper_rag as a sibling Python package
# (PAPER_RAG_HOME env var) — see ../backend/Dockerfile.
#
# Use this image when:
#   - You want a STANDALONE paper_rag CLI container (ingest scripts, calibration)
#   - You want to pre-warm bge-m3 model cache into a shared volume before
#     gateway boots (MODE=bake at build time)
#   - You want to run M9 proactive cron jobs (digest / stale) in a sidecar
#     (MODE=proactive at runtime)
#
# It is NOT used in the default `make up` flow. Gateway gets paper_rag via
# volume mount + sibling install. See docker/docker-compose.yaml.
#
# Build matrix
# ------------
#   docker build -t paper-rag:lean              .                    # default, slim
#   docker build -t paper-rag:bake --build-arg MODE=bake .            # pre-warm bge-m3
#   docker build -t paper-rag:full --build-arg EXTRAS=deliver,deerflow .
#
# Runtime modes (override CMD or set env PAPER_RAG_MODE)
# ------------------------------------------------------
#   idle      — sleep loop, exec into for ad-hoc CLI (default)
#   cli       — run a one-shot script then exit (override CMD)
#   proactive — APScheduler loop running daily digest + weekly stale scan
#   jupyter   — launch jupyter lab on :8888 for exploration

ARG PYTHON_VERSION=3.10
ARG APT_MIRROR=
ARG PIP_INDEX_URL=https://pypi.org/simple

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG APT_MIRROR
ARG PIP_INDEX_URL
ARG EXTRAS=""

# Optional apt mirror override for restricted networks
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# build-essential for FlagEmbedding C extensions; libsqlite3-dev for FTS5 build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libsqlite3-dev curl ca-certificates git \
        && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /opt/paper_rag

# Copy declarative metadata first to leverage Docker layer cache for deps
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config

# Install paper_rag + optional extras into a venv we copy into runtime stage
RUN python -m venv /opt/venv \
 && . /opt/venv/bin/activate \
 && pip install --upgrade pip \
 && if [ -n "${EXTRAS}" ]; then \
        pip install -e ".[${EXTRAS}]"; \
    else \
        pip install -e "."; \
    fi

# Optional: pre-warm bge-m3 weights (~2GB) into HF cache for shared volume
ARG MODE=lean
ENV HF_HOME=/opt/paper_rag/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/paper_rag/models \
    TRANSFORMERS_CACHE=/opt/paper_rag/models
RUN if [ "$MODE" = "bake" ]; then \
        . /opt/venv/bin/activate \
        && python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=False)" ; \
    fi

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG APT_MIRROR

RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# Runtime-only deps: libsqlite3 (for FTS5), curl (healthcheck), tini (PID 1)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsqlite3-0 curl ca-certificates tini \
        && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PAPER_RAG_HOME=/opt/paper_rag \
    HF_HOME=/opt/paper_rag/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/paper_rag/models \
    TRANSFORMERS_CACHE=/opt/paper_rag/models \
    PAPER_RAG_MODE=idle

# Copy venv + source from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/paper_rag /opt/paper_rag

WORKDIR /opt/paper_rag

# Non-root user (UID 1001 to avoid clash with host)
RUN groupadd --system --gid 1001 paperrag \
 && useradd  --system --uid 1001 --gid paperrag --home-dir /opt/paper_rag --shell /bin/bash paperrag \
 && mkdir -p /opt/paper_rag/data /opt/paper_rag/models /opt/paper_rag/logs \
 && chown -R paperrag:paperrag /opt/paper_rag /opt/venv

# Persistent volumes for data + model cache
VOLUME ["/opt/paper_rag/data", "/opt/paper_rag/models", "/opt/paper_rag/logs"]

# Bundled entrypoint dispatches on PAPER_RAG_MODE
COPY --chown=paperrag:paperrag docker-entrypoint.sh /usr/local/bin/paper-rag-entrypoint
RUN chmod +x /usr/local/bin/paper-rag-entrypoint

USER paperrag

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import paper_rag, sys; sys.exit(0)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paper-rag-entrypoint"]
CMD ["idle"]
