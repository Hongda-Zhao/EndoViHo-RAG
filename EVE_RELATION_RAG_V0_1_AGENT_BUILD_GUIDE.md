EVE Relation RAG v0.1 — Agent Build Guide

用途： 指引 Codex/Agent 构筑第一版可运行、可测试、可发布到 GitHub 的 EVE Relation RAG。
产品语言： English only。
指南语言： 中文，技术对象使用英文名称。
目标版本： v0.1.0-alpha。
核心目标： 完成一个真正的 Hybrid RAG MVP，而不是只完成 SQL 检索，也不是一次性实现完整科研知识平台。

0. 第一版到底要完成什么

第一版必须让用户能够输入英文问题，例如：

Which bivalve assemblies contain Orthopolintovirales-related EVE loci?

What evidence supports locus:eve:000001?

How did the source publications define the criteria used for this locus?

Which bivalve assemblies contain Orthopolintovirales-related loci,
and what methods support those assignments?

系统根据问题选择三条路线之一：

structured
    PostgreSQL 返回 assembly、locus、lineage、assertion、evidence 和计数

literature
    PostgreSQL full-text + pgvector 返回论文段落、方法和限制

hybrid
    先得到 StructuredResult
    → 用 locus / lineage / method / DOI 作为文献检索 anchors
    → LLM 基于被冻结的事实和检索段落生成带引用答案

因此，第一版是一个真正的 RAG 系统：

Retrieval：结构化数据库检索 + 文献检索
Augmentation：把两类检索结果组成 ContextPack
Generation：LLM 只能根据 ContextPack 生成答案

LLM 不是数据库，也不是 EVE 判定工具。它不能修改 SQL 返回的数字、ID、坐标和状态。

1. 第一版的范围

1.1 必须支持

根据 assembly accession.version 查询 included EVE loci。

根据 locus key 查询坐标、calls、assertions、evidence 和来源。

根据 assembly source lineage 查询 viral lineage 关系。

根据 viral lineage 反向查询 assemblies 和 source taxa。

统计：

distinct included locus count；

distinct assembly count；

distinct assembly source taxon count；

detection call count。

从固定论文语料中回答方法、定义、证据和限制问题。

将结构化结果与论文解释组合成带引用的 Hybrid answer。

未解析实体、歧义实体和不支持的问题必须 fail closed。

所有公开结果必须绑定一个 immutable DatasetRelease。

提供 API、CLI、简单 Demo、测试、benchmark 和 Docker Compose。

1.2 第一版明确不做

prevalence / percentage
screened-negative / biological absence
host-lineage compare
phylogenetic tree / placement / jplace
new sequence upload
BLAST / HMMER / Foldseek / de novo EVE detection
GraphRAG / Neo4j
multilingual queries
live web search
automatic PDF OCR
free-form text-to-SQL
LLM-generated arbitrary SQL
autonomous multi-agent research

遇到这些请求时返回：

{
  "status": "unsupported",
  "reason": "...",
  "fact_retrieval_executed": false
}

不得把不支持的问题静默改成另一个问题。

2. 科学边界

2.1 Assembly source taxon 不是已证明的古代宿主

数据库使用：

assembly source taxon
assembly source lineage

不把它直接称为 confirmed host。

系统可以说：

Assemblies assigned to Bivalvia contain release-included loci with supported affinity to Orthopolintovirales.

系统不能自动说：

Orthopolintovirales infected modern bivalves.

2.2 Locus 是 assembly-local coordinate-defined locus

一个 locus 必须绑定：

assembly accession.version
sequence accession.version
start0
end0
strand

内部坐标统一为：

0-based, half-open: [start0, end0)
length = end0 - start0

它不自动表示：

跨 assembly 的正交位点；

一个独立整合事件；

在物种全部个体中固定；

已完全排除污染或 assembly error。

2.3 Call、locus、assertion 和 release inclusion 分开

DetectionCall
    某个来源或运行报告的候选

EVELocus
    规范化后的 assembly-local 坐标对象

ScientificAssertion
    某个方法对 locus 给出的判断

ReleaseMembership
    当前 release 是否公开包含该 locus/assertion

不能用一个 is_eve=true 或一个 viral_taxon 字段替代以上对象。

2.4 Viral lineage 不限于 ICTV 正式 taxon

第一版必须支持版本化 viral lineage scheme，例如：

ICTV taxonomy
study-defined lineage
phylogeny-defined clade label imported as a study term
legacy literature label

例子：

Orthopolintovirales
Asfarviridae-like
Mirusvirus E01
PLV-like

系统必须显示 lineage scheme 和 snapshot，不能把 study-defined term 冒充 ICTV 正式分类。

3. 总体架构

3.1 离线流程

Structured source files
→ normalize and validate
→ PostgreSQL truth tables
→ explicit release membership

Documents
→ parse Markdown / text / JATS XML
→ section-aware chunking
→ full-text index
→ embeddings
→ pgvector index
→ document anchors

Both branches
→ release validation
→ benchmark
→ publish immutable DatasetRelease

第一版不直接解析任意 PDF。试点文献优先准备为：

Markdown
plain text
JATS XML
manually cleaned supplementary tables

PDF adapter 可以后续增加。

3.2 在线流程

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

4. 每个组件负责什么

组件

职责

禁止事项

Router

判断 structured / literature / hybrid / unsupported

不决定科学真值

Resolver

将名称、alias、accession、locus key 映射为 stable key

未解析时不得删除条件

QueryPlanner

产生受限 QueryPlan

不生成 SQL

Validator

检查字段、实体、release、metric 和权限

失败后不得继续检索

StructuredRepository

使用 SQLAlchemy 执行白名单查询

不接受任意 SQL 字符串

DocumentRetriever

FTS + vector search + RRF

不返回结构化计数

ContextBuilder

合并 StructuredResult、chunks、provenance、limitations

不重新计算数字

AnswerComposer

根据 ContextPack 生成英文答案

不使用模型记忆补事实

CitationValidator

检查 citation ID、chunk locator 和 claim mapping

不宣称自动证明科学正确

Renderer

输出稳定 JSON 和人类可读答案

不隐藏 warning

5. 第一版最小数据模型

不要为第一版实现完整平台级 ontology。以下模型足以完成 RAG MVP。

5.1 Release 与 lineage

dataset_release

id
release_key              UNIQUE, public stable key
version
status                   draft | published | retired
published_at
host_lineage_snapshot_id
viral_lineage_snapshot_id
corpus_release_key
manifest_checksum

公开查询只允许 published release。

lineage_snapshot

id
snapshot_key             UNIQUE
domain                   assembly_source | viral
scheme_name
scheme_version
source_artifact
checksum

lineage_term

id
snapshot_id
term_key
canonical_name
rank
parent_term_id           nullable
status                   official | provisional | study_defined
UNIQUE(snapshot_id, term_key)

lineage_closure

snapshot_id
ancestor_term_id
descendant_term_id
depth
UNIQUE(snapshot_id, ancestor_term_id, descendant_term_id)

lineage_alias

snapshot_id
term_id
alias
alias_type               scientific_name | common_name | synonym | abbreviation
normalized_alias
UNIQUE(snapshot_id, normalized_alias, term_id)

只导入英文和 scientific names。

5.2 Assembly 与 locus

genome_assembly

id
assembly_key             UNIQUE
assembly_accession_version
assembly_name
genome_size_bp
source_artifact
checksum

assembly_taxon_assignment

id
assignment_key           UNIQUE
assembly_id
lineage_snapshot_id
taxon_term_id
assignment_policy_key
source_artifact

Assembly 本身不永久绑定一个 taxon。来源分类指派必须绑定固定 lineage snapshot，并由 release 选择。

release_assembly_membership

release_id
assembly_id
taxon_assignment_id
PRIMARY KEY(release_id, assembly_id)

assembly_sequence

id
assembly_id
sequence_accession_version
sequence_length
checksum
UNIQUE(assembly_id, sequence_accession_version)

eve_locus

id
locus_key                UNIQUE
sequence_id
start0
end0
strand
raw_location
identity_policy_key
CHECK(0 <= start0 AND start0 < end0)

第一版只支持单区间 locus。Multipart locus 推迟到后续版本；输入中出现 multipart 时进入 quarantine，而不是错误压缩。

detection_call

id
call_key                 UNIQUE
locus_id
source_type              analysis_run | publication_report
source_key
method_key
native_call_key
result_json

5.3 Assertions 与 evidence

scientific_assertion

id
assertion_key            UNIQUE
locus_id
assertion_type           endogeneity | hcvr | viral_affinity | inclusion
target_lineage_term_id   nullable
decision_code
method_key
run_key
result_json
created_at

约束：

viral_affinity → target_lineage_term_id IS NOT NULL
其他类型       → target_lineage_term_id IS NULL

inclusion decision_code ∈ include | exclude | review
hcvr decision_code      ∈ passed | failed | uncertain
endogeneity             ∈ supported | unsupported | uncertain

evidence_item

id
evidence_key             UNIQUE
evidence_type
source_document_id       nullable
source_artifact
locator
summary
checksum

assertion_evidence

assertion_id
evidence_id
relation                 supports | contradicts | contextualizes
PRIMARY KEY(assertion_id, evidence_id, relation)

release_locus_membership

release_id
locus_id
inclusion_assertion_id
PRIMARY KEY(release_id, locus_id)

release_assertion_membership

release_id
assertion_id
PRIMARY KEY(release_id, assertion_id)

历史 release 的内容必须由 membership 表确定，不能使用 latest 或 is_current 动态推断。发布 validator 还要确认：

membership 中的 assertion 与 locus 一致
inclusion_assertion_id 的 assertion_type = inclusion
viral_affinity target 属于 release 固定的 viral lineage snapshot
assembly taxon assignment 属于 release 固定的 host lineage snapshot

5.4 文献 RAG

corpus_release

id
corpus_release_key       UNIQUE
version
status                   draft | published | retired
embedding_model_key
chunking_policy_key
retrieval_policy_key
manifest_checksum

document

id
document_key             UNIQUE
title
doi
pmid
pmcid
license_key
source_uri
document_version
checksum

document_chunk

id
chunk_key                UNIQUE
document_id
section
locator
chunk_index
text
token_count
text_search_vector
checksum

document_embedding

chunk_id
embedding_model_key
embedding
PRIMARY KEY(chunk_id, embedding_model_key)

corpus_document_membership

corpus_release_id
document_id
PRIMARY KEY(corpus_release_id, document_id)

document_anchor

document_id
anchor_type              locus | lineage | method | assembly | keyword
anchor_key
PRIMARY KEY(document_id, anchor_type, anchor_key)

document_anchor 只用于约束和提升检索相关性，不作为科学真值。

6. 第一版需要准备的数据包

推荐目录：

data/pilot/
├── release.yaml
├── lineage_snapshots.csv
├── lineage_terms.csv
├── lineage_aliases.csv
├── assemblies.csv
├── assembly_taxon_assignments.csv
├── release_assembly_membership.csv
├── sequences.csv
├── loci.csv
├── detection_calls.csv
├── assertions.csv
├── evidence_items.csv
├── assertion_evidence.csv
├── release_locus_membership.csv
├── release_assertion_membership.csv
├── corpus_release.yaml
├── documents.csv
├── corpus_document_membership.csv
├── document_anchors.csv
└── documents/
    ├── paper_001.md
    ├── paper_002.md
    └── method_notes.md

试点范围建议：

50–200 assemblies
100–1,000 loci
2–5 assembly source clades
3–10 viral lineages
10–30 documents
30–50 gold questions

第一版优先使用已公开、已发表或获得明确许可的数据。不要直接导入完整未发表数据库。

7. 文献处理与检索细节

7.1 Chunking

第一版默认：

section-aware chunks
target size: 400–700 tokens
overlap: 50–100 tokens

每个 chunk 必须保留：

document_key
section
locator
chunk_index
text checksum

表格、figure legend、Methods 和 Supplementary text 应单独保留，不要全部拼成一个长文本。

7.2 Embedding

实现一个统一接口：

class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    @property
    def model_key(self) -> str: ...

要求：

模型名称和维度由配置决定；

embedding model key 写入数据库；

测试使用 deterministic fake embeddings；

生产可接本地或远程 embedding provider；

更换模型必须重新建立 embedding index。

7.3 Hybrid document retrieval

对同一个问题执行：

A. PostgreSQL full-text search
B. pgvector nearest-neighbor search
C. optional metadata/anchor filtering
D. Reciprocal Rank Fusion
E. return top_k chunks

RRF 可采用简单实现：

score(document) = Σ 1 / (k + rank_i)

k 是固定配置，并记录在 retrieval policy 中。

第一版不实现 reranker；只有 benchmark 证明必要时再增加。

7.4 Anchor-first retrieval

Hybrid route 必须先从 StructuredResult 中提取 anchors：

locus keys
lineage keys
method keys
document IDs / DOI
assembly accession.version

然后文献检索优先搜索：

与 anchor 直接链接的 documents；

同时满足关键词/向量相关性的 chunks；

若 anchor documents 不足，再扩展到同一 corpus。

这样可以降低“检索到相似但与当前记录无关的论文”的风险。

8. QueryPlan

第一版只使用一个小型、可理解的 schema。

{
  "plan_version": "0.1",
  "route": "hybrid",
  "release_key": "release:eve-pilot-v0.1",
  "intent": "list_loci",
  "question": "Which bivalve assemblies contain Orthopolintovirales-related loci, and what evidence supports them?",
  "assembly_accession": null,
  "locus_key": null,
  "source_lineage_key": "lineage:host:bivalvia",
  "viral_lineage_key": "lineage:virus:orthopolintovirales",
  "include_descendants": true,
  "hcvr_only": false,
  "metric_key": null,
  "limit": 50,
  "literature_top_k": 8
}

支持的 intent：

assembly_detail
locus_detail
list_loci
list_assemblies
list_source_taxa
aggregate
explain_method
explain_evidence

支持的 metric_key：

distinct_included_locus_count
distinct_assembly_count
distinct_source_taxon_count
detection_call_count

8.1 Resolver 优先顺序

1. exact assembly accession.version
2. exact locus key
3. exact stable lineage key
4. exact canonical name
5. exact normalized alias
6. suggestion only

模糊匹配只能返回 suggestions，不能自动执行查询。

8.2 Fail-closed

以下情况不得执行结构化事实查询：

user mentioned an entity but no entity was resolved
multiple entities remain ambiguous
release is missing or unpublished
metric is unsupported
query requests prevalence or biological absence
required filter was dropped

返回：

{
  "status": "needs_clarification",
  "unresolved_mentions": ["..."],
  "fact_retrieval_executed": false
}

合法的全库查询必须显式使用：

scope = all_records_in_release

不能用“没有解析到实体”代表全库。

9. StructuredResult、RetrievedChunks 与 ContextPack

9.1 StructuredResult

{
  "status": "ok",
  "release_key": "release:eve-pilot-v0.1",
  "query_plan_hash": "sha256:...",
  "summary": {
    "locus_count": 3,
    "assembly_count": 2,
    "source_taxon_count": 2,
    "detection_call_count": 4
  },
  "records": [],
  "assertions": [],
  "evidence": [],
  "anchors": [
    "lineage:host:bivalvia",
    "lineage:virus:orthopolintovirales",
    "method:marker-phylogeny-v1"
  ],
  "limitations": [
    "The result describes this dataset release and does not establish modern infection."
  ]
}

summary 必须在应用 pagination 之前计算。

响应同时返回：

total_rows
returned_rows
limit
cursor

9.2 RetrievedChunks

{
  "corpus_release_key": "corpus:eve-pilot-v0.1",
  "retrieval_policy_key": "retrieval:fts-vector-rrf-v1",
  "chunks": [
    {
      "citation_id": "D1",
      "chunk_key": "chunk:paper001:methods:003",
      "document_key": "doc:paper001",
      "title": "...",
      "doi": "...",
      "section": "Methods",
      "locator": "Methods, paragraph 3",
      "text": "...",
      "retrieval_score": 0.041
    }
  ]
}

9.3 ContextPack

{
  "question": "...",
  "validated_plan": {},
  "structured_result": {},
  "retrieved_chunks": {},
  "answer_rules": {
    "language": "English",
    "do_not_change_structured_facts": true,
    "cite_document_claims": true,
    "state_limitations": true
  }
}

ContextPack 是唯一允许传给 LLM 的事实上下文。

10. LLM Answer Composer

10.1 输入限制

LLM 只能接收：

user question
validated QueryPlan
StructuredResult
RetrievedChunks
fixed answer instructions

不能让模型访问数据库账号、SQL executor 或任意工具。

10.2 输出 schema

{
  "answer_text": "...",
  "structured_claims_used": [
    {
      "claim": "...",
      "source_path": "structured_result.summary.locus_count"
    }
  ],
  "literature_claims": [
    {
      "claim": "...",
      "citation_ids": ["D1", "D2"]
    }
  ],
  "limitations": ["..."]
}

10.3 固定生成规则

Prompt 必须包含：

- Write in English.
- Use structured counts, IDs, coordinates, statuses and release keys exactly as provided.
- Do not infer infection, prevalence, absence, co-divergence or integration events.
- Every claim derived from documents must cite one or more provided citation IDs.
- Do not cite a document that does not support the sentence.
- When evidence is insufficient, say so explicitly.
- Do not use external knowledge.

10.4 Citation validation

第一版只要求机械且可测试的检查：

所有 citation IDs 必须存在于 RetrievedChunks。

每个 literature claim 至少有一个 citation。

citation 对应的 document、locator 和 checksum 必须存在。

答案中的结构化数字必须等于 StructuredResult。

答案不得出现未提供的 assembly、locus、lineage 或 DOI。

语义上“文献是否真正支持 claim”仍需要 gold benchmark 和人工抽检，不能宣称已被完全自动证明。

11. API 与 CLI

11.1 API

GET  /health
GET  /version
GET  /v1/releases
GET  /v1/assemblies/{accession}
GET  /v1/loci/{locus_key}

POST /v1/query
POST /v1/query/plan
POST /v1/retrieval/documents

POST /v1/query 返回 discriminated union：

StructuredAnswer
LiteratureAnswer
HybridAnswer
ErrorAnswer

11.2 CLI

eve-rag db upgrade
eve-rag import-pilot data/pilot
eve-rag index-documents --release release:eve-pilot-v0.1
eve-rag validate-release release:eve-pilot-v0.1
eve-rag publish-release release:eve-pilot-v0.1
eve-rag query "..."
eve-rag benchmark
eve-rag serve

所有 destructive 命令必须：

默认拒绝非 test database；

需要显式 --confirm-destructive；

检查数据库名称包含 test 或 benchmark；

不允许 benchmark 清空任意用户提供的数据库。

12. 推荐项目结构

eve-relation-rag/
├── src/eve_relation_rag/
│   ├── api/
│   ├── cli/
│   ├── config/
│   ├── db/
│   │   ├── models/
│   │   ├── migrations/
│   │   └── repositories/
│   ├── ingestion/
│   │   ├── structured/
│   │   └── documents/
│   ├── lineage/
│   ├── planning/
│   │   ├── router.py
│   │   ├── resolver.py
│   │   ├── schemas.py
│   │   └── validator.py
│   ├── retrieval/
│   │   ├── structured.py
│   │   ├── lexical.py
│   │   ├── vector.py
│   │   ├── rrf.py
│   │   └── hybrid.py
│   ├── generation/
│   │   ├── context.py
│   │   ├── composer.py
│   │   ├── prompts/
│   │   └── validators.py
│   ├── providers/
│   │   ├── embeddings.py
│   │   └── llm.py
│   └── rendering/
├── app/
│   └── streamlit_app.py
├── data/
│   ├── pilot/
│   └── synthetic/
├── benchmark/
│   ├── questions.jsonl
│   ├── gold_results.jsonl
│   └── README.md
├── docs/
│   ├── architecture.md
│   ├── data_model.md
│   ├── retrieval.md
│   ├── evaluation.md
│   ├── limitations.md
│   └── development_status.md
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── retrieval/
│   ├── generation/
│   └── benchmark/
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

13. 技术栈

第一版固定：

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

Provider 层必须抽象：

EmbeddingProvider
LLMProvider

测试不得调用真实付费 API。使用：

FakeEmbeddingProvider
FakeLLMProvider

实际 Demo 可以通过环境变量选择本地或远程 provider。

14. Benchmark

第一版最少准备：

15 structured questions
10 literature questions
10 hybrid questions
5 fail-closed / unsupported questions

14.1 Structured metrics

必须达到：

entity resolution accuracy = 100% on gold set
QueryPlan exact/slot match = 100%
result-set exact equality = 100%
numeric exact match = 100%
unknown entity fail-closed = 100%
pagination does not alter totals = 100%
release provenance match = 100%

14.2 Literature retrieval metrics

初始门槛：

Recall@5 ≥ 0.80
Recall@10 ≥ 0.90
citation ID validity = 100%
locator existence = 100%

这些门槛只代表试点 corpus，不代表完整病毒学领域性能。

14.3 Hybrid generation metrics

必须达到：

structured numbers and IDs unchanged = 100%
all document-derived claims have citations = 100%
no invented record identifiers = 100%
unsupported-request refusal = 100%

另外对 10 个 Hybrid answers 进行人工审阅：

supported
partially supported
unsupported

任何 unsupported factual claim 都阻止 release。

15. Agent 工作协议

Agent 每次只实现一个 milestone。

必须遵守：

先完整阅读本指南和现有仓库。

不自行扩大 scope。

先写或更新测试，再修改实现。

不引入本指南未批准的框架。

不让 LLM 直接生成 SQL。

不把 unresolved entity 转成全库查询。

不自动 seed demo data 到非 test 环境。

所有 database schema 变化必须有 Alembic migration。

每次工作结束必须运行相关测试和静态检查。

在 docs/development_status.md 记录：

完成内容；

执行命令；

测试结果；

未完成内容；

已知限制；

下一 milestone。

Agent 不应承诺后台工作，也不应在当前 milestone 未通过验收时进入下一阶段。

16. Milestones

Milestone 0 — Repository scaffold

实现：

Python package
uv / pyproject
FastAPI health endpoint
configuration
Docker Compose with PostgreSQL + pgvector
pytest / Ruff / mypy
GitHub Actions
development_status.md

退出条件：

docker compose up -d db
uv run alembic upgrade head
uv run pytest
uv run ruff check .

全部通过。

Milestone 1 — Truth layer and pilot importer

实现：

database models
migrations
pilot CSV/YAML importer
lineage closure builder
release membership
database constraints
release validator

退出条件：

可以从空 PostgreSQL 导入 pilot data；

所有 FK 和 checksum 检查通过；

同一导入重复执行不会制造重复对象；

published release version 来自数据库；

无自动 demo seed。

Milestone 2 — Structured retrieval

实现：

QueryPlan
English resolver
validator
SQLAlchemy compiler
repositories
StructuredResult
structured API and CLI

退出条件：

structured gold tests 100%；

unknown entity 不执行事实查询；

exact result set；

pagination 与 summary 分离；

no arbitrary SQL。

Milestone 3 — Literature ingestion and retrieval

实现：

Markdown/text/JATS importer
section-aware chunker
full-text index
embedding provider
pgvector index
RRF
anchor filtering
RetrievedChunks

退出条件：

documents 可重复导入；

embedding model key 被记录；

Recall@5/10 达到门槛；

citation locator 全部有效；

不解析任意 PDF。

Milestone 4 — Hybrid RAG and generation

实现：

router
hybrid orchestration
ContextPack
LLM provider
answer composer
citation/fact validators
HybridAnswer

退出条件：

structured facts 100% 不变；

文献 claims 全部带合法 citation；

不足证据时明确说明；

真实 API 调用可选，测试全部使用 fake providers。

Milestone 5 — Demo and release

实现：

Streamlit demo
README architecture diagram
example questions
benchmark report
Docker quick start
LICENSE / DATA_LICENSE / CITATION.cff
v0.1.0-alpha release checklist

退出条件：

git clone
cp .env.example .env
docker compose up --build

可以启动数据库、API 和 Demo，并完成至少一个 structured、一个 literature 和一个 hybrid 示例。

17. 第一个 Agent 指令

Read EVE_RELATION_RAG_V0_1_AGENT_BUILD_GUIDE.md completely.

Implement Milestone 0 only.

Do not implement database domain tables, document ingestion,
embeddings, LLM integration, retrieval, or later milestones yet.

Use Python 3.12, uv, FastAPI, PostgreSQL with pgvector,
SQLAlchemy, Alembic, pytest, Ruff, mypy, Docker Compose,
and GitHub Actions as specified.

Run all available validation commands.
Report the exact commands and results.
Update docs/development_status.md.
Do not proceed to Milestone 1 until every Milestone 0 exit
condition passes.

后续 milestone 也使用同样格式：

Implement Milestone N only.
Read the guide and development_status.md first.
Preserve all previous tests.
Add milestone-specific tests before or with implementation.
Do not implement Milestone N+1 features.
Run and report exact validation commands.
Update development_status.md.

18. Definition of Done

第一版只有在以下条件全部满足时才算完成：

产品输入和输出只支持英文；

PostgreSQL 是唯一结构化真值源；

结构化 facts、文献 chunks 和生成答案三层分开；

structured、literature、hybrid 三条路线均可运行；

每条 structured result 绑定 published release；

每个 locus 有 assembly.version、sequence.version 和合法坐标；

calls、assertions、evidence 和 release membership 分开；

viral lineage 支持 ICTV 和 study-defined schemes；

未解析和歧义查询 fail closed；

LLM 不生成 SQL，不修改 StructuredResult；

文献检索使用固定 corpus、FTS、pgvector 和 RRF；

每个文献 claim 有可定位 citation；

所有数字和 ID 与 StructuredResult 完全一致；

benchmark 达到本指南门槛；

Docker 可以从空环境启动；

pilot release 可从原始文件重建；

README 明确说明科学限制；

GitHub 包含 tests、benchmark、license、citation 和 changelog。

最终产品可以诚实地说：

In release R, the database contains these assembly-local EVE loci and
these versioned viral-lineage affinity assertions. The literature
retrieved for those records describes the supporting methods and
limitations at the cited locations.

不能说：

The language model proved that this virus infected this host.
