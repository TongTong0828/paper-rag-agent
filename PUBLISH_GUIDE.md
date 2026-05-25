# PUBLISH_GUIDE.md — 发布到 GitHub 完整指引

> **目标仓库**: https://github.com/TongTong0828/paper-rag-agent
> **策略**: 把 `paper_rag/` 作为独立仓发布；保留 deerflow 集成代码作为参考（在 `docs/integration/`）

---

## 1. 发布前最终检查（30 秒跑完）

```bash
cd paper_rag

# 1. 测试全绿
python -m pytest -q --ignore=tests/eval

# 2. lint 通过（首次可能要 ruff format 一遍）
ruff check src tests scripts

# 3. 没有任何 secrets
grep -rEn "sk-[A-Za-z0-9]{16,}|api_key.*=.*['\"][^'\"]+['\"]" \
    --include="*.py" --include="*.yaml" --include="*.toml" \
    src/ scripts/ config/ tests/ docs/ 2>/dev/null | \
    grep -v "test_\|example\|placeholder\|REDACTED\|ABCDEF" | \
    head

# 4. 没有 /Users/ 硬编码
grep -rn "/Users/" --include="*.py" --include="*.yaml" --include="*.md" \
    src/ tests/ scripts/ docs/ 2>/dev/null | head

# 5. .env 不会被提交
git check-ignore .env  # 应该输出 .env
```

期望输出：**162 passed / no lint errors / 0 secrets / 0 hardcoded paths / .env ignored**

---

## 2. 发布策略：3 选 1

### 策略 A（推荐）: 独立 paper_rag 仓

把 `paper_rag/` 单独抽出做仓库。优点：

- ✅ 仓库小（~2MB 源码）
- ✅ 不带 DeerFlow 主仓代码（避免许可证 / fork 复杂度）
- ✅ 面试讲故事最干净："这是我从零设计的 RAG 子系统"
- ⚠️ DeerFlow 集成部分需要单独保存到 `docs/integration/`

```bash
# Step 1: 创建独立工作目录
cd ~
mkdir paper-rag-agent
cp -r ~/Desktop/deer-flow/paper_rag/. ~/paper-rag-agent/
cd ~/paper-rag-agent

# Step 2: 把 deerflow 集成片段拷贝到 docs/integration/（可选保留）
mkdir -p docs/integration/{router,middleware/gateway,middleware/langgraph,subagent,frontend}
cp ~/Desktop/deer-flow/backend/app/gateway/routers/paper_rag.py        docs/integration/router/
cp ~/Desktop/deer-flow/backend/app/gateway/routers/metrics.py          docs/integration/router/
cp ~/Desktop/deer-flow/backend/app/gateway/middleware/auth.py          docs/integration/middleware/gateway/
cp ~/Desktop/deer-flow/backend/app/gateway/middleware/observability.py docs/integration/middleware/gateway/
cp ~/Desktop/deer-flow/backend/app/gateway/middleware/protection.py    docs/integration/middleware/gateway/
cp ~/Desktop/deer-flow/backend/packages/harness/deerflow/agents/middlewares/{token_usage,latency_tracking,recursion_guard,pii_scrub}_middleware.py \
   docs/integration/middleware/langgraph/
cp ~/Desktop/deer-flow/backend/packages/harness/deerflow/community/paper_rag/tools.py docs/integration/subagent/
cp ~/Desktop/deer-flow/backend/packages/harness/deerflow/subagents/builtins/paper_research.py docs/integration/subagent/
cp ~/Desktop/deer-flow/frontend/src/app/workspace/paper-rag/page.tsx   docs/integration/frontend/
cp -r ~/Desktop/deer-flow/docker/observability                          docs/integration/

# Step 3: 写 docs/integration/README.md
cat > docs/integration/README.md << 'INTEGRATION_README'
# DeerFlow Integration Reference

These files show how to wire `paper_rag` into the DeerFlow framework.
They are **reference snapshots** — apply manually to a real DeerFlow checkout.

| Folder | Purpose |
|---|---|
| router/ | Mount paper_rag HTTP endpoints on the gateway FastAPI app |
| middleware/gateway/ | 8-layer gateway middleware (auth/observability/protection) |
| middleware/langgraph/ | 4 langgraph middleware (cost/latency/recursion/PII) |
| subagent/ | community/paper_rag tools + paper-research subagent config |
| frontend/ | Next.js workspace/paper-rag page |
| observability/ | Prometheus + Grafana docker-compose override + alerts |

See ADR-0015 / 0020 / 0021 for design rationale.
INTEGRATION_README
```

### 策略 B: Fork DeerFlow + paper_rag 子目录

直接 fork bytedance/deer-flow，paper_rag 在其中。优点：

- ✅ 完整集成可直接跑
- ⚠️ 仓库大（含整个 DeerFlow），git history 嘈杂
- ⚠️ 必须遵守 DeerFlow 上游许可证

### 策略 C: monorepo（paper_rag + 集成片段）

新建仓 `paper-rag-agent/`，根目录是 paper_rag，加 `integrations/deerflow/` 子目录存集成代码。介于 A 和 B 之间。

---

## 3. 推荐 Git workflow（按策略 A）

### 3.1 初始化

```bash
cd ~/paper-rag-agent

# 验证没有 .env / data/ 残留
ls -la .env data/ 2>&1 | head
# (.env 应该被 .gitignore 屏蔽；data/ 应该不存在或被屏蔽)

# 初始化 git
git init
git branch -M main

# 第一次提交（一次性大 commit）
git add -A
git status   # 重点确认：没有 .env、没有 data/、没有 *.sqlite、没有 *.pdf

git commit -m "Initial commit: paper_rag agent — Industrial Agentic RAG for academic papers

Features:
- Agentic QA with 3-tier abstain (confident / weak_evidence / no_evidence)
- 5 deliverable formats (markdown survey / pptx / docx / latex_bib / pdf)
- Proactive agent (daily digest / sub_match / stale review / auto-ingest)
- M11 feedback data loop with semi-auto threshold calibration
- DeerFlow integration via gateway router (19 endpoints) and 4 langgraph middlewares

Stats: 21 ADRs, 162/162 tests passing, 8-layer gateway middleware,
13 Grafana panels, 13 Prometheus alert rules.

See README.md and docs/SYSTEM_DESIGN.md for the 30-min walkthrough."
```

### 3.2 推到 GitHub

```bash
git remote add origin https://github.com/TongTong0828/paper-rag-agent.git
git push -u origin main
```

### 3.3 推送后立刻做 4 件事

1. **GitHub Settings → General → Description**:
   > Industrial-grade Agentic RAG system for academic papers — 5 subsystems, 21 ADRs, 162 tests, full DeerFlow integration kit.

2. **Topics**: 加 `agentic-rag` `llm` `qdrant` `bge-m3` `python` `fastapi` `langgraph` `prometheus` `paper-research`

3. **About → Website**: 留空或填你的博客

4. **Branches → main → Branch protection**:
   - Require pull request reviews（如果有合作者）
   - Require status checks（CI 跑过才允许 merge）

---

## 4. CI 上线即可用

仓库已自带 `.github/workflows/paper_rag.yml`（lint + pytest + docker build smoke + offline calibration sanity）。第一次 push 后 GitHub Actions 会自动跑。

**确保 secrets 不会进 CI**：CI 不需要 OPENAI_API_KEY（用 offline 模式 + stub LLM），所以不用配置 GitHub secrets。

---

## 5. README badges 上线后立刻有效

README 顶部已经有 5 个 badge：

```
[![status](status-0.1.0--dev-orange)]()
[![python](python-3.10%2B-blue)]()
[![tests](tests-149%2F149-success)]()
[![ADR](ADR-21-blue)]()
[![endpoints](HTTP%20endpoints-19-blue)]()
```

第一次推送后 30 秒内全部渲染 OK。

---

## 6. 可选：发布到 PyPI（v0.1.0）

```bash
# 验证 pyproject.toml 有完整的 metadata
python -c "import tomllib; print(tomllib.loads(open('pyproject.toml','rb').read())['project'])" 2>/dev/null || \
python -c "import tomli; print(tomli.loads(open('pyproject.toml','rb').read())['project'])"

# 装 build
pip install build twine

# 构建 sdist + wheel
python -m build

# 上传到 PyPI（先在 https://pypi.org 注册并拿到 token）
twine upload dist/*
```

---

## 7. 第一次 push 前的 dry-run

```bash
# 看一眼会被推上去的文件清单（pre-commit hook 风格）
git ls-files | head -30
echo "..."
git ls-files | wc -l    # 应该在 200-300 范围
echo ""
echo "==== 总大小 ===="
git ls-files | xargs ls -la | awk '{s+=$5} END {print s/1024/1024 " MB"}'
echo ""
echo "==== 大文件 top 10 ===="
git ls-files | xargs ls -la | sort -k5 -n -r | head -10

# 红线检查
echo ""
echo "==== Red flag check ===="
git ls-files | xargs grep -l "sk-[A-Za-z0-9]\{20,\}" 2>/dev/null | head
git ls-files | grep -E "\.env$|\.sqlite$|\.pdf$|\.pkl$|\.bin$" | head
```

期望：**~250 文件，<5MB，0 个大文件超过 100KB（除 dashboard.json），0 个红线匹配**。

---

## 8. 常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| `git push` 拒绝（contains large files） | 之前 commit 过 data/ 或 .env | `git rm --cached data/ -r; git commit --amend` |
| GitHub 报 "secret detected" | 真有 leak 了 | 改秘钥 + `git filter-repo` 清历史 |
| Action 跑不起来 | workflow 语法错或路径错 | 看 Actions tab 报错日志 |
| README badge 不显示 | shields.io 缓存 | 等 5 分钟或加 `?cachebust=1` |
| 别人 fork 跑测试失败 | 路径假设 | 已修：测试用 `Path(__file__).resolve().parents[2]` |

---

## 9. 发布后立刻补一篇 GitHub release notes

```
v0.1.0-dev — Public release

Highlights:
- Agentic RAG with industrial 3-tier abstain (96.7% pos-kept / 100% neg-blocked, calibrated)
- 5-format deliverables (markdown / pptx / docx / latex / pdf)
- Proactive agent with 4 push channels (DingTalk / Feishu / WeCom / SMTP)
- Semi-auto data feedback loop (hard cases → threshold recalibration)
- 8-layer gateway middleware + 19 langgraph middlewares
- 13 Grafana panels + 13 Prometheus alert rules

What's NOT in this release:
- Production-tested at scale (single-node only)
- Multi-tenant isolation beyond user_id (no org / project scoping)
- Online LLM provider failover (single chat_model only)

Next: v0.2 — multi-replica RateLimit (Redis), production calibration data, frontend ingestion UI.
```

---

## 10. 一键发布脚本（可选）

如果你想一条命令搞定，把 §3 的流程写进 `scripts/publish_to_github.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-https://github.com/TongTong0828/paper-rag-agent.git}"
WORKDIR="${2:-$HOME/paper-rag-agent}"

if [ -d "$WORKDIR" ]; then
    echo "Workdir $WORKDIR exists; aborting."
    exit 1
fi

mkdir -p "$WORKDIR"
cp -r ./. "$WORKDIR"/
cd "$WORKDIR"

# Pre-flight
python -m pytest -q --ignore=tests/eval || exit 1
[ ! -f .env ] || rm .env

git init -q
git branch -M main
git add -A
git status
echo "Ready to commit. Type 'yes' to continue:"
read confirm
[ "$confirm" = "yes" ] || exit 1

git commit -m "Initial commit: paper_rag agent v0.1.0-dev"
git remote add origin "$REPO_URL"
git push -u origin main
```

---

祝顺利！
