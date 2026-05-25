# ADR-0002 · MinerU 用本地版

- **日期**: 2026-05-13
- **状态**: accepted

## 决策

PDF 解析使用 MinerU 本地版（`magic-pdf` / `mineru` CLI），不走 API。

## 理由

- 用户已有 GPU 友好环境，本地跑可控
- 长期数据量大（目标 100+ 篇起），API 计费/限流会变成瓶颈
- 解析过程要落盘原始 layout.json + figures + tables，本地跑文件更顺
- API 失败时要重试，链路长

## 兜底

- `src/parse/fallback_pymupdf.py`：MinerU 异常或超时时降级为 pymupdf 纯文本，状态进 SQLite 标记 `parsed_with: pymupdf` 供后续重跑
- 配置项 `mineru.fallback_to_pymupdf: true`

## 风险

- 本地模型权重 ~2GB，首次启动慢
- GPU 不在时解析慢但能跑（CPU 也行）
- 复杂表格、跨页公式仍会错；保留 `raw.pdf` 路径供答案跳回原文核对
