# EVE Relation RAG V0 — Agent Build Guide

> **Version:** V0  
> **Status:** Foundation construction  
> **Product language:** English only  
> **Guide language:** Chinese, with English technical object names  
> **Purpose:** 指引 Codex/Agent 在不扩张科学定义的前提下，逐步构筑一个可运行、可测试、可审计的 Hybrid RAG MVP。

## 0. 文档边界与优先级

本指南融合两类既有内容：

1. 通用 V0 规范中的审计、版本、证据、查询安全和发布原则；
2. EVE Relation RAG Agent Build Guide 中的具体产品目标、技术栈和 milestone 执行方式。

它不自行定义新的病毒学规则。发生冲突时按以下优先级处理：

```text
用户明确确认的当前决定
→ docs/data_semantics.md 中已记录的科学基础
→ 本融合指南中的工程合同
→ 两份源指南中的背景或未来建议
```

源指南中出现某个未来对象、字段、状态或方法，不等于已经获得实现授权。Agent 只能实现用户明确指定的当前 milestone。

### 0.1 版本规则

当前项目统一称为 `V0`。本指南不引入 minor、patch 或 alpha 版本号。

数据将来仍需要 immutable `DatasetRelease` 来保证答案可复现，但 release key 的命名规则尚未确认，不能由 Agent 自行决定。

### 0.2 两条工作线

科学定义和软件构筑是两条相关但不同的工作线：

```text
Scientific foundation
    决定什么对象可以称为 EVE

Software milestones
    决定怎样保存、检索、引用和展示已确认的数据
```

软件不得反向修改科学定义。

## 1. 已确认的科学基础

### 1.1 Scope

The definition is intended for all eukaryotic hosts and all viral lineages.

### 1.2 EVE definition

> **An endogenous viral element (EVE) is a continuous virus-like gene fragment embedded in a eukaryotic host genome and flanked on both sides by host genomic sequence.**

### 1.3 Basic structure

```text
eukaryotic host genomic sequence
    -- continuous virus-like gene fragment --
eukaryotic host genomic sequence
```

### 1.4 当前定义没有决定什么

上述句子目前没有定义：

- host flank 的最小长度；
- virus-like gene fragment 的检测工具、数据库或阈值；
- 坐标体系；
- 相邻、重叠或嵌套候选的 split、merge 和 deduplication；
- 跨 assembly 的位点等价关系；
- release inclusion 规则。

这些内容必须逐项讨论和确认。Agent 不得填入默认值。

## 2. V0 产品目标

V0 的目标是完成一个真正的 Hybrid RAG MVP，而不是只做 SQL 查询，也不是一次实现完整科研知识平台。

用户以英文提问。系统根据问题进入以下路线之一：

```text
structured
    PostgreSQL 返回 assembly、locus、lineage、assertion、evidence 和精确计数

literature
    从固定、版本化的论文语料中检索定义、方法、证据和限制

hybrid
    先得到 StructuredResult
    → 从结构化结果提取 locus、lineage、method、document anchors
    → 检索直接相关的论文片段
    → LLM 仅根据 ContextPack 组织带引用答案

unsupported
    问题超出 V0 能力；事实查询不执行
```

PostgreSQL 是唯一结构化真值层。文献检索结果用于解释，不能覆盖数据库事实。LLM 不能判定 EVE、修改数字、修改 ID、修改坐标或执行任意 SQL。

## 3. V0 范围

### 3.1 必须支持

- 按完整 assembly accession 查询 release 中纳入的 EVE loci；
- 按 locus key 查询其 assembly、sequence、坐标、calls、assertions、evidence 和来源；
- 按 assembly source lineage 查询 loci；
- 按 viral lineage 查询 assemblies、source taxa 和 loci；
- 计算定义明确的 distinct locus、assembly、assembly source taxon 和 detection call 数量；
- 从固定论文语料回答定义、方法、证据和限制问题；
- 将结构化结果与论文解释组合成带引用的 Hybrid answer；
- 未解析、歧义、不支持或非法请求 fail closed；
- 每个公开结构化结果绑定 immutable published `DatasetRelease`；
- 提供 API、CLI、简单 Demo、测试、benchmark 和 Docker Compose。

### 3.2 明确不做

- prevalence、percentage 或 biological frequency；
- screened-negative 或 biological absence；
- host-lineage comparison；
- phylogenetic tree、placement 或 jplace；
- new sequence upload；
- BLAST、HMMER、Foldseek 或 de novo EVE detection；
- GraphRAG 或 Neo4j；
- multilingual queries；
- live web search；
- automatic PDF OCR；
- free-form text-to-SQL；
- LLM-generated arbitrary SQL；
- autonomous multi-agent research。

不支持的问题必须返回结构化拒答，并明确 `fact_retrieval_executed = false`。不得删除失败条件后改查更大范围。

## 4. 科学与工程边界

### 4.1 Assembly source taxon 不等于已证明的古代宿主

数据库使用：

```text
assembly source taxon
assembly source lineage
```

它描述 assembly 的来源分类指派，不自动证明病毒曾感染该现代物种，也不自动证明古代整合宿主。

### 4.2 EVELocus 是 assembly-local object

一个 `EVELocus` 必须绑定：

- versioned assembly identity；
- versioned sequence identity；
- 明确声明的 coordinate convention；
- 合法区间；
- 当前 V0 EVE foundation definition。

它不自动表示：

- 一个独立整合事件；
- 跨 assembly 的正交位点；
- 在整个物种中固定；
- 已完全排除污染或 assembly error。

坐标体系尚未确认，代码和 schema 不得先写死。

### 4.3 Call、locus、assertion 和 release membership 分开

```text
DetectionCall
    某篇来源或某次既有分析报告的候选

EVELocus
    按已确认规则规范化后的 assembly-local coordinate object

ScientificAssertion
    某个有来源的方法对 locus 给出的、带上下文的判断

ReleaseMembership
    当前 DatasetRelease 是否公开包含该 locus 或 assertion
```

V0 不在线执行新的 detection。它只导入、规范化和查询已经存在且来源明确的结果。

### 4.4 Viral lineage 是版本化对象

V0 需要同时容纳正式 taxonomy term 和来源明确的 study-defined lineage。任何 lineage 必须显示 scheme 和 snapshot；study-defined label 不得冒充正式 taxonomy term。

具体采用哪些 lineage authorities 和 snapshots 仍需确认。

## 5. 总体架构

### 5.1 离线流程

```text
Confirmed scientific/data contracts
        ↓
Structured source files
        → normalize and validate
        → PostgreSQL truth tables
        → explicit release membership

Authorized documents
        → parse Markdown / text / JATS XML
        → section-aware chunking
        → PostgreSQL full-text index
        → embeddings
        → pgvector index
        → document anchors

Both branches
        → release validation
        → benchmark
        → publish immutable DatasetRelease
```

V0 不解析任意 PDF。试点语料优先使用 Markdown、plain text、JATS XML 和人工清理的补充材料。

### 5.2 在线流程

```text
English Question
      ↓
Route detection
      ↓
Identifier and entity resolution
      ↓
Validated QueryPlan
      ├──────── structured ────────┐
      │                            │
      ▼                            │
PostgreSQL structured retrieval    │
      ↓                            │
StructuredResult                   │
      ↓                            │
derive anchors                     │
      └──────────────┐             │
                     ▼             │
          literature retrieval ◄───┘
          FTS + pgvector + RRF
                     ↓
              RetrievedChunks
                     ↓
                ContextPack
                     ↓
              constrained LLM
                     ↓
       fact and citation validation
                     ↓
                  Answer
```

## 6. 组件责任

| Component | Responsibility | Must not do |
|---|---|---|
| Router | 选择 structured、literature、hybrid 或 unsupported | 决定科学真值 |
| Resolver | 将 accession、locus key、name 和 alias 解析为 stable key | 删除无法解析的条件 |
| QueryPlanner | 产生受限 QueryPlan | 生成 SQL |
| Validator | 检查 schema、entity、release、metric 和 scope | 校验失败后继续检索 |
| StructuredRepository | 用 SQLAlchemy 执行白名单查询 | 接受任意 SQL 字符串 |
| DocumentRetriever | 执行 FTS、vector search、RRF 和 anchor filtering | 产生结构化计数 |
| ContextBuilder | 合并 StructuredResult、chunks、provenance 和 limitations | 重新计算数字 |
| AnswerComposer | 只根据 ContextPack 生成英文答案 | 使用模型记忆补事实 |
| CitationValidator | 检查 citation ID、locator 和 claim mapping | 声称自动证明科学结论 |
| Renderer | 输出稳定 JSON 和人类可读答案 | 隐藏 warning 或改变结果 |

## 7. V0 概念数据对象

本节只登记对象职责，不冻结具体表字段、结果枚举或坐标格式。具体 schema 在相应决定确认后建立。

### 7.1 Structured truth objects

| Object | Responsibility |
|---|---|
| `DatasetRelease` | 固定一次公开数据发布及其所有依赖 |
| `LineageSnapshot` | 固定 assembly source 或 viral lineage scheme 的一个版本 |
| `LineageTerm` / `LineageAlias` / `LineageClosure` | 保存 term、英文 alias 和层级关系 |
| `GenomeAssembly` | 保存 versioned assembly identity 和来源 artifact |
| `AssemblyTaxonAssignment` | 保存 assembly 在固定 snapshot 中的来源分类指派 |
| `AssemblySequence` | 保存 versioned component sequence identity |
| `EVELocus` | 保存满足已确认 V0 定义的 assembly-local locus |
| `DetectionCall` | 保存既有来源或既有运行报告的候选 call |
| `ScientificAssertion` | 保存方法限定、来源明确的 locus assertion |
| `EvidenceItem` | 保存可定位的论文、表格、文件或分析输出证据 |
| `AssertionEvidence` | 将 evidence 连接到具体 assertion |
| `ReleaseMembership` | 明确当前 release 包含哪些 assemblies、loci 和 assertions |

### 7.2 Literature objects

| Object | Responsibility |
|---|---|
| `CorpusRelease` | 固定一次可检索文献语料发布 |
| `Document` | 保存 document identity、来源、许可和 checksum |
| `DocumentChunk` | 保存 section、locator、chunk index、text 和 checksum |
| `DocumentEmbedding` | 保存指定 embedding model 产生的 chunk vector |
| `DocumentAnchor` | 将 document 与 locus、lineage、method、assembly 或关键词相连，用于约束检索 |

`DocumentAnchor` 是检索信号，不是科学真值。

## 8. QueryPlan 与 fail-closed

V0 使用小型、可理解、可验证的 QueryPlan。示意结构如下：

```json
{
  "plan_version": "V0",
  "route": "hybrid",
  "release_key": "<published_release_key>",
  "intent": "list_loci",
  "question": "<original English question>",
  "assembly_accession": null,
  "locus_key": null,
  "source_lineage_key": null,
  "viral_lineage_key": null,
  "include_descendants": true,
  "metric_key": null,
  "limit": 50,
  "literature_top_k": 8
}
```

示意字段不等于最终 schema。最终字段必须在 Milestone 2 前单独确认。

### 8.1 Intent

候选 intent：

```text
assembly_detail
locus_detail
list_loci
list_assemblies
list_source_taxa
aggregate
explain_method
explain_evidence
```

### 8.2 Metrics

候选 metric：

```text
distinct_included_locus_count
distinct_assembly_count
distinct_source_taxon_count
detection_call_count
```

指标的去重单位和过滤范围必须在实现前确认；未确认的 metric 不进入 API。

### 8.3 Resolver priority

```text
exact assembly accession
→ exact locus key
→ exact stable lineage key
→ exact canonical name
→ exact normalized curated alias
→ suggestion only
```

模糊匹配只能返回 suggestions，不能自动执行事实查询。

### 8.4 Fail-closed

以下情况不得执行结构化事实查询：

- 用户提到的实体没有解析成功；
- 多个候选仍然歧义；
- release 缺失、未发布或依赖不完整；
- metric、intent 或 filter 不受支持；
- 请求 prevalence、absence 或其他非目标能力；
- 任何用户条件在 QueryPlan 中丢失；
- 任何 QueryPlan filter 没有映射到固定 compiler constraint。

合法全库查询必须显式声明全 release scope；不能用解析失败代表查询全部。

## 9. Literature retrieval 与 generation

### 9.1 Corpus

V0 只使用固定、版本化、许可明确的 document corpus。在线查询不实时抓取网页，也不把临时搜索结果混入发布事实。

每个 chunk 至少保留：

```text
document key
section
locator
chunk index
text checksum
parser policy
corpus release
```

### 9.2 Retrieval

对同一个问题执行：

```text
PostgreSQL full-text search
+ pgvector nearest-neighbor search
+ optional metadata/anchor filtering
→ Reciprocal Rank Fusion
→ top_k RetrievedChunks
```

Hybrid route 必须先从 `StructuredResult` 提取 anchors，再优先检索与当前记录直接相连的 documents。若 anchor documents 不足，才扩展到同一 corpus。

V0 不默认实现 reranker；只有 benchmark 证明必要时才能增加。

### 9.3 Providers

`EmbeddingProvider` 和 `LLMProvider` 必须使用抽象接口。测试只使用 deterministic fake providers，不得调用真实付费 API。

### 9.4 ContextPack

LLM 只允许接收：

- original user question；
- validated QueryPlan；
- StructuredResult；
- RetrievedChunks；
- fixed answer instructions。

`ContextPack` 是唯一允许传给 LLM 的事实上下文。

### 9.5 Answer rules

- Write in English.
- Preserve all structured counts, IDs, coordinates, statuses and release keys exactly.
- Do not infer infection, prevalence, absence, co-divergence or independent integration events.
- Every document-derived claim must cite a provided citation ID.
- Do not cite a chunk that does not support the claim.
- When evidence is insufficient, say so explicitly.
- Do not use external knowledge.

Validator 至少检查：

- citation ID 存在；
- literature claim 至少有一个 citation；
- document、locator 和 checksum 存在；
- structured numbers 和 IDs 与 StructuredResult 完全相同；
- 答案没有新增 assembly、locus、lineage 或 DOI。

## 10. 技术栈

V0 工程栈：

```text
Python 3.12
uv
FastAPI
Pydantic v2
PostgreSQL 16+
pgvector
SQLAlchemy 2.x
psycopg 3
Alembic
Typer
pytest
Ruff
mypy
Docker Compose
Streamlit
GitHub Actions
```

数据库 schema 变化必须通过 Alembic migration。测试不得访问真实付费模型。

## 11. 推荐仓库结构

```text
eve-relation-rag/
├── src/eve_relation_rag/
│   ├── api/
│   ├── cli/
│   ├── config/
│   ├── db/
│   ├── ingestion/
│   ├── lineage/
│   ├── planning/
│   ├── retrieval/
│   ├── generation/
│   ├── providers/
│   └── rendering/
├── app/
├── data/
│   ├── pilot/
│   └── synthetic/
├── benchmark/
├── docs/
│   ├── data_semantics.md
│   └── development_status.md
├── tests/
├── compose.yaml
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── .env.example
├── README.md
├── LICENSE
├── DATA_LICENSE
├── CITATION.cff
└── CHANGELOG.md
```

除 `data_semantics.md` 和当前 milestone 明确要求的文件外，不提前创建未来文档占位符。

## 12. Milestones

Agent 每次只能实现一个由用户明确指定的 milestone。

### Milestone 0 — Repository scaffold

实现：

- Python package；
- `uv` / `pyproject.toml`；
- FastAPI health endpoint；
- configuration；
- Alembic configuration and an empty baseline migration, without domain tables；
- Docker Compose with PostgreSQL + pgvector；
- pytest、Ruff、mypy；
- GitHub Actions；
- `docs/development_status.md`。

禁止实现：

- domain database tables；
- pilot importer；
- document ingestion；
- embeddings；
- LLM integration；
- structured、literature 或 hybrid retrieval；
- 任何未确认的科学规则。

退出条件：

```text
docker compose up -d db
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run mypy src
```

所有适用命令必须通过。若某命令因 Milestone 0 尚无相应对象而不适用，必须明确说明，不能伪造成功。

### Milestone 1 — Confirmed contracts and truth layer

前置条件：第 16 节中影响 schema 的决定已经由用户确认。

实现：

- only confirmed domain models and migrations；
- pilot structured importer；
- lineage hierarchy support；
- release membership；
- database constraints；
- release validator。

不得实现未经确认的字段、状态或科学规则。

### Milestone 2 — Structured retrieval

实现：

- confirmed QueryPlan schema；
- English resolver；
- semantic validator；
- fixed SQLAlchemy compiler；
- repositories；
- StructuredResult；
- structured API and CLI。

退出条件包括 exact result-set equality、numeric exact match、unknown entity fail-closed、pagination 不改变 totals，以及禁止任意 SQL。

### Milestone 3 — Literature ingestion and retrieval

实现：

- Markdown、text、JATS importer；
- section-aware chunker；
- full-text index；
- embedding provider；
- pgvector index；
- RRF；
- anchor filtering；
- RetrievedChunks。

不解析任意 PDF，不调用真实付费 provider 进行测试。

### Milestone 4 — Hybrid RAG and generation

实现：

- router；
- hybrid orchestration；
- ContextPack；
- LLM provider；
- answer composer；
- citation and fact validators；
- HybridAnswer。

结构化 facts 必须保持不变，所有 document-derived claims 必须带合法 citation。

### Milestone 5 — Demo and release

实现：

- Streamlit demo；
- README architecture and quick start；
- example questions；
- benchmark report；
- Docker quick start；
- licenses and citation metadata；
- V0 release checklist。

## 13. Benchmark 原则

### 13.1 Structured

Gold set 必须实现：

- entity resolution exact match；
- QueryPlan slot/exact match；
- result-set exact equality；
- numeric exact match；
- unknown entity fail-closed；
- pagination does not alter totals；
- release provenance match。

这些 correctness checks 必须为 100%。

### 13.2 Literature retrieval

试点门槛：

```text
Recall@5 >= 0.80
Recall@10 >= 0.90
citation ID validity = 100%
locator existence = 100%
```

这些门槛只描述固定试点 corpus，不代表整个病毒学领域。

### 13.3 Hybrid generation

必须满足：

```text
structured numbers and IDs unchanged = 100%
all document-derived claims have citations = 100%
no invented record identifiers = 100%
unsupported-request refusal = 100%
```

任何人工审阅认定为 unsupported 的 factual claim 都阻止发布。

## 14. Agent 工作协议

Agent 必须：

1. 完整阅读本指南、`docs/data_semantics.md` 和 `docs/development_status.md`（若已存在）；
2. 每次只实施用户明确指定的一个 milestone；
3. 不自行扩大 scope；
4. 不把源指南中的未来建议当作当前批准；
5. 不创建用户尚未确认的科学定义、字段、枚举、阈值或文档；
6. 先写或同步更新测试，再修改实现；
7. 不让 LLM 生成或执行 SQL；
8. 不把 unresolved entity 转为全库查询；
9. 不自动向非 test 环境写入 demo data；
10. 所有 schema 变化使用 Alembic；
11. 运行并如实报告当前 milestone 的验证命令和结果；
12. 更新 `docs/development_status.md`，但不提前记录未来 milestone 为已完成；
13. 当前 milestone 未达到退出条件时，不进入下一 milestone；
14. 不承诺后台工作或未实际完成的结果。

当一个决定尚未确认时，正确行为是停止相关实现、清楚列出缺失决定，并继续完成不依赖该决定的安全工作。

## 15. V0 Definition of Done

V0 只有在以下条件全部满足时才算完成：

- 产品输入和输出只支持英文；
- PostgreSQL 是唯一结构化真值源；
- scientific foundation、structured facts、literature chunks 和 generated answers 分层；
- structured、literature 和 hybrid 三条路线均可运行；
- 每个 structured result 绑定 published immutable DatasetRelease；
- 每个 locus 有 versioned assembly、versioned sequence 和已确认坐标规则下的合法位置；
- calls、loci、assertions、evidence 和 release membership 分开；
- viral lineage 显示 scheme 和 snapshot；
- 未解析和歧义查询 fail closed；
- LLM 不生成 SQL、不修改 StructuredResult；
- 文献检索使用固定 corpus、FTS、pgvector 和 RRF；
- 每个 document-derived claim 有可定位 citation；
- benchmark 达到本指南门槛；
- Docker 可以从空环境启动；
- pilot release 可以从冻结输入重建；
- GitHub 包含测试、benchmark、license、citation 和 changelog；
- README 明确说明科学与数据覆盖限制。

最终系统可以诚实地说：

> In release R, the database contains these assembly-local EVE loci and these versioned viral-lineage assertions. The literature retrieved for those records describes the supporting methods and limitations at the cited locations.

系统不能说：

> The language model proved that this virus infected this host.

## 16. 尚未确认、不得擅自实现的决定

以下内容必须与用户逐项确认：

1. 内部与公开坐标体系及转换规则；
2. `EVELocus` 的 identity、split、merge 和 deduplication；
3. 如何判定 continuous virus-like gene fragment；
4. 如何判定两侧序列属于 eukaryotic host genomic sequence；
5. 采用哪些 assembly source 和 viral lineage snapshots；
6. 哪些 loci 和 assertions 可以进入一个 published release；
7. pilot dataset 的实际范围、来源与许可；
8. stable key 与 release key 的命名规则。

这些决定不影响 Milestone 0 的通用脚手架，但会阻止相应的 Milestone 1 domain schema 和数据导入。

## 17. 融合来源

本指南融合以下来源，不修改它们：

| Source | SHA-256 | Role |
|---|---|---|
| `EVE_RELATION_V0.md` | `f32622334888c20b28b7c8b93649885eac2e17891b762234b61da977219c378c` | 通用审计、release、QueryPlan、evidence、provenance 和 fail-closed 原则 |
| `EVE_RELATION_RAG_V0_1_AGENT_BUILD_GUIDE.md` | `0ed5b48aa55a09762248d0c18b8e7f9a052328d4126429377f7bb3a9fdbd1547` | Hybrid RAG MVP、技术栈、组件和 milestone |
| remote `docs/data_semantics.md` | `1c849f71a885f1d1218a16f9fff16e30269b85d89d1b3c24911231bba9dbdf43` | 当前唯一确认的 EVE 科学基础 |

本融合稿自身不是对第 16 节未决事项的批准。
