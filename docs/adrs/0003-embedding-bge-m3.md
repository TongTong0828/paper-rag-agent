# ADR-0003 · Embedding 选 bge-m3

- **日期**: 2026-05-13
- **状态**: accepted

## 决策

向量化模型采用 `BAAI/bge-m3`，1024 维，最长 8192 token。

## 候选与对比

| 模型 | 维度 | 多语言 | 长文本 | 学术效果 | 本地化 | 综合 |
|---|---|---|---|---|---|---|
| **bge-m3** | 1024 | 强 | 8192 | 强 | 是 | ✅ 选定 |
| jina-embeddings-v3 | 1024 | 强 | 8192 | 中 | 是 | 备选 |
| OpenAI text-embedding-3-large | 3072 | 强 | 8192 | 强 | 否（API） | 预算少时 |
| nomic-embed-text-v1.5 | 768 | 中 | 8192 | 中 | 是 | 不选 |

## 理由

- 中英学术混合（用户有中英文论文需求）→ bge-m3 在 MIRACL / MTEB 双榜表现最好
- 本地可跑，无 API 费用，避免长期成本
- 同时支持 dense + sparse + colbert 三种向量（先只用 dense，后续可升级）
- 维度 1024，Qdrant 单机够用

## 不可逆性

**embedding 选定后整库重建成本极高**，所以阶段 0 就钉死。换模型 = `qdrant_volume/` 删库重做。
