# Embedding 与 Reranker 对照实验（preliminary）

> **状态：preliminary / 不可作为正式模型选择结论。** 当前 13 个 legacy gold 问题尚未完成专家 category、alternative/excluded evidence 与 approved review；因此本报告不进入 trusted benchmark，也不提供按类别结论。

## 冻结输入

- Corpus：`corpus:endoviho-rag:v0:20260829:001`；11 documents，1,464 chunks。
- Gold：13 个现有真实问题；legacy gold SHA-256 `ded2a89f666ee8293cb422abecd95581688bd3e626018c25df5f2fd7097b7d2b`。
- 检索：相同 PostgreSQL FTS、anchors、full/title-abstract branches、RRF k=60、top_k=10。
- 硬件：Apple M2，16 GiB，CPU-only；每系统 warmup 1 次、测量 1 次/问题。
- 模型加载时间不计入请求延迟；sidecar 构建耗时另列于 resource CSV。

## 质量与请求延迟

| 系统 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | E2E p50 (ms) | E2E p95 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A · BGE-small baseline | 0.538 | 0.846 | 0.846 | 1.000 | 0.716 | 0.786 | 84.2 | 128.0 |
| B · BGE + MedCPT CE (top 20) | 0.385 | 0.769 | 0.846 | 0.846 | 0.541 | 0.617 | 2,752.0 | 3,109.2 |
| C · MedCPT retrieval (768d) | 0.538 | 0.692 | 0.846 | 0.923 | 0.651 | 0.716 | 54.2 | 100.4 |
| C+ · MedCPT retrieval + MedCPT CE (top 20) | 0.385 | 0.923 | 0.923 | 1.000 | 0.599 | 0.698 | 2,460.2 | 3,087.2 |
| D · Qwen3 embedding (384d) | 0.615 | 0.846 | 0.846 | 1.000 | 0.742 | 0.804 | 985.8 | 1,439.0 |
| D+ · Qwen3 embedding + Qwen3 reranker (top 20) | 0.385 | 0.615 | 0.769 | 0.923 | 0.526 | 0.619 | 221,689.1 | 312,625.1 |

## Preliminary 观察

- Recall@5 最高：C+ · MedCPT retrieval + MedCPT CE (top 20)（0.923）。
- MRR@10 最高：D · Qwen3 embedding (384d)（0.742）。
- 请求 p50 最低：C · MedCPT retrieval (768d)（54.2 ms）。
- 这些观察只描述当前 13 题；不能替代 30–50 题专家 approved benchmark。

## 可复现输出

机器结果位于 `benchmark/embedding_ablation/`。报告由 `summary.json` 与 `experiment_manifest.json` 确定性生成；plot-ready CSV 包含 quality、latency、resource 与 reranking rank shift。`retrieval_by_category.csv` 仅含表头，因为当前没有获批类别。
