# ADR-0001 · 4 子系统解耦

- **日期**: 2026-05-13
- **状态**: accepted

## 背景

需求一句话包含 4 件事：爬论文、MinerU 切分入库、检索策略、自进化 Wiki，且要 Agentic RAG。

## 决策

按职责拆成 4 个解耦子系统，单独实现、单独验收：

- **A. 采集器** (`src/ingest/`)：拿 PDF + 元数据
- **B. 解析入库** (`src/parse/` + `src/chunk/` + `src/embed/` + `src/store/`)：MinerU → 切分 → 向量化 → 双库
- **C. Agentic RAG 检索** (`src/retrieve/` + `src/rag/` + `src/tools/`)：召回 + rerank + 内部小 agent
- **D. 自进化 Wiki** (`src/wiki/`)：概念抽取 + patch 更新 + 反哺

## 理由

- 各模块输入/输出契约稳定后可独立替换（如 MinerU 换 Marker、bge-m3 换其他模型）
- 阶段 1 只需要打通 A 极简 + B + C 简化版即可验收，不被 Wiki 拖累
- 测试边界清晰：每个子系统都有自己的 test 目录

## 后果

- 多了一层目录层级
- 子系统间 schema（chunk / paper_id）必须先定义稳定
