# Embedding 与 Reranker 对照实验设计（Phase 0）

- 状态：**Phase 0 设计快照；Phase 1 实现见
  [embedding_ablation_phase1.md](embedding_ablation_phase1.md)**
- 审计快照：`70120595a0c8eb13b28d895b7162ab35c72dbed3`（`codex/repository-cleanup`）
- 审计日期：2026-09-01
- 适用仓库：`Hongda-Zhao/EndoViHo-RAG`

## 1. 结论先行

本实验应作为独立的、只读语料输入的 retrieval ablation 实现，不应复用 production 的 embedding 写入路径，也不应改变 production 的 provider、policy key、数据库表或默认配置。

推荐的 Phase 1 边界是：

1. 从一个 checksum-pinned、已 published 的 corpus release 读取同一批文档、chunk、FTS 数据和 anchors。
2. 在 production PostgreSQL 中只执行 `SELECT`；FTS 分支继续使用现有 PostgreSQL 表达式和候选深度。
3. 在 `.artifacts/embedding_ablation/<experiment_id>/` 中建立 checksum-bound sidecar 向量矩阵和临时索引，不写入 `document_embedding`。
4. 所有 system 共用同一 benchmark、chunk 顺序、anchor 解析、RRF 参数、最终 `top_k` 和硬件记录。
5. 只把不含模型权重、论文全文或 chunk 正文的机器结果写到 `benchmark/embedding_ablation/`；报告由这些结果确定性生成。

这个边界既能复现 baseline，又绕开当前数据库“一份 corpus release 只能绑定一个 embedding model”和 `VECTOR(384)` 的硬约束。相关代码证据见 [仓库映射](embedding_ablation_repo_mapping.md)。

## 2. Phase 0 范围与非目标

Phase 0 只完成代码审计、接口设计、隔离方案、schema proposal 和下一阶段准入条件。它不执行以下行为：

- 不修改 production implementation、production defaults 或 Alembic schema。
- 不增加模型/runtime 依赖。
- 不下载、加载或运行 MedCPT、Qwen3 或其他真实模型。
- 不运行新的真实 benchmark。
- 不重切文档，不生成新的真实 gold label。
- 不写入或覆盖 published corpus、production embeddings、validation receipt。

本文件不是实验结果报告。真正的结果报告 `docs/embedding_reranker_ablation.md` 只能在后续阶段由可信的机器结果生成。

## 3. 必须冻结的实验不变量

一次 experiment manifest 必须把以下字段作为共同输入冻结；任意 system 不得覆盖它们：

| 不变量 | 必须记录或验证的身份 |
|---|---|
| Corpus release | `corpus_release_key`、`manifest_sha256`、`policy_graph_sha256`、published/validated receipt identity |
| Document bytes | 按 manifest row 排序的 `document_key`、`source_artifact_sha256`、`byte_size` |
| Chunks | 按 `chunk_key` 排序的 key、document key、block type、text SHA-256、locator SHA-256；正文只在内存中传给本地模型 |
| Gold questions | annotation manifest SHA-256；仅 `review_status=approved` 的问题进入正式结果 |
| FTS branch | policy key、PostgreSQL/词典版本、权重、`ts_rank_cd` 参数、候选深度与 tie-break |
| Anchors | anchor policy key、anchor manifest SHA-256、每题 anchor keys、`anchored_then_corpus_fill` 语义 |
| RRF | 三个分支、`k=60`、量化精度、tie-break 和 branch candidate depth |
| Evaluation | 所有 system 使用相同最终 `top_k=10`；rerank depth 20/50 是 system 参数，不是最终 `top_k` |
| Execution | 同一个 `hardware_record_sha256`、runtime/lock hash、离线环境、warm-up/iteration/scheduling policy |

文档/chunk 身份已有稳定 hash 载体：文档模型保存原始字节 hash，见 [db/models.py](../src/eve_relation_rag/db/models.py#L1795)；chunk key preimage 同时绑定 corpus、parser、chunking policy 和规范化正文，见 [literature/contracts.py](../src/eve_relation_rag/literature/contracts.py#L389)。现有切分目标为 384 tokens、64 overlap、448 max，见 [literature/chunking.py](../src/eve_relation_rag/literature/chunking.py#L21)。任何比较系统都只能消费这些既有 chunks。

## 4. 已确认的 BGE baseline representation contract

| 字段 | 当前值 | 证据 |
|---|---|---|
| Model ID | `BAAI/bge-small-en-v1.5` | [contracts.py](../src/eve_relation_rag/literature/contracts.py#L27) |
| Exact revision | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` | [contracts.py](../src/eve_relation_rag/literature/contracts.py#L29) |
| Dimension | 384 | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L37) |
| Pooling | CLS | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L37) |
| Normalization | L2 unit norm | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L37) |
| Similarity | Cosine | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L37) |
| Query format | `Represent this sentence for searching relevant passages: {question}` | [contracts.py](../src/eve_relation_rag/literature/contracts.py#L31) |
| Passage format | 原始 chunk text，无 passage prefix | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L37) |
| Max sequence length | 512 tokens；过长 query 拒绝，不静默截断 | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L127) |
| Dtype | canonical float32 | [embeddings.py](../src/eve_relation_rag/literature/embeddings.py#L58) |
| Model loading | 本地目录、完整 manifest 校验、`local_files_only=True`、`trust_remote_code=False` | [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L57) |
| License recorded by current policy | MIT | [ingestion.py](../src/eve_relation_rag/literature/ingestion.py#L573) |

向量接收路径还会检查维度、float32 可表示性、有限值和单位范数，见 [embeddings.py](../src/eve_relation_rag/literature/embeddings.py#L83)。因此 baseline 不是“模型名称相同”即可复现，而是上述整个 representation contract 与 artifact manifest 都必须相同。

## 5. 实验架构

```text
published corpus (PostgreSQL, SELECT only)
        |
        +-- exact FTS ranks --------------------------+
        +-- ordered immutable chunk snapshot          |
        +-- title/abstract subset                      |
        +-- anchors                                    |
                                                        v
verified local model -> sidecar vectors -> dense ranks -> exact RRF
                                                        |
                                                        +-- top 10 (no reranker)
                                                        |
                                                        +-- top 20/50 candidates
                                                                |
                                                  verified local reranker
                                                                |
                                                           final top 10
                                                                |
                                     metrics + latency + resources + failures
                                                                |
                                      canonical JSON/CSV -> generated Markdown
```

### 5.1 推荐的代码边界

Phase 1 新代码应放在独立 package，例如：

```text
src/eve_relation_rag/experiments/embedding_ablation/
├── contracts.py
├── artifact_verification.py
├── corpus_snapshot.py
├── providers.py
├── retrieval.py
├── reranking.py
├── metrics.py
├── telemetry.py
├── reporting.py
└── cli.py
```

该 package 不应被 `bootstrap.py`、API 或 Streamlit production composition root 导入。实验 CLI 也不得调用现有会重建 candidate corpus 的 `literature benchmark` 命令；当前命令会验证/重建 candidate corpus 并固定加载 BGE，见 [cli.py](../src/eve_relation_rag/cli.py#L727)。

### 5.2 Production 数据库只读规则

实验连接必须同时满足：

- 使用单独的数据库只读账号；该账号没有任何 DDL/DML 权限。
- SQLAlchemy connection 使用 `postgresql_readonly=True`。
- transaction 开始后验证 `SHOW transaction_read_only` 为 `on`。
- 运行前后计算相同的 corpus fingerprint：release identity、document/chunk/anchor/embedding 行数以及按稳定顺序聚合的 hashes。
- fingerprint 不同则整个 experiment 失败，所有 system 标记为不可信。
- 禁止调用 ingestion、embedding build、validation receipt、publish 或 Alembic 路径。

现有 retrieval repository 已在只读 transaction 中执行查询，可作为行为参考，见 [repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L68)。但它只接受与 release 绑定的 production BGE vector，并在返回前截断到 production `top_k`，因此不能直接承载多模型或 rerank top-50 实验。

### 5.3 Sidecar 索引

推荐 sidecar 方案不需要数据库 schema 变化：

- 每个 embedding artifact/system 生成一个 float32 matrix，行顺序严格等于 snapshot 的 ordered `chunk_key`。
- title/abstract dense branch 仅在同一矩阵中筛选 `block_type in {title, abstract}`；不得重新切分或建立不同正文。
- 1464 chunks 的首轮可用精确矩阵乘法计算 cosine 排名，避免为小语料引入 ANN 近似或额外依赖。
- sidecar header 必须记录 dimension、normalization、matrix SHA-256、ordered chunk-key SHA-256 和 model manifest SHA-256。
- sidecar 只保留向量与 keys/hashes，不持久化 chunk text。
- sidecar 放在已忽略的 `.artifacts/` 中；不得提交模型权重、索引中的正文或论文全文。

若后续改用 ANN，index algorithm、版本、参数、seed 与 index bytes SHA-256 都要进入 system manifest，并先证明与 exact ranking 的差异。

## 6. Provider 与表示契约

### 6.1 保持现有 `EmbeddingProvider`

现有协议已经提供 `model_key`、`dimension`、artifact manifest SHA-256、document/query embedding，见 [providers.py](../src/eve_relation_rag/literature/providers.py#L13)。Phase 1 保持它不变，并在实验层用 adapter 关联更丰富的 `ModelRepresentationContract`，避免扩张 production interface。

实验层 representation contract 至少包含：

```json
{
  "model_id": "...",
  "exact_revision": "...",
  "license": "...",
  "task_kind": "embedding|reranker",
  "dimension": 384,
  "pooling": "...",
  "normalization": "l2|none|...",
  "similarity": "cosine|dot_product|...",
  "query_format": "...",
  "passage_format": "...",
  "max_sequence_length": 512,
  "truncation_policy": "reject|truncate_tail|...",
  "artifact_manifest_sha256": "<64 lowercase hex>",
  "runtime_key": "...",
  "local_files_only": true,
  "trust_remote_code": false
}
```

每个 adapter 启动时必须把声明值与本地 config/tokenizer/runtime 观察值比较。错误 revision、缺失 artifact、checksum mismatch、错误 dimension 或无法在 `trust_remote_code=False` 下加载时，应在产生任何向量或结果前拒绝。

### 6.2 新增 `RerankerProvider`

Phase 1 新增的协议应保持用户指定的最小接口：

```python
from collections.abc import Sequence
from typing import Protocol


class RerankerProvider(Protocol):
    @property
    def model_key(self) -> str: ...

    @property
    def artifact_manifest_sha256(self) -> str: ...

    def score(
        self,
        query: str,
        passages: Sequence[str],
    ) -> Sequence[float]: ...
```

实验 orchestrator 在协议外统一执行以下检查和遥测：

- passage 输入 tuple 在调用前后必须相同。
- 返回长度必须等于 passage 数量，包括空输入的明确定义行为。
- 每个返回值必须可转换为有限 float；NaN/Inf 立即失败。
- score 只能按输入位置绑定 candidate，不允许 provider 删除、插入或重排候选。
- 实际 rerank 排序在 provider 外执行：`score desc`，再以 pre-rerank rank、`chunk_key` 作为稳定 tie-break。
- 记录 query/passage truncation、batch size、batch count、wall-clock latency 和错误阶段。
- anchor 模式下只能在同一 retrieval tier 内重排；`corpus_fill` 不得越过 `anchored` tier。

## 7. 本地 artifact manifest

### 7.1 Manifest 校验规则

为每个 encoder/reranker 使用独立 manifest；manifest 本身为 canonical UTF-8 JSON，并记录目录中每个文件的 relative path、byte size 和 SHA-256。校验规则与当前 BGE verifier 一致：拒绝 symlink、路径逃逸、缺失文件、额外文件、大小/checksum 不符，见 [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L166)。

Manifest 还必须有：

- schema version、model ID、exact immutable revision；不接受 branch/tag/`main`。
- license identifier 与人工确认状态。
- encoder/reranker task kind。
- dimension、pooling、normalization、similarity。
- query/passage serialization 和特殊 token 策略。
- tokenizer/model max sequence length及 truncation side。
- runtime/backend/version compatibility。
- `trust_remote_code=false` 和 local-only 声明。

启动时设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 和 `HF_DATASETS_OFFLINE=1`，并在导入模型 runtime 之前生效。所有模型构造都必须使用本地绝对路径，禁止用 model ID 做 repository resolution。

### 7.2 必需 artifacts

| 系统 | 必需的本地 manifest/artifact | 当前状态 |
|---|---|---|
| A | 已批准的 BGE-small encoder manifest 与目录 | 本机观察到现有 manifest；仓库不携带权重 |
| B | A 的 BGE artifact + `ncbi/MedCPT-Cross-Encoder` manifest/目录 | 未提供 exact revision/license/checksum |
| C | `ncbi/MedCPT-Query-Encoder`、`ncbi/MedCPT-Article-Encoder`；可选 Cross-Encoder；另有一个 bundle manifest 绑定兼容组合 | 未提供 exact revisions/licenses/checksums/dimensions |
| D | `Qwen3-Embedding-0.6B`；可选 `Qwen3-Reranker-0.6B`；可选 bundle manifest | 未提供 exact revisions/licenses/checksums；384 输出能力尚未本地核验 |

MedCPT query/article encoder 是一对不同 artifacts，不能只记录一个 model ID。它们的 passage input serialization 也必须显式固定。不得猜测 exact revision、license 或输出维度。

### 7.3 384 维准入

首轮只允许满足以下任一条件的系统进入可信比较：

1. 本地 config 和 adapter 的离线 contract test 都证明原生输出恰为 384 维；或
2. pinned model 明确定义、且本地 runtime 原生支持其官方 384-dimensional output 参数，并通过固定 reference fixture 验证。

禁止为了通过检查而进行无依据的截断、随机/学习投影或 padding。若 MedCPT/Qwen3 不能安全地产生 384 维，则停止该 system，不写 production DB，并按第 13 节的 schema proposal 另行审批。Sidecar 能技术上容纳其他维度，但仍须先得到 Phase 1 范围和 schema proposal 的明确批准。

## 8. 比较系统定义

以下 `system_key` 仅为 Phase 1 建议，最终值及所有参数必须进入 manifest：

| System | 建议 system key | Candidate generation | Reranking |
|---|---|---|---|
| A | `bge_small__fts_dense_summary__rrf60` | 当前 BGE query/full-chunk/title-abstract dense + 原 FTS + 原 RRF | 无 |
| B20 | `bge_small__rrf60__medcpt_ce__d20` | 与 A 完全相同，取融合后 top 20 | MedCPT Cross-Encoder |
| B50 | `bge_small__rrf60__medcpt_ce__d50` | 与 A 完全相同，取融合后 top 50 | MedCPT Cross-Encoder |
| C | `medcpt_biencoder__fts_dense_summary__rrf60` | MedCPT query/article encoder 替换两个 dense 分支，FTS/anchors/RRF 不变 | 无 |
| C+R | `medcpt_biencoder__rrf60__medcpt_ce__d<N>` | C 的融合候选 | MedCPT Cross-Encoder |
| D | `qwen3_embedding_0_6b__fts_dense_summary__rrf60` | Qwen3 embedding 替换两个 dense 分支，FTS/anchors/RRF 不变 | 无 |
| D+R | `qwen3_embedding_0_6b__rrf60__qwen3_reranker_0_6b__d<N>` | D 的融合候选 | Qwen3 reranker |

所有 system 的 branch candidate depth 保持 100；最终评估 `top_k` 保持 10。B20/B50 是两个不同 system，不能把两种 depth 混合到同一汇总行。

## 9. Retrieval 与 reranking 语义

Baseline 当前行为必须精确复制：

- FTS：PostgreSQL English config，title=A、section/block=B、text=D，见 [ingestion.py](../src/eve_relation_rag/literature/ingestion.py#L736)；查询使用 `websearch_to_tsquery` 和 `ts_rank_cd(..., 32)`，结果按 rank desc、`chunk_key` 排序，见 [repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L196)。
- Dense：full-chunk 和只含 title/abstract chunks 的 summary branch；两者均按 cosine distance、`chunk_key` 排序，见 [repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L220)。
- Fusion：三个分支各取 first rank，`RRF k=60`，12 位小数量化，稳定 tie-break，见 [fusion.py](../src/eve_relation_rag/retrieval/literature/fusion.py#L8)。
- Anchors：先取 anchored tier，再去重填充 corpus-wide tier，见 [repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L101)。

实验实现若复制私有 SQL，必须有 parity test：对 baseline provider 的每个批准问题，实验分支 ranks、RRF score 和最终 top-10 必须与 production baseline 完全一致，才允许比较其他系统。

Reranker 输入 passage format 由其 manifest 固定，但仍以同一个现有 chunk 为核心，不得重新切分。若格式包含 title、section 或 locator，必须以稳定、无歧义的 serialization key 记录，并明确这是一项 system representation 差异。

## 10. Annotation schema 与人工审批

现有 benchmark question 只有 question、anchors 和一个扁平的 `relevant_chunk_keys`，没有 category、alternatives、excluded 或 review status，见 [benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L39)。因此 13 个现有问题可作为待迁移素材，但不能由 Codex 自动补 category、alternative 或 approval。

Phase 1 可生成空白/待审模板；每题至少保存用户指定字段：

```json
{
  "question_id": "...",
  "question": "...",
  "category": "definition",
  "required_chunk_keys": [],
  "acceptable_alternative_chunk_keys": [],
  "excluded_chunk_keys": [],
  "review_status": "pending"
}
```

允许的 categories 为：`definition`、`method`、`classification`、`evidence`、`limitation`、`taxonomy`。只有 `review_status="approved"` 且 reviewer identity/review timestamp 完整的问题进入 formal benchmark；`pending`、`rejected` 均不得进入正式 summary。

扁平的 alternatives 无法表达“某个 alternative 替代哪个 required evidence”。为保证 Recall/nDCG 精确，建议额外加入：

```json
{
  "evidence_groups": [
    {
      "group_id": "e1",
      "required_chunk_key": "chunk:sha256:...",
      "acceptable_alternative_chunk_keys": []
    }
  ],
  "reviewer_id": "...",
  "reviewed_at": "YYYY-MM-DDTHH:MM:SSZ",
  "annotation_notes": "..."
}
```

顶层 `required_chunk_keys` 和 `acceptable_alternative_chunk_keys` 是 groups 的规范化投影并做一致性校验。每个 evidence group 在 metrics 中最多计一次；alternatives 不增加 Recall 分母。`excluded_chunk_keys` 必须与所有正例集合互斥。

扩展到 30–50 题时只能由专家提供/批准真实 labels。Codex 只建立 schema、模板和验证器。

## 11. 指标的确定性定义

### 11.1 Quality

- `Recall@K`（K=1,3,5,10）：top K 满足的 evidence groups 数 / 全部 evidence groups 数。一个 group 的 required 或任一 approved alternative 首次出现即视为满足。
- `MRR@10`：第一个属于任一未排除正例 group 的结果 rank 的倒数；top 10 无正例为 0。
- `nDCG@10`：每个 evidence group 只在首次命中位置获得 binary gain 1；之后同 group 的 required/alternative gain 为 0。DCG 使用 `1/log2(rank+1)`，IDCG 由 `min(group_count, 10)` 个 gain 1 构成。
- 汇总默认是 approved questions 的 macro mean，并同时按 category 输出。每个数用 Decimal/明确舍入策略序列化，避免平台浮点格式差异。

模型选择必须以 `Recall@5`、`MRR@10` 和 quality–latency Pareto trade-off 为主；`Recall@10` 不能单独决定胜者。

### 11.2 Latency

每个 query/system 记录整数 nanoseconds：

- `embedding_latency_ns`：query serialization + tokenization + query encoder；不含一次性模型加载。
- `retrieval_latency_ns`：FTS、dense scoring、anchor tier 和 RRF；不含 query embedding 与 reranking。
- `reranking_latency_ns`：reranker serialization/tokenization/inference/score validation；无 reranker 系统记为 null，而非伪造 0 分布。
- `end_to_end_latency_ns`：从 query 开始到 final top-k 完成，包含上述阶段。
- corpus passage embedding/index build latency 单独记录，不能混入在线 query latency。

P50/P95 使用 manifest 固定的离散 nearest-rank 算法，并记录 warm-up count、measured iteration count、query/system 交错 schedule、batch size 和 timer。所有系统在同一 run 使用同一 schedule；不得只给某个模型额外预热。

### 11.3 Resources 与 truncation

- `peak_process_rss_bytes` 和可用时的 `peak_accelerator_memory_bytes` 分开记录。
- `model_size_bytes` 为 verified artifact manifest 中所有文件大小之和。
- `index_size_bytes` 为 sidecar matrix/index 实际文件大小之和。
- truncation 分别记录 query、passage embedding、reranker query 和 reranker passage 的 item/token counts。
- 任何静默 truncation 都是 contract violation；允许 truncation 的模型必须在 manifest 中声明 side、max length 和计数方法。

Hardware record 至少包括 CPU model/core、RAM、OS/kernel、machine architecture、accelerator model/count/driver/runtime、BLAS/backend、Python、uv lock SHA-256、PostgreSQL、pgvector、thread/environment settings。当前 benchmark fingerprint 只有 Python/platform/lock/PostgreSQL/pgvector，见 [benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L103)，不足以满足本实验要求。

## 12. 输出与可信度

可信运行生成：

```text
benchmark/embedding_ablation/
├── experiment_manifest.json
├── systems/
├── per_question/
├── summary.json
├── summary.csv
├── latency.csv
├── resource_usage.csv
├── failures.jsonl
├── retrieval_quality.csv
├── retrieval_by_category.csv
├── latency_comparison.csv
├── resource_comparison.csv
└── rank_shift_after_reranking.csv
```

同时由上述机器结果生成 `docs/embedding_reranker_ablation.md`。

确定性要求：

- JSON 使用 canonical serialization，object keys 与 system/question rows 均按稳定 key 排序。
- CSV 固定 UTF-8、LF、header/column order、float format 和空值表示。
- `per_question/` 与 `systems/` 文件名来自经过校验的 `system_key`，拒绝路径字符。
- `failures.jsonl` 按 system、question、stage、error code 排序，不写 query/passage 正文。
- Markdown generator 只读 `experiment_manifest.json`、summary/CSV，不重新调用模型或数据库。
- generator 必须复算输入 hashes 与 aggregate metrics；任何不一致拒绝生成。
- 报告中的时间戳、路径和主机名等非确定内容必须来自 manifest 的规范化字段，不能读取生成时环境。

每个运行有 `trust_status`：

- `trusted`：全部 providers 为 checksum-verified 的允许 concrete adapters，全部问题 approved，corpus 前后 fingerprint 相同，且所有 system 完成所需检查。
- `test_only`：允许 deterministic fake provider，仅供 unit/integration tests；不得生成 trusted report 或与真实模型混入正式 summary。
- `failed`：任何身份、checksum、维度、finite、normalization、数据库只读或结果完整性检查失败。

现有 fake provider 明确只用于 tests，见 [providers.py](../src/eve_relation_rag/literature/providers.py#L51)。实验 trust gate 必须检查已验证 concrete adapter/provenance，而不是只依赖 Protocol 的结构相似性。

## 13. Pgvector/schema proposal（仅提案，不实施）

### 13.1 当前限制

当前 schema 同时存在三项硬绑定：

1. `embedding_model.dimension = 384`、pooling=CLS、L2/cosine 等 check constraints，见 [db/models.py](../src/eve_relation_rag/db/models.py#L1674)。
2. `corpus_release` 只有一个 `embedding_model_id`，见 [db/models.py](../src/eve_relation_rag/db/models.py#L1716)。
3. `document_embedding.embedding` 是 `VECTOR(384)`，且 FK 要求 embedding model 必须等于 release 绑定的 model，见 [db/models.py](../src/eve_relation_rag/db/models.py#L1931)。

所以 production 表不仅不能安全存非 384 向量，也不能给同一 release 同时存多个 384 维模型。绝不能通过改 column dimension、删除 FK/check 或覆盖现有 rows 来完成 ablation。

### 13.2 推荐方案 A：无 schema 变化

Phase 1 优先采用第 5.3 节 sidecar。它支持独立、可删除、checksum-bound 的实验索引，production DB 仍是只读；即使后续批准非 384 模型，也不需要 pgvector 变更。

### 13.3 备选方案 B：独立实验数据库/schema

若必须用 pgvector，应新建独立实验数据库或严格隔离的实验 schema，而不是修改 production tables。建议逻辑实体为：

- `ablation_run`：绑定 experiment/corpus/gold/hardware hashes。
- `ablation_model_artifact`：绑定完整 representation contract 和 manifest hash。
- `ablation_system`：绑定 model components、FTS/RRF/anchor/top-k/rerank depth。
- `ablation_index`：绑定 corpus snapshot、model、dimension、index config/hash。
- 维度专用向量表，如 `ablation_embedding_384`、`ablation_embedding_768`、`ablation_embedding_1024`，每表使用匹配的 `VECTOR(N)` 和 HNSW/ivfflat index。

不建议在一个无 typmod 的 `vector` column 中混合维度，因为索引和 dimension validation 会变得含糊。任何方案 B migration 必须先提交：表/索引 DDL、权限模型、容量估算、rollback、production zero-write proof 和 exact-vs-ANN parity plan，得到单独批准后才能实施。

## 14. Phase 1 测试矩阵

| 要求 | 最小测试 |
|---|---|
| wrong model/revision rejected | manifest identity 与 adapter/config 任一不符，在模型构造前失败 |
| missing artifact rejected | manifest 中列出的文件缺失或本地目录不存在 |
| checksum mismatch rejected | manifest 文件 hash、artifact hash 或 bundle hash 任一不符 |
| wrong dimension rejected | 声明 384、实际长度非 384；以及 system/index dimension 不一致 |
| NaN/Inf rejected | embedding 与 reranker 分数分别覆盖 NaN、+Inf、-Inf |
| normalization contract checked | L2 contract 的单位范数正/反例；未声明 normalization 不得当作 cosine-ready |
| reranker output length checked | 少一项、多一项、空输入行为 |
| candidate order preserved | sentinel candidates 证明 scores 按原位置绑定、输入 tuple 未变、无删除 |
| fake provider cannot create trusted report | fake 可生成 `test_only` fixture，但 trust/report gate 拒绝 `trusted` |
| experiment cannot modify published corpus | read-only role 拒绝 DML；运行前后 corpus fingerprint 完全相同 |
| experiment cannot modify production defaults | 对 settings/bootstrap/contracts/migrations 的 pre/post file SHA map 相同 |
| metric calculations are exact | 手算 fixtures 覆盖 multi-gold、alternatives、duplicate hits、excluded、zero hit、rank 10、ties、category macro、p50/p95 |

还需要：baseline parity、anchor tier 不可跨越、RRF tie-break、top20/top50 pool、truncation accounting、hardware drift、CSV/Markdown golden files、失败恢复/原子输出、离线 socket guard 和“不提交正文/权重”扫描。

## 15. Phase 1 准入条件与建议顺序

开始 Phase 1 前需要用户批准或提供：

1. 每个 MedCPT/Qwen3 component 的本地绝对目录、artifact manifest 路径、approved manifest SHA-256、exact revision、license 与 representation contract。
2. 选择只运行安全 384 systems，或明确批准非 384 sidecar/system；不批准任何 production schema 变化。
3. 对 13 个现有问题完成 category、新 annotation schema 和 `review_status=approved` 的人工复核；否则只能产生 preliminary/test-only 结果。
4. 固定正式 corpus release、corpus/anchor/gold manifest SHA-256 和 production 只读数据库账号。
5. 固定 rerank depths（建议把 20 与 50 当作两个 system）及 latency warm-up/iteration policy。

建议 Phase 1 依次实施：contracts/manifest verifier → read-only snapshot/fingerprint → baseline sidecar parity → metrics/report golden tests → reranker protocol/tests → verified model adapters → 仅在所有 gates 通过后运行真实 benchmark。

Phase 0 到此停止；未获得 Phase 1 批准前不实现或运行上述组件。
