# Makefile — unified entry points for paper_rag.

PY ?= python
PYTHONPATH := src:tests

.PHONY: help install install-dev lint format test test-pytest smoke \
        qdrant-up qdrant-down init-store ingest ask eval clean clean-data \
        docker-build docker-build-bake docker-up-proactive docker-cli docker-shell \
        calibrate-abstain hard-cases

help:
	@echo "Targets:"
	@echo "  install        Install runtime deps (editable)"
	@echo "  install-dev    Install dev + mineru extras"
	@echo "  lint           Run ruff check"
	@echo "  format         Run ruff format"
	@echo "  test           Run pure-logic tests (no qdrant / no llm) via _run_tests.py"
	@echo "  test-pytest    Run tests via pytest (richer output, fixtures)"
	@echo "  test-middleware  Run gateway + langgraph middleware tests only"
	@echo "  smoke          Walk all modules and assert importable count"
	@echo "  qdrant-up      Start Qdrant docker container"
	@echo "  qdrant-down    Stop & remove Qdrant container"
	@echo "  init-store     Build SQLite tables + Qdrant collections"
	@echo "  ingest ID=...  Ingest one (e.g. make ingest ID=2310.12345)"
	@echo "  ask Q=...      Ask a question (e.g. make ask Q='What is X?')"
	@echo "  eval           Run retrieval-only eval on example set"
	@echo "  calibrate-abstain  Re-run threshold calibration (offline mode)"
	@echo "  hard-cases     Collect hard cases from feedback events"
	@echo "  docker-build   Build paper_rag image (lean, ~600MB)"
	@echo "  docker-build-bake  Build with bge-m3 pre-warmed (~3GB)"
	@echo "  docker-up-proactive  Start proactive cron sidecar (compose)"
	@echo "  docker-cli CMD='...'  Run one-shot command in fresh container"
	@echo "  docker-shell   Drop into bash inside fresh container"
	@echo "  obs-up         Start Prometheus + Grafana monitoring stack"
	@echo "  obs-down       Stop monitoring stack"
	@echo "  publish        Publish to GitHub (REPO=... WORKDIR=...)"
	@echo "  publish-dryrun Preview what would be committed"
	@echo "  clean          Remove pycache & build artifacts"
	@echo "  clean-data     DELETE data/ (DANGEROUS)"

install:
	$(PY) -m pip install -e .

install-dev:
	$(PY) -m pip install -e .[dev,mineru]

lint:
	ruff check src tests

format:
	ruff format src tests

test:
	@PYTHONPATH=$(PYTHONPATH) $(PY) scripts/_run_tests.py

test-pytest:
	@$(PY) -m pytest -q --ignore=tests/eval

test-middleware:
	@$(PY) -m pytest -q tests/test_middleware.py tests/test_langgraph_middleware.py

smoke:
	@PYTHONPATH=src $(PY) scripts/_run_smoke.py

qdrant-up:
	bash scripts/up_qdrant.sh

qdrant-down:
	-docker rm -f paper-rag-qdrant

init-store:
	$(PY) scripts/init_store.py

ingest:
	@if [ -z "$(ID)" ]; then echo "Usage: make ingest ID=<arxiv-id>"; exit 1; fi
	$(PY) scripts/ingest_one.py --arxiv $(ID)

ask:
	@if [ -z "$(Q)" ]; then echo "Usage: make ask Q='your question'"; exit 1; fi
	$(PY) scripts/ask.py "$(Q)"

eval:
	$(PY) tests/eval/run_eval.py --file tests/eval/qa_set.example.jsonl --retrieval-only

calibrate-abstain:
	$(PY) scripts/calibrate_abstain.py --mode offline \
	    --qa-set tests/eval/qa_set.real.jsonl \
	    --out data/index/abstain_calibration.json

hard-cases:
	$(PY) scripts/collect_hard_cases.py --since 30d \
	    --out tests/eval/hard_cases.jsonl

# ── Docker (M9.5) ────────────────────────────────────────────────────────────
DOCKER_TAG ?= paper-rag:lean

docker-build:
	docker build -t $(DOCKER_TAG) .

docker-build-bake:
	docker build -t paper-rag:bake --build-arg MODE=bake .

docker-up-proactive:
	cd .. && docker compose -f docker/docker-compose.yaml up -d paper_rag_proactive

docker-cli:
	@if [ -z "$(CMD)" ]; then echo "Usage: make docker-cli CMD='python scripts/ask.py ...'"; exit 1; fi
	docker run --rm -it -v $$PWD/data:/opt/paper_rag/data \
	    -v $$PWD/config:/opt/paper_rag/config:ro \
	    --env-file ../.env \
	    $(DOCKER_TAG) cli $(CMD)

docker-shell:
	docker run --rm -it -v $$PWD/data:/opt/paper_rag/data \
	    -v $$PWD/config:/opt/paper_rag/config:ro \
	    --env-file ../.env \
	    $(DOCKER_TAG) shell

# ── Observability stack (M9.7) ───────────────────────────────────────────────
.PHONY: obs-up obs-down

obs-up:
	cd ../docker && docker compose \
	    -f docker-compose.yaml \
	    -f observability/docker-compose.observability.yaml \
	    up -d prometheus grafana
	@echo "Prometheus:  http://localhost:9090"
	@echo "Grafana:     http://localhost:3001  (admin/admin)"

obs-down:
	cd ../docker && docker compose \
	    -f docker-compose.yaml \
	    -f observability/docker-compose.observability.yaml \
	    stop prometheus grafana

# ── GitHub publishing (G4) ──────────────────────────────────────────────────
.PHONY: publish-dryrun publish

publish-dryrun:
	@echo "Files that WOULD be committed (excluding gitignored):"
	@cd $(shell pwd) && git -c core.excludesfile=.gitignore status --porcelain --ignored 2>/dev/null | head -20 || true
	@echo ""
	@echo "Run: make publish REPO=https://github.com/<you>/<repo>.git WORKDIR=$$HOME/<repo>"

publish:
	@if [ -z "$(REPO)" ]; then \
	    echo "Usage: make publish REPO=https://github.com/<you>/<repo>.git [WORKDIR=$$HOME/<repo>]"; exit 1; \
	fi
	bash scripts/publish_to_github.sh $(REPO) $(WORKDIR)

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info

clean-data:
	@echo "About to delete data/. Press Ctrl-C in 5s to abort."
	@sleep 5
	rm -rf data/papers data/parsed data/index
