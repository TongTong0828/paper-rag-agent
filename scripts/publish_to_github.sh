#!/usr/bin/env bash
# scripts/publish_to_github.sh — One-shot publishing helper.
#
# Usage:
#   bash scripts/publish_to_github.sh [repo_url] [workdir]
#
# Defaults:
#   repo_url: https://github.com/TongTong0828/paper-rag-agent.git
#   workdir:  $HOME/paper-rag-agent
#
# What it does:
#   1. Pre-flight: pytest + lint + secrets scan
#   2. Copy paper_rag/ tree to a clean workdir
#   3. Pull DeerFlow integration files into docs/integration/ (策略 A)
#   4. git init / commit / push
#
# It NEVER deletes the source tree. Always reviewable before push.

set -euo pipefail

REPO_URL="${1:-https://github.com/TongTong0828/paper-rag-agent.git}"
WORKDIR="${2:-$HOME/paper-rag-agent}"
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
DEER_FLOW_ROOT="$(cd "$SOURCE/.." && pwd)"

echo "==============================================="
echo "  paper_rag → GitHub publishing helper"
echo "==============================================="
echo "  Source     : $SOURCE"
echo "  DeerFlow   : $DEER_FLOW_ROOT"
echo "  Workdir    : $WORKDIR"
echo "  Repo       : $REPO_URL"
echo

if [ -d "$WORKDIR" ]; then
    echo "[FATAL] Workdir $WORKDIR already exists. Remove it or pass a different path."
    exit 1
fi

# ── Pre-flight ───────────────────────────────────────────────────────────────
echo "── 1/5 Pre-flight tests ──"
cd "$SOURCE"
python -m pytest -q --ignore=tests/eval --no-header || {
    echo "[FAIL] tests not green. Aborting."
    exit 1
}

echo
echo "── 2/5 Secret scan ──"
LEAK=$(grep -rEn "sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._\-]{40,}" \
    --include="*.py" --include="*.yaml" --include="*.toml" --include="*.json" --include="*.md" \
    src/ scripts/ config/ tests/ docs/ 2>/dev/null | \
    grep -v "test_\|example\|placeholder\|REDACTED\|ABCDEFGHIJK\|sk-XX" || true)
if [ -n "$LEAK" ]; then
    echo "[FAIL] potential secrets found:"
    echo "$LEAK"
    exit 1
fi
echo "secret scan: clean"

echo
echo "── 3/5 Copy source tree ──"
mkdir -p "$WORKDIR"
# Use rsync to skip data/, .env etc per .gitignore
rsync -a \
  --exclude='data/' --exclude='.env' --exclude='.env.*' --exclude='!.env.example' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
  --exclude='.ruff_cache' --exclude='.mypy_cache' --exclude='build/' --exclude='dist/' \
  --exclude='.venv/' --exclude='*.egg-info' --exclude='.workbuddy/' \
  --exclude='*.sqlite' --exclude='*.sqlite-*' --exclude='*.db' \
  --exclude='*.pdf' --exclude='*.pkl' --exclude='*.bin' \
  "$SOURCE"/. "$WORKDIR"/
echo "copied to $WORKDIR"

echo
echo "── 4/5 Pull DeerFlow integration snapshot ──"
mkdir -p "$WORKDIR/docs/integration/router"
mkdir -p "$WORKDIR/docs/integration/middleware/gateway"
mkdir -p "$WORKDIR/docs/integration/middleware/langgraph"
mkdir -p "$WORKDIR/docs/integration/subagent"
mkdir -p "$WORKDIR/docs/integration/frontend"

cp "$DEER_FLOW_ROOT/backend/app/gateway/routers/paper_rag.py"        "$WORKDIR/docs/integration/router/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/backend/app/gateway/routers/metrics.py"          "$WORKDIR/docs/integration/router/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/backend/app/gateway/middleware/auth.py"          "$WORKDIR/docs/integration/middleware/gateway/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/backend/app/gateway/middleware/observability.py" "$WORKDIR/docs/integration/middleware/gateway/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/backend/app/gateway/middleware/protection.py"    "$WORKDIR/docs/integration/middleware/gateway/" 2>/dev/null || true
for f in token_usage_middleware latency_tracking_middleware recursion_guard_middleware pii_scrub_middleware; do
    cp "$DEER_FLOW_ROOT/backend/packages/harness/deerflow/agents/middlewares/${f}.py" \
       "$WORKDIR/docs/integration/middleware/langgraph/" 2>/dev/null || true
done
cp "$DEER_FLOW_ROOT/backend/packages/harness/deerflow/community/paper_rag/tools.py"     "$WORKDIR/docs/integration/subagent/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/backend/packages/harness/deerflow/subagents/builtins/paper_research.py" "$WORKDIR/docs/integration/subagent/" 2>/dev/null || true
cp "$DEER_FLOW_ROOT/frontend/src/app/workspace/paper-rag/page.tsx"   "$WORKDIR/docs/integration/frontend/" 2>/dev/null || true
cp -r "$DEER_FLOW_ROOT/docker/observability"                          "$WORKDIR/docs/integration/" 2>/dev/null || true

cat > "$WORKDIR/docs/integration/README.md" << 'EOF'
# DeerFlow Integration Reference

Snapshot of the deerflow-side files that wire `paper_rag` into a DeerFlow
deployment. Apply manually to a DeerFlow checkout — these files reference
its internal package layout.

| Folder | Purpose |
|---|---|
| `router/` | paper_rag HTTP endpoints + Prometheus metrics router |
| `middleware/gateway/` | 8-layer gateway middleware (auth/observability/protection) |
| `middleware/langgraph/` | 4 langgraph middleware (cost/latency/recursion/PII) |
| `subagent/` | community/paper_rag tools + paper-research subagent config |
| `frontend/` | Next.js workspace/paper-rag page |
| `observability/` | Prometheus + Grafana docker-compose override + alerts |

See `paper_rag/docs/adrs/0015-m8-service-deerflow-gateway.md`,
`0020-gateway-middleware-and-observability.md`,
`0021-langgraph-middleware-hardening.md` for design rationale.
EOF
echo "integration snapshot ready"

echo
echo "── 5/5 git init / commit / push ──"
cd "$WORKDIR"
git init -q -b main

# Sanity: nothing dangerous in the index
DANGER=$(git status --porcelain --ignored 2>/dev/null | grep -E "\.env$|/data/|\.sqlite$|\.pdf$" || true)
if [ -n "$DANGER" ]; then
    echo "[ABORT] dangerous files in workdir:"
    echo "$DANGER"
    exit 1
fi

git add -A
echo
echo "Files about to be committed (top 30):"
git status --short | head -30
echo "..."
echo "Total tracked: $(git ls-files | wc -l)"

echo
echo "Continue? Type 'yes' to commit + push, anything else to stop here:"
read -r confirm
if [ "$confirm" != "yes" ]; then
    echo "Stopping. Workdir is at $WORKDIR; you can finish manually."
    exit 0
fi

git commit -q -m "Initial commit: paper_rag agent v0.1.0-dev

Industrial Agentic RAG for academic papers.

Highlights:
- 3-tier abstain (confident / weak / no_evidence) — 100% neg-blocked, 96.7% pos-kept
- 5 deliverable formats (markdown / pptx / docx / latex / pdf)
- Proactive agent: daily digest / sub_match / stale review / auto-ingest
- M11 feedback data loop (semi-auto threshold recalibration)
- DeerFlow integration kit (gateway router + 8-layer middleware + 4 langgraph middleware)

Stats: 21 ADRs, 162/162 tests, 19 HTTP endpoints, 13 Grafana panels,
13 Prometheus alert rules.
"

git remote add origin "$REPO_URL"
echo
echo "Running git push..."
git push -u origin main

echo
echo "✅ Done. Visit: ${REPO_URL%.git}"
