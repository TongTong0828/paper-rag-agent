#!/usr/bin/env bash
# Start a Qdrant single-node container, persisting data under data/index/qdrant_volume.

set -e
cd "$(dirname "$0")/.."

VOLUME_DIR="$(pwd)/data/index/qdrant_volume"
mkdir -p "$VOLUME_DIR"

docker run -d \
  --name paper-rag-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v "$VOLUME_DIR:/qdrant/storage" \
  qdrant/qdrant:latest

echo "Qdrant is starting at http://localhost:6333"
echo "Check: curl http://localhost:6333/collections"
