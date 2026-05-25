# ADR-0005 · paper_id 三级规则

- **日期**: 2026-05-13
- **状态**: accepted

## 决策

paper_id 全局唯一标识，按优先级生成：

```
arxiv:<arxiv_id>      # 最优，arxiv 论文
doi:<doi>             # 次之，正式发表
sha1:<file sha1>      # 兜底，无元数据的 PDF
```

举例：`arxiv:2310.12345` / `doi:10.1109/abc.2024.000123` / `sha1:9f86d081884c7d65...`

## 文件名安全形式

冒号在文件路径里替换为下划线：`arxiv:2310.12345` → 目录名 `arxiv_2310.12345`。

只在 SQLite/Qdrant payload 里保留原始 `paper_id`，落盘时统一走 `to_safe_dirname()`。

## 去重策略（按顺序）

1. DOI 完全匹配（最稳）
2. arxiv_id 匹配（去掉 v1/v2 版本号后比）
3. 标题归一化匹配：lower + 去标点 + 去空格 + 第一作者姓
4. 文件 SHA1 匹配（兜底）

## 不做语义去重

- 成本高（每篇都要 embedding）
- 容易误杀（survey 和 individual paper 标题相似）
- 4 级规则已能覆盖 99% 场景
