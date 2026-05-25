# OPERATIONS — 部署与运维

> 给跑这套系统的人看。不讨论"为什么"，只讨论"怎么做"。

## 1. 安装

```bash
cd paper_rag

# 推荐用 uv / venv
python -m venv .venv && source .venv/bin/activate

# 最小依赖
pip install -e .

# 想用本地 MinerU 解析
pip install -e .[mineru]

# 开发依赖（测试 + ruff）
pip install -e .[dev]
```

## 2. 起依赖服务

### Qdrant

```bash
bash scripts/up_qdrant.sh
# 验证
curl http://localhost:6333/collections
```

数据卷在 `data/index/qdrant_volume/`，删它等于重建库。

### SQLite

无需启动，`scripts/init_store.py` 自动建表。

```bash
python scripts/init_store.py
```

### LLM（OpenAI-compatible）

```bash
export OPENAI_BASE_URL=https://...
export OPENAI_API_KEY=sk-...
export CHAT_MODEL=...
export SMALL_MODEL=...   # 可选，目前未独立使用
```

## 3. 日常操作

### 入库单篇

```bash
python scripts/ingest_one.py --arxiv 2310.12345
python scripts/ingest_one.py --pdf /abs/path.pdf --title "My Paper"
python scripts/ingest_one.py --arxiv ... --force   # 跳过 dedup
```

### 入库批量

```bash
cat > ids.txt <<EOF
arxiv:2310.12345
arxiv:2308.00352
doi:10.1109/abc.2024.000123
s2:649def34...
url:https://.../paper.pdf
EOF

python scripts/ingest_batch.py --file ids.txt
```

末尾自动重建 BM25。

### 问答

```bash
python scripts/ask.py "What is the main contribution?"
python scripts/ask.py "..." --paper-id arxiv:2310.12345 --top-k 6
python scripts/ask.py "..." --no-llm   # 只看检索结果
```

### 评测

```bash
# 检索过线再上完整 RAG
python tests/eval/run_eval.py --file tests/eval/qa_set.jsonl --retrieval-only
python tests/eval/run_eval.py --file tests/eval/qa_set.jsonl --no-judge
python tests/eval/run_eval.py --file tests/eval/qa_set.jsonl
```

输出落到 `data/index/eval_runs/<ts>.json`。

### Wiki

默认关闭。打开：`config/default.yaml` 改 `wiki.enabled: true` 后再 ingest。

手动跑回顾：

```bash
python scripts/wiki_review.py
```

## 4. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `database is locked` | SQLite 并发写 | 等本迭代 P0-#1 修；临时 `rm data/index/papers.sqlite-journal` |
| `qdrant_client.http.exceptions.UnexpectedResponse` | Qdrant 没起 | `bash scripts/up_qdrant.sh` 或 `docker ps` 看 paper-rag-qdrant |
| `mineru: command not found` | 没装 mineru | `pip install -e .[mineru]` 或在 config 里改 `mineru.fallback_to_pymupdf: true` |
| `Field name "json" in ... shadows attribute` | 旧 pydantic 报警 | 忽略，已用 alias |
| 答案里出现 `[1] [2]` 这种引用 | LLM 没按 prompt 输出 | 等 P0-#3；临时手工核对 |
| 入库很慢 | Wiki trigger 同步阻塞 | 关 `wiki.enabled` 或等 P1-#11 |
| 解析后没图 | MinerU 输出适配未完成 | 等 P0-#5 |

## 5. 备份

- **SQLite**: 直接 `cp data/index/papers.sqlite` 或 `sqlite3 ... .dump > backup.sql`
- **Qdrant**: 停容器 → `tar czf qdrant.tgz data/index/qdrant_volume`
- **BM25 索引**: `data/index/bm25.pkl`；可随时由 SQLite 重建：`python -c "from paper_rag.retrieve import sparse_bm25; sparse_bm25.build_index(force=True)"`
- **MinerU 解析产物**: `data/parsed/` 可丢，重新跑 ingest 会重生（但费时间）
- **原始 PDF**: `data/papers/` 必备份（来源不一定再可达）

## 6. 删库重建

```bash
# 全删
docker rm -f paper-rag-qdrant
rm -rf data/index data/papers data/parsed

# 重做
bash scripts/up_qdrant.sh
python scripts/init_store.py
python scripts/ingest_batch.py --file ids.txt
```

> Embedding 模型一旦换 → 等于换库 → 必须删 Qdrant `paper_chunks` 重做。

## 7. DeerFlow 集成

`paper_rag/` 不需要 `pip install` 也能被 DeerFlow 找到（适配层走 sys.path 兜底，从 deer-flow 根向上找）。生产部署时建议设：

```bash
export PAPER_RAG_HOME=/abs/path/to/paper_rag
```

然后启动 DeerFlow gateway 即可。skill 自动通过 `skills/custom/paper-research/SKILL.md` 注册到 lead agent。
