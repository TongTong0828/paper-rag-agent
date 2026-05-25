#!/usr/bin/env bash
# paper_rag container entrypoint — dispatches on $1 or $PAPER_RAG_MODE.
#
# Modes:
#   idle      — sleep loop; intended for `docker exec` ad-hoc CLI work
#   cli       — run an arbitrary command (`docker run ... cli python scripts/ask.py "..."`)
#   proactive — APScheduler loop: daily digest 08:00 + weekly stale Mon 09:00
#   jupyter   — jupyter lab on :8888 (requires jupyter installed in image)
#   shell     — drop into bash (debug)
#
# Exit codes:
#   0 — graceful exit
#   1 — unknown mode
#   2 — proactive loop crashed

set -euo pipefail

MODE="${1:-${PAPER_RAG_MODE:-idle}}"
shift || true

echo "[paper_rag] entrypoint mode=${MODE} python=$(python --version 2>&1) cwd=$(pwd)"
python -c "import paper_rag; print('[paper_rag] package:', paper_rag.__file__)"

case "${MODE}" in
  idle)
    echo "[paper_rag] idle mode — exec into the container with: docker exec -it <id> bash"
    # `tail -f /dev/null` is signal-friendly under tini and uses zero CPU
    exec tail -f /dev/null
    ;;

  cli)
    if [ "$#" -eq 0 ]; then
      echo "[paper_rag] cli mode requires a command, e.g. cli python scripts/ask.py 'question'" >&2
      exit 1
    fi
    exec "$@"
    ;;

  proactive)
    echo "[paper_rag] proactive mode — starting APScheduler loop"
    exec python -m paper_rag.proactive.cron_runner
    ;;

  jupyter)
    if ! command -v jupyter >/dev/null 2>&1; then
      echo "[paper_rag] jupyter not installed; rebuild with EXTRAS=jupyter" >&2
      exit 1
    fi
    exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
        --ServerApp.token="${JUPYTER_TOKEN:-paper_rag}" \
        --ServerApp.root_dir=/opt/paper_rag
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    echo "[paper_rag] unknown mode: ${MODE}" >&2
    echo "  valid: idle | cli | proactive | jupyter | shell" >&2
    exit 1
    ;;
esac
