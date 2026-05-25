# Contributing to paper_rag

欢迎贡献！下面是规范。

## 开发环境

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev,mineru]

# Strongly recommended — runs the same ruff rules CI does on every commit:
pip install pre-commit
pre-commit install
```

The `.pre-commit-config.yaml` mirrors `.github/workflows/ci.yml`, so a clean
`pre-commit run --all-files` is a reliable predictor of a green CI run.

## 分支与提交

- 分支命名：`feat/<issue#>-<slug>` / `fix/<issue#>-<slug>` / `docs/<slug>` / `chore/<slug>`
- 提交信息建议：`<type>(<scope>): <summary>` — type ∈ {feat, fix, docs, refactor, test, chore}
- 例：`feat(ingest): cross-source dedup by DOI #2`

## PR 检查清单

- [ ] 关联 issue 编号（标题或描述里写 `#<n>`）
- [ ] 改动 ≥ 100 行 → 至少新增/修改一处测试
- [ ] 改 schema / 配置 → 同步 ADR 或在现有 ADR 加 `### Update YYYY-MM-DD`
- [ ] 影响 `paper_id` / chunk schema / embedding 模型 → 写迁移说明
- [ ] `make lint` 与 `make test-pure` 通过
- [ ] 更新 CHANGELOG `[Unreleased]` 段

## 写代码

- 风格：`ruff` 默认设置（见 `pyproject.toml`），最大行宽 100
- 类型注解：函数签名都加上
- 日志：`from paper_rag.utils.logger import get_logger; log = get_logger(__name__)`
- 配置：所有可调参数从 `paper_rag.config.load()` 拿，**不在代码里硬编码**

## 测试

- **纯逻辑测试**（`tests/test_*_pure.py` / `tests/test_pure.py`）必须在零外部依赖（无 Qdrant / 无 LLM / 可无 sqlmodel）下能跑过
- 依赖外部服务的测试要能 skip：用 `try/except ImportError: return`，不要 hard fail
- 跑全套：`make test-pure`

## 写文档

| 类型 | 落在哪 |
|---|---|
| 新决策 | `docs/adrs/NNNN-<slug>.md`（提交一份就 freeze 文本，更新写 ### Update） |
| 改架构图 / 数据流 | `docs/ARCHITECTURE.md` |
| 改部署或调试 | `docs/OPERATIONS.md` |
| 加迭代项 | 开一个 [GitHub Issue](https://github.com/TongTong0828/paper-rag-agent/issues) |
| 标已完成 | `docs/STATUS.md` |

## 加新工具（暴露给 LLM Agent）

1. 在 `src/paper_rag/tools/<name>.py` 实现，参数用 pydantic
2. 在 `src/paper_rag/tools/_schema.py` 加输入 schema
3. 在 `src/paper_rag/tools/__init__.py` 的 `__getattr__` 加路由（懒加载）
4. 在 `backend/packages/harness/deerflow/community/paper_rag/tools.py` 加 `@tool` 包装
5. 更新 `skills/custom/paper-research/SKILL.md` 的工具表

## 加新 source（采集器）

1. 继承 `ingest.sources.PaperSource`
2. 落盘约定：`data/papers/{paper_id}/{raw.pdf, meta.json, source.txt}`
3. 在 `scripts/ingest_batch.py` 的 `_route()` 加 prefix 识别

## 安全 / 隐私

- 不提交真实 API key / `.env` / `data/` 内容
- 评测集若含敏感问题 → `tests/eval/qa_set.private.jsonl`（已 gitignore）
