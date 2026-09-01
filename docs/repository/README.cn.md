[English](../../README.md) | **简体中文** | [日本語](README.ja.md)

[![CI](https://github.com/Hongda-Zhao/EndoViHo-RAG/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Hongda-Zhao/EndoViHo-RAG/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../../LICENSE)

# EndoViHo-RAG

**一个会先查 EVE 数据和论文，再带着证据回答的本地研究工具。**

> 当前是工程预览版，不是已经发布的生物学知识库，也不应被用来自动得出新的生物学结论。

## 30 秒看懂这个项目

RAG 可以理解成一次“开卷考试”：系统不会只靠 AI 的记忆回答，而是先从数据库和论文中
找资料，再根据找到的证据组织答案，并保留出处。

```text
你提出问题 → 查 EVE 数据库和论文 → 找到相关证据 → 整理答案 → 附上来源
```

EVE（内源性病毒元件）是宿主基因组中来源于病毒的序列记录。EndoViHo-RAG 希望让研究者
能够追踪一条记录来自哪里、论文中哪一段支持它，以及系统为什么回答或拒绝回答。

这个仓库主要提供：

- PostgreSQL 中可追踪来源的 EVE 结构化记录；
- 固定论文语料上的关键词检索和语义检索；
- FastAPI、命令行和 Streamlit 网页 Demo；
- 带引用的回答流程，以及数据或证据不完整时的明确拒绝；
- 离线、固定版本、可重复检查的模型比较实验。

它不是开放式聊天机器人。它也不能单独证明感染、流行率、独立整合、共同演化或其他新的
生物学结论。

## 最新结果：哪种文献检索方案更合适？

为了让系统更容易找到正确论文段落，我们比较了 BGE、MedCPT 和 Qwen3。可以把它们想成
不同的“电子图书管理员”：“检索模型”先从全部资料中找候选段落，“重排模型”再把候选段落
重新排序。

所有方案都使用同一批资料、同一批问题和相同的检索规则，只替换模型组合。

![六种文献检索方案的命中数与等待时间对比](../assets/retrieval_ablation_overview.png)

*左图看前 5 条结果中是否找到正确证据；右图看每道题通常需要等待多久。右图使用对数刻度，
因此越靠右代表慢得越明显。*

### 先说结论

1. **暂时保留当前 BGE 方案。** 它在 13 题中找对 11 题，典型等待约 0.08 秒，是目前质量和
   速度最均衡的选择。
2. **MedCPT 检索加重排找得最多。** 它找对 12 题，比当前方案多 1 题，但典型等待约
   2.46 秒，约慢 29 倍。
3. **Qwen3 单独检索时，正确证据通常排得更靠前。** 但它没有增加前 5 条的命中题数，且
   典型等待约 0.99 秒，约慢 12 倍。
4. **当前 Qwen3 重排组合不划算。** 它只找对 10 题，典型等待约 3 分 42 秒。

| 方案 | 前 5 条找到正确证据 | 证据排序分数 | 典型等待 | 白话解释 |
|---|---:|---:|---:|---|
| A · 当前 BGE 方案 | **11 / 13** | 0.716 | **0.08 秒** | 目前最均衡 |
| B · 当前方案 + MedCPT 排序 | 11 / 13 | 0.541 | 2.75 秒 | 更慢，排序也变差 |
| C · MedCPT 检索 | 11 / 13 | 0.651 | **0.05 秒** | 请求最快，但整体排序略弱 |
| C+ · MedCPT 检索 + 排序 | **12 / 13** | 0.599 | 2.46 秒 | 多找对 1 题，但明显更慢 |
| D · Qwen3 检索 | 11 / 13 | **0.742** | 0.99 秒 | 排序最好，但没有多找对题 |
| D+ · Qwen3 检索 + 排序 | 10 / 13 | 0.526 | 3 分 42 秒 | 当前不值得采用 |

“证据排序分数”是 MRR@10，范围为 0–1，越高表示正确证据通常出现得越靠前；“典型等待”是
端到端延迟的中位数，不包含首次加载模型的时间。模型不能只按“有没有在前 10 条找到”来选，
因为读者通常更关心前几条结果是否正确，以及需要等多久。

### 这次比较是否公平？

六种方案固定使用：

- 11 篇相同文献、完全相同的文件字节；
- 1,464 个相同文本片段，没有为某个模型重新切分论文；
- 13 个相同的真实问题；
- 相同的关键词检索、锚点、RRF 合并规则和返回数量；
- 同一台 Apple M2、16 GiB、纯 CPU 设备；
- 本地固定版本且通过校验的模型文件，运行时不联网。

实验在独立 sidecar 中进行，没有覆盖已发布语料、生产 embedding、数据库维度或生产默认值。

> **重要限制：这些是初步结果，不是正式模型选择结论。** 现有 13 题是 legacy gold，目前
> 还没有问题完成专家批准（`approved = 0`），也没有足够的分类、替代证据和排除证据标注。
> 下一步应扩展到 30–50 个专家批准问题后再决定是否替换当前方案。

完整指标、模型版本、资源占用和可复现信息见
[技术报告](../embedding_reranker_ablation.md)与
[机器结果](../../benchmark/embedding_ablation/)。

## 当前能运行到什么程度？

代码、数据库迁移、API、CLI、网页 Demo、Docker Compose 和自动测试都可以运行。但仓库
不会分发真实结构化数据、论文全文或模型权重，也不会自动下载模型。全新安装得到的是空数据库；
需要数据的请求会返回明确的拒绝原因，而不是用示例数据伪装成真实结果。

生产配置目前禁用文本生成模型。换句话说，这个仓库已经具备可审计的工程骨架和真实检索
实验，但还不是一个开箱即用的完整科学数据产品。

## 快速运行

需要 Git 和 Docker Compose。第一次构建需要联网拉取固定版本的容器镜像和依赖。

```sh
git clone https://github.com/Hongda-Zhao/EndoViHo-RAG.git
cd EndoViHo-RAG
cp .env.example .env
docker compose up --detach --build --wait
```

启动后可打开：

- 网页 Demo：<http://127.0.0.1:8501>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

检查服务是否启动：

```sh
curl -sS -w '\nHTTP %{http_code}\n' http://127.0.0.1:8000/health
```

预期输出：

```text
{"status":"ok","service":"EVE Relation RAG","version":"V0"}
HTTP 200
```

这只证明程序已经启动，不代表真实数据、论文或模型已经加载。停止服务并保留本地数据库：

```sh
docker compose down
```

## 开发与验证

项目使用 Python 3.12、uv、PostgreSQL 16 和 pgvector。标准检查命令为：

```sh
. scripts/local-dev-env.sh
uv sync --locked --dev --extra demo
docker compose up -d db
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run mypy src app
uv lock --check
uv run alembic check
```

本地 embedding 运行环境需要单独安装：

```sh
uv sync --locked --dev --extra demo --extra local-embeddings
```

模型必须从本地、固定 revision、通过 SHA-256 清单校验的目录显式加载。系统不会自动发现或
下载模型、数据、论文、release 或 binding。

## 进一步阅读

- [Embedding 与 reranker 完整实验报告](../embedding_reranker_ablation.md)
- [实验设计与安全边界](../embedding_ablation_design.md)
- [实验代码在仓库中的位置](../embedding_ablation_repo_mapping.md)
- [Phase 1 实施说明](../embedding_ablation_phase1.md)
- [MedCPT 768 维 sidecar 方案](../embedding_ablation_768_sidecar_proposal.md)
- [机器可读实验输出](../../benchmark/embedding_ablation/)
- [README 图表生成脚本](../../scripts/plot_readme_embedding_ablation.py)
- [数据语义与科学边界](../data_semantics.md)
- [数据来源说明](../../data/README.md)

## 许可与引用

软件使用 [MIT License](../../LICENSE)。数据、论文和模型各自遵循其来源许可，详见
[DATA_LICENSE](../../DATA_LICENSE)。引用信息见 [CITATION.cff](../../CITATION.cff)。
