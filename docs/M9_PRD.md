# M9 PRD · 主动 Agent（日报 / 订阅 / 提醒 / 自动 ingest）

- **状态**: draft → approved
- **作者**: paper_rag 团队
- **日期**: 2026-05-21
- **关联 ADR**: 0018
- **预计工程量**: 1-2 周
- **目标里程碑**: M9 主动 Agent

---

## 1 · 目标 (Goals)

把 paper_rag 从"被动答"升级到"主动给" —— 每天有理由打开它：

1. **每日 arxiv 简报**：用户保存的关键词 → 每天 8AM 推 TL;DR
2. **订阅匹配**：新 paper 入库时，主题接近用户订阅的 → 推送
3. **Stale paper 提醒**：用户 30 天没访问的 paper → 推送复习卡片
4. **自动 ingest hook**：聊天里出现 arxiv URL → 后台自动入库
5. **会议日程关联（可选 P2）**：日历有 reading group → 提前 1h 推讨论卡

---

## 2 · 非目标 (Non-Goals)

- ❌ 真实 IM 推送（钉钉/微信/邮件）— v1 走前端 inbox，集成留 M11.5
- ❌ 自然语言订阅（"凡是 RAG 综述都告诉我"）— v1 用关键词 + 主题向量
- ❌ 跨用户协同 ("和你方向相近的人也订阅了 X") — M12
- ❌ 移动端 push notification — 推前端 inbox 即可
- ❌ 预测性推荐（"你可能感兴趣"）— 这是 ML，M13

---

## 3 · 现状 (Context)

### 3.1 已具备
- M8 router + BetterAuth user 隔离
- M11 feedback events（可作信号源）
- WorkBuddy `automation_update` cron 引擎（可调度）
- arxiv source（M0 已有）+ ingest pipeline
- bge-m3 dense 相似度（matching 直接用）

### 3.2 Gap
- ❌ 没有"用户订阅"概念
- ❌ 没有 inbox / 推送通道（用户去哪看推送？）
- ❌ ingest 是命令行触发，不能由 chat 自动触发
- ❌ 没有"用户最后访问 paper 时间"记录

---

## 4 · 用户故事 (User Stories)

### US-1：每日简报（最高频，主入口）
> 张同学：在前端"主题订阅"页加 keyword `Self-RAG`、`agentic RAG`，强度=high。  
> 第二天 8AM：inbox 收到一张卡片"过去 24h arxiv 出了 3 篇 RAG 论文"，含每篇 50 字 TL;DR + "一键 ingest" 按钮。

### US-2：订阅命中
> 李同学：订阅了 `FlashAttention`。某天系统给王同学 ingest 了 FlashAttention v2，李同学的 inbox 立刻收到匹配通知（dense sim > 0.7）。

### US-3：复习提醒
> 王同学：30 天没翻过 Self-RAG 论文。  
> 系统：推一张"复习一下？" 卡片，含上次 highlight 的 3 句话 + 一个 prompt"用一句话总结这篇"。

### US-4：自动 ingest hook
> 赵同学：在 chat 里说"看一下 https://arxiv.org/abs/2401.01313"。  
> 系统：检测到 arxiv URL → 后台自动 ingest（不阻塞答复）→ 入库后回复"已为你入库 'Comprehensive Survey of Hallucination'"。

### US-5：会议关联（P2）
> 系统：检测到日历 16:00 有"reading group: Self-RAG paper"。  
> 15:00 推一张"15 分钟讲解卡 + 5 个潜在追问问题"。

---

## 5 · 数据模型

### 5.1 `subscriptions` 表

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'keyword' | 'topic_vector' | 'arxiv_category'
    value TEXT NOT NULL,         -- keyword 文本 / category code (cs.CL)
    strength TEXT DEFAULT 'normal',  -- low | normal | high (controls match threshold)
    enabled INTEGER DEFAULT 1,
    created_at REAL DEFAULT (strftime('%s','now')),
    last_matched_at REAL
);
CREATE INDEX idx_sub_user ON subscriptions(user_id);
```

### 5.2 `inbox_items` 表（推送通道）

```sql
CREATE TABLE inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'daily_digest' | 'sub_match' | 'stale_paper' | 'auto_ingest'
    title TEXT NOT NULL,
    body_md TEXT,                -- Markdown card body
    actions_json TEXT,           -- [{"label":"Ingest","kind":"ingest","arg":"..."}]
    related_paper_ids TEXT,      -- comma-separated for filtering
    read_at REAL,                -- NULL = unread
    created_at REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX idx_inbox_user_unread ON inbox_items(user_id, read_at, created_at);
```

### 5.3 `paper_access` 表（stale 检测用）

```sql
CREATE TABLE paper_access (
    user_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    last_accessed_at REAL DEFAULT (strftime('%s','now')),
    access_count INTEGER DEFAULT 1,
    PRIMARY KEY (user_id, paper_id)
);
```

每次 `paper_qa_tool` / `paper_search_tool` / `wiki_lookup_tool` 命中 paper_id 时更新这张表（M9.A 末期接入，先建表）。

---

## 6 · 接口设计

### 6.1 Subscriptions CRUD

```http
GET    /api/paper_rag/subscriptions       # 列表
POST   /api/paper_rag/subscriptions       # {kind, value, strength}
DELETE /api/paper_rag/subscriptions/{id}
PATCH  /api/paper_rag/subscriptions/{id}  # toggle enabled
```

### 6.2 Inbox

```http
GET  /api/paper_rag/inbox?unread_only=true&limit=20
POST /api/paper_rag/inbox/{id}/read       # mark read
POST /api/paper_rag/inbox/{id}/dismiss    # delete
```

### 6.3 主动任务触发（运维 + 测试）

```http
POST /api/paper_rag/proactive/digest/run     # 立即跑日报（dev / test）
POST /api/paper_rag/proactive/stale/run      # 立即跑 stale 扫描
POST /api/paper_rag/proactive/match          # body: {paper_id} 立即跑订阅匹配
```

---

## 7 · 调度策略

### 7.1 走 WorkBuddy automation 引擎

不重新发明 cron。`~/.workbuddy/workbuddy.db` 已有 automation 表。我们在系统启动 lifespan 时注册 3 条默认 cron：

| 任务 | RRULE | 说明 |
|---|---|---|
| `paper_rag.daily_digest` | `FREQ=DAILY;BYHOUR=8;BYMINUTE=0` | 用户每人跑一次 |
| `paper_rag.stale_scan` | `FREQ=WEEKLY;BYDAY=SU;BYHOUR=10` | 周日扫一次 stale |
| `paper_rag.feedback_retention` | `FREQ=DAILY;BYHOUR=3` | M11 90d 清理 |

**注**：这是**应用级 cron**——cron 触发的 prompt 实际上是 "调 paper_rag 内部 API"，因为 WorkBuddy automation 是用 LLM agent 跑 prompt 的。考虑成本（每天每用户 1 次 LLM 唤醒），**v1 用 in-process APScheduler 替代**，仅在前端展示用 automation。

### 7.2 v1：in-process APScheduler

gateway lifespan 启动一个 BackgroundScheduler：

```python
sched = AsyncIOScheduler()
sched.add_job(daily_digest_for_all_users, "cron", hour=8)
sched.add_job(stale_scan_for_all_users, "cron", day_of_week="sun", hour=10)
sched.start()
```

- 优点：无外部依赖、随 gateway 进程生死、易调试
- 缺点：单实例（多 worker 时只能 1 个跑）→ 用 `--workers 1` 部署 + 文件锁
- M11+：迁移到 Redis-backed Celery / RQ

---

## 8 · 主动任务实现

### 8.1 daily_digest_for_user(user_id)

```python
def daily_digest_for_user(user_id):
    subs = subscriptions.list_for_user(user_id, kind='keyword')
    if not subs: return  # nothing to digest
    
    # Fetch arxiv past 24h for each keyword
    new_papers = []
    for sub in subs:
        papers = arxiv_source.search(sub.value, days=1)
        new_papers.extend(papers)
    new_papers = dedup(new_papers, key='arxiv_id')
    if not new_papers: return
    
    # TL;DR per paper (small_model, fast)
    bullets = []
    for p in new_papers[:10]:  # cap at 10
        tldr = small_llm_call(f"In 50 words: {p.title}. Abstract: {p.abstract}")
        bullets.append({'paper': p, 'tldr': tldr})
    
    # Render Markdown card
    card = render_digest_card(user_id, bullets)
    inbox.write(user_id, kind='daily_digest', title='今日 arxiv 简报',
                body_md=card.body, actions_json=card.actions)
```

### 8.2 sub_match_on_ingest(paper_id)

新 paper ingest 完成后（hook 进 ingest pipeline）：

```python
def on_paper_ingested(paper_id, user_id_who_ingested):
    paper = sqlite_store.get_paper(paper_id)
    embedding = bge_m3.encode_one(paper.title + " " + paper.abstract)
    
    for sub in subscriptions.iter_active():
        if sub.user_id == user_id_who_ingested:
            continue  # don't notify ingester
        sub_emb = subscriptions.get_embedding(sub)
        sim = cosine(embedding, sub_emb)
        if sim > strength_threshold(sub.strength):  # high=0.6, normal=0.7, low=0.8
            inbox.write(sub.user_id, kind='sub_match',
                        title=f"📚 新 paper 匹配你的订阅 '{sub.value}'",
                        body_md=f"### {paper.title}\n\n{paper.abstract[:200]}...",
                        actions_json=[{"label":"查看","kind":"open","arg":paper_id}],
                        related_paper_ids=paper_id)
            subscriptions.mark_matched(sub.id)
```

### 8.3 stale_scan_for_user(user_id)

```python
def stale_scan_for_user(user_id, days=30):
    cutoff = time.time() - days * 86400
    stale = paper_access.query(user_id, last_accessed_lt=cutoff)
    for pa in stale[:5]:  # top 5 stalest
        paper = sqlite_store.get_paper(pa.paper_id)
        inbox.write(user_id, kind='stale_paper',
                    title=f"🕰 复习一下: {paper.title[:60]}",
                    body_md=...,
                    actions_json=[
                        {"label":"问一句","kind":"qa","arg":f"Summarize {paper.title}"},
                        {"label":"标已读","kind":"mark_accessed","arg":paper.paper_id}
                    ])
```

### 8.4 auto_ingest_hook (chat 流处理)

`qa_agentic.answer()` 入口检测 user question 中的 arxiv URL，**不阻塞**地启动后台 ingest：

```python
def detect_arxiv_urls(text):
    return re.findall(r'arxiv\.org/abs/(\d{4}\.\d{5})', text)

def answer(question, ...):
    arxiv_ids = detect_arxiv_urls(question)
    if arxiv_ids:
        for aid in arxiv_ids:
            asyncio.create_task(background_ingest(aid, user_id, conversation_id))
    return regular_qa_pipeline(...)
```

后台 ingest 完成后写 inbox `kind='auto_ingest'` 通知用户。

---

## 9 · 推送通道

### 9.1 v1：前端 inbox 轮询

前端每 60s 调 `GET /api/paper_rag/inbox?unread_only=true`，未读数显示在导航栏。

### 9.2 后续（M11.5）：

- WebSocket 主动推送
- 邮件 digest（用户配置）
- 钉钉 / 飞书 webhook

---

## 10 · 验收 (DoD)

| # | 验收点 | 验证 |
|---|---|---|
| 1 | 用户可 CRUD 订阅 | 集成测试 |
| 2 | 跑 `digest/run` 端点能产生至少 1 条 inbox（用户有订阅时） | 集成测试 |
| 3 | 新 paper ingest 后能匹配出至少 1 个订阅 user 的 inbox | 集成测试 |
| 4 | stale 扫描能识别 30 天未访问 paper | 单测（mock 时间戳） |
| 5 | chat 含 arxiv URL 时触发后台 ingest | 单测（mock asyncio） |
| 6 | inbox `read_at` / `dismiss` 正确生效 | 集成测试 |
| 7 | 跨用户隔离：A 的 inbox 不可见于 B | 集成测试 |
| 8 | 8 项 proactive 纯逻辑单测全绿 | pytest |

---

## 11 · 风险与缓解

| 风险 | 缓解 |
|---|---|
| arxiv API 429 | M6 已踩过，用 `scripts/ingest_arxiv_direct.py` 直拉 PDF + delay |
| LLM 成本（每用户每日 N 次 small_model 调用） | 缓存 TL;DR，2 个用户订阅同关键词只算一次 |
| inbox 无限膨胀 | 自动 30 天 retention（dismiss 后立即删，read 后 30 天） |
| 单 worker 部署限制 | 用文件锁 + 双 worker 时只让 worker_id=0 跑 cron |
| 用户取消订阅但仍收到 stale 卡片（已被 ingest 的 paper） | stale 只查 enabled=true 的 access 记录 |

---

## 12 · 时间表

| Day | 任务 |
|---|---|
| **D1** | subscriptions / inbox / paper_access 三张表 + 基础 CRUD |
| **D2** | matcher.py（dense sim 匹配）+ 4 项单测 |
| **D3** | digest.py + stale.py + 4 项单测 |
| **D4** | gateway 端点 + APScheduler 接入 + 集成测试 |
| **D5** | auto_ingest_hook + 文档 + 9 端点回归 |

---

## 13 · 后续（M11.5+）

- WebSocket 主动推送
- 钉钉 / 飞书 / 邮件 digest
- 跨用户协同信号（"你方向相近的 N 人也订阅了 X"）
- ML 推荐（M13）
