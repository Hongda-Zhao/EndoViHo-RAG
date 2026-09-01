# Embedding/Reranker Ablation 仓库映射（Phase 0）

- 状态：**Phase 0 audit 快照；Phase 1 实现见
  [embedding_ablation_phase1.md](embedding_ablation_phase1.md)**
- 仓库：`Hongda-Zhao/EndoViHo-RAG`
- Git snapshot：`70120595a0c8eb13b28d895b7162ab35c72dbed3`
- 分支：`codex/repository-cleanup`
- 审计日期：2026-09-01

## 1. 七项审计问题的直接答案

1. **现有 embedding/retrieval/database/benchmark 代码已定位。** 核心路径是 `literature/contracts.py`、`providers.py`、`local_bge.py`、`embeddings.py`、`retrieval/literature/{repository,fusion}.py`、`db/models.py`、migration 0006/0009、`literature/benchmarking.py` 和 `cli.py`。
2. **BGE representation contract 已确认。** 它是 exact revision 的 BGE-small、384 维、CLS、L2、cosine、指定 query prefix、无 passage prefix、max 512、float32、本地完整 manifest 校验、禁止 remote code。
3. **Pgvector dimension assumptions 已确认。** ORM、migration、model identity checks、corpus FK 和 runtime gate 均硬编码当前 BGE/384；同一 corpus release 甚至不能并存另一个 384 维 model。
4. **隔离路径已确认。** production PostgreSQL 只读 + pre/post corpus fingerprint；新模型向量放 `.artifacts/` sidecar；结果目录不包含正文或权重；不调用 production rebuild/publish/receipt 路径。
5. **当前 benchmark 不支持多个 `system_key`。** 全仓库检索没有该字段；definition/report/result 都用当前 BGE/retrieval policy 的 `Literal`，并且只计算 Recall@5/10。
6. **MedCPT、Qwen3 和 reranker 接入缺口已列出。** 需要独立实验 contracts、artifact verifier、provider adapters、reranker protocol、sidecar retrieval、multi-system runner、metrics/telemetry/reporting，以及每个 component 的 pinned local artifacts。
7. **Schema 结论已形成。** Phase 1 推荐不改 schema；若要将其他 dimension 写入 pgvector，必须采用独立实验数据库/schema 和 dimension-specific vector tables，并另行审批。

详细实现设计见 [embedding_ablation_design.md](embedding_ablation_design.md)。

## 2. 当前 production 数据流

```text
Settings(local_bge only)
    -> bootstrap creates LocalBgeProvider
    -> PublishedCorpusGate authorizes one exact release/model/policy graph
    -> query prefix + BGE encode + float32/unit-norm validation
    -> LiteratureRepository
         |- PostgreSQL FTS top 100
         |- full-chunk pgvector cosine top 100
         |- title/abstract pgvector cosine top 100
         `- exact RRF(k=60)
    -> anchored tier first, corpus_fill second
    -> hard-coded RetrievedChunks identity

Candidate benchmark CLI
    -> loads exact corpus/anchor/benchmark manifests
    -> loads the same LocalBgeProvider
    -> rebuild validation of candidate corpus
    -> fixed top-10 benchmark
    -> Recall@5, Recall@10, citation and locator gates
```

这条路径设计用于 fail-closed production baseline，不是一个多模型实验框架。把 ablation 直接插入这里会同时碰到 provider identity gate、corpus model FK、`VECTOR(384)`、hard-coded result Literals 和 validation receipt 语义。

## 3. 代码与需求映射

| 主题 | 当前实现与证据 | 对 ablation 的含义 |
|---|---|---|
| Baseline keys | parser/chunking/model/FTS/retrieval/anchor keys 都是 module constants，[contracts.py](../src/eve_relation_rag/literature/contracts.py#L22) | A 系统必须复用 exact values；其他 system 不能伪装成 production key |
| Request/output identity | request `top_k` 为 1..20；output 将 retrieval/model 写成 `Literal`，[contracts.py](../src/eve_relation_rag/literature/contracts.py#L205)、[contracts.py](../src/eve_relation_rag/literature/contracts.py#L461) | 现有 API 无法表达多 system 或 top-50 candidate pool；实验需要独立 contracts |
| Chunk identity | chunk key 绑定 corpus、parser、chunking policy、locator、normalized text/hash，[contracts.py](../src/eve_relation_rag/literature/contracts.py#L389) | 所有系统可安全共享同一 immutable chunk key；禁止 rechunk |
| Embedding protocol | `model_key`、dimension、artifact manifest SHA、document/query methods，[providers.py](../src/eve_relation_rag/literature/providers.py#L13) | Protocol 可保留；丰富 metadata/telemetry 放实验 adapter/contract |
| Fake provider | fake 与 baseline key/384 相同，但标明 tests only，[providers.py](../src/eve_relation_rag/literature/providers.py#L51) | trust gate 不能只看 structural Protocol 或 model key |
| BGE manifest | schema 固定 dimension/pooling/norm/license/max length/prefix/revision，[local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L21) | A 的 representation contract 可直接从此构造；其他模型需各自 manifest |
| Offline load | 本地绝对目录，完整 manifest 校验，`local_files_only=True`、`trust_remote_code=False`，[local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L57) | 新 adapters 必须达到相同或更严格的离线/provenance 标准 |
| Artifact verifier | 拒绝 symlink/path escape/missing/checksum/size/extra files，[local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L166) | verifier 可抽象到实验 package，但不能放宽 |
| Vector validation | dimension、finite float32、unit norm；hash metadata 将 pooling 固定为 CLS，[embeddings.py](../src/eve_relation_rag/literature/embeddings.py#L58) | 现函数只适合 baseline；实验 validator/checksum 必须读取每模型 contract，不能把 MedCPT/Qwen 假记为 CLS |
| Embedding build | provider 必须等于 baseline key/dim/manifest；只允许 candidate/validated release，[embeddings.py](../src/eve_relation_rag/literature/embeddings.py#L112) | 不得复用此写入路径生成 ablation embeddings |
| Chunking | target 384、overlap 64、max 448；tokenizer offsets 进入固定 chunks，[chunking.py](../src/eve_relation_rag/literature/chunking.py#L21) | 新模型只能面对这些 chunks，并记录自身 truncation |
| FTS materialization | title A、section/block B、body D，[ingestion.py](../src/eve_relation_rag/literature/ingestion.py#L736) | 所有 system 直接复用同一存量 `fts_document` |
| FTS query | English `websearch_to_tsquery`、`ts_rank_cd(...,32)`、rank/key tie-break、depth 100，[repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L196) | 实验 FTS SQL 必须逐项 parity |
| Dense branches | full corpus 与 title/abstract subset 都使用 cosine、key tie-break、depth 100，[repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L220) | Sidecar runner 替换 vector source，但保持 subsets/depth/ties |
| RRF | 三分支、k=60、Decimal 12 位、稳定排序，[fusion.py](../src/eve_relation_rag/retrieval/literature/fusion.py#L8) | 可复用 pure function；system manifest 仍须记录 settings |
| Anchors | anchored results 先加入，corpus-wide 去重补齐，[repository.py](../src/eve_relation_rag/retrieval/literature/repository.py#L101) | reranker 不能让 corpus-fill 穿越 anchored tier |
| Production service gate | provider 必须等于 capability 和 global baseline identity，[application/literature.py](../src/eve_relation_rag/application/literature.py#L26) | 新模型不能注入 production service |
| Policy graph gate | BGE repository/revision/dim/max/pooling/norm/prefix/similarity/license/metadata 都逐字段固定，[gate.py](../src/eve_relation_rag/literature/gate.py#L90) | 保留 production fail-closed；实验另建身份图 |
| Settings/bootstrap | settings 只允许 `local_bge`；bootstrap 只构造 `LocalBgeProvider`，[settings.py](../src/eve_relation_rag/config/settings.py#L29)、[bootstrap.py](../src/eve_relation_rag/bootstrap.py#L78) | Phase 1 不改 production config/defaults/composition root |
| Benchmark question | 只有 question key/text、anchors、`relevant_chunk_keys`，[benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L39) | 缺 category/alternatives/excluded/review status；需要新 annotation schema |
| Benchmark definition | exact corpus + baseline retrieval/model Literals；无 system key，[benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L55) | 当前 benchmark 不支持多 system |
| Benchmark metrics | runner 固定 top 10，只算 Recall@5/10，[benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L293) | 需独立 metric engine；不能用 Recall@10 作为唯一选择依据 |
| Runtime fingerprint | Python/platform/uv/PostgreSQL/pgvector，[benchmarking.py](../src/eve_relation_rag/literature/benchmarking.py#L103) | 缺 CPU/RAM/accelerator/backend/thread 等 hardware record |
| Benchmark CLI | 会加载 BGE、验证/重建 candidate corpus，然后跑 benchmark，[cli.py](../src/eve_relation_rag/cli.py#L727) | 不适合 production read-only ablation；新 CLI 必须独立 |
| CI/quality | CI 使用 PostgreSQL+pgvector 16 并运行 lock/docs/alembic/pytest/ruff/mypy，[ci.yml](../.github/workflows/ci.yml#L10) | Phase 1 新 tests 可沿用；DB integration tests 需要迁移到 head 的服务 |

## 4. Baseline representation contract 的实现落点

### 4.1 身份

- Repository ID：`BAAI/bge-small-en-v1.5`。
- Revision：`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`。
- Model key 包含 revision 与 `cls-l2norm-v1`。
- Production gate 不仅检查 key，还逐字段检查 repo/revision/dimension/pooling/prefix/license/runtime metadata。

对应常量见 [contracts.py](../src/eve_relation_rag/literature/contracts.py#L27)，gate 见 [gate.py](../src/eve_relation_rag/literature/gate.py#L126)。

### 4.2 输入、pooling 与输出

- Query 在用户问题前加固定 English instruction。
- Passage 不加 prefix。
- Model max sequence length 为 512。
- Query 超限会失败；document chunks 已被既有 BGE tokenizer policy 限到最多 448 tokens。
- SentenceTransformer encode 使用 normalized float32 output。
- 持久化前将每个值 canonicalize 到 little-endian float32，并检查 finite 与 L2 norm tolerance `1e-5`。

对应实现见 [local_bge.py](../src/eve_relation_rag/literature/local_bge.py#L127) 和 [embeddings.py](../src/eve_relation_rag/literature/embeddings.py#L83)。

### 4.3 Artifact identity

当前 BGE verifier 首先核对 manifest 文件自身 SHA-256，再核对 exact schema/identity 和目录中每个文件，最后要求 manifest file set 与实际 file set 完全相等。它没有联网 fallback。

这说明 ablation 的 `artifact_manifest_sha256` 不能仅是模型目录名或 Hugging Face cache snapshot 名；必须是内容清单的 approved SHA-256。

## 5. PostgreSQL/pgvector assumptions

### 5.1 ORM 与 migration

`EmbeddingModel` 有 `dimension=384`、`max_sequence_tokens=512`、`pooling=cls`、L2/cosine 等 check constraints，[db/models.py](../src/eve_relation_rag/db/models.py#L1674)。Migration 0006 建表时使用相同 constraints，[0006_m3_literature_retrieval.py](../migrations/versions/0006_m3_literature_retrieval.py#L193)。

`DocumentEmbedding.embedding` 为 `VECTOR(384)`，并使用 cosine HNSW index，[db/models.py](../src/eve_relation_rag/db/models.py#L1931)、[db/models.py](../src/eve_relation_rag/db/models.py#L2171)。Migration DDL 同样固定为 384，[0006_m3_literature_retrieval.py](../migrations/versions/0006_m3_literature_retrieval.py#L535)。

### 5.2 单 model release binding

`CorpusRelease` 只有一个非空 `embedding_model_id`，[db/models.py](../src/eve_relation_rag/db/models.py#L1755)。`DocumentEmbedding` 的 composite FK 指向 `(corpus_release.id, corpus_release.embedding_model_id)`，所以同一 release 的 embedding row 必须使用 release 所绑定的唯一 model，[db/models.py](../src/eve_relation_rag/db/models.py#L1947)。

结论：即使 Qwen/MedCPT 能输出 384 维，也不能直接向当前 release 追加它们的 rows。

### 5.3 Published corpus immutability

Migration 0006 给 document/chunk/embedding/anchor 等子表增加 published guard，[0006_m3_literature_retrieval.py](../migrations/versions/0006_m3_literature_retrieval.py#L861)。Migration 0009 将保护提前到 validated 状态，validated/published/retired release 的 child INSERT/UPDATE/DELETE 都拒绝，[0009_m3_validated_release_freeze.py](../migrations/versions/0009_m3_validated_release_freeze.py#L18)。

这些数据库 guards 是安全底线，但实验还应使用权限层只读账号和 pre/post fingerprint；不能依赖“写入后由 trigger 报错”作为正常控制流。

## 6. 当前 benchmark 对 multi-system 的支持情况

答案是 **不支持**：

- `BenchmarkDefinition` 直接把 retrieval policy 和 embedding model 写成 baseline `Literal`。
- `BenchmarkReport` 重复相同 hard-coded identity。
- `BenchmarkQuestionResult` 没有 `system_key`。
- `run_benchmark` 强制请求 top 10，并要求响应 identity 等于 definition。
- report 只含 Recall@5/10、citation/locator validity。
- 全仓库搜索 `system_key`、MedCPT、Qwen3 embedding、`RerankerProvider`、rerank 没有命中；现有 Qwen3 命中是 generation model，与本任务无关。

因此 Phase 1 应新增独立的 `AblationExperimentManifest`、`AblationSystemDefinition`、`AblationQuestionResult`、`AblationSummary`，不要放宽 production `BenchmarkDefinition`/`RetrievedChunks` 的 Literals。

## 7. 可复用与不可复用组件

### 7.1 可原样或通过 wrapper 复用

- `EmbeddingProvider` Protocol。
- `LocalBgeProvider`，作为 baseline 的 verified adapter；telemetry 在外层 wrapper 记录。
- `fuse_ranked_candidates` pure function，用于 exact RRF。
- Corpus/document/chunk/anchor contracts 和 canonical hashing helpers。
- Production FTS SQL 的语义与数据库已物化的 `fts_document`。
- 当前 artifact verifier 的 file-enumeration/checksum 安全规则。

### 7.2 不应直接复用

- `embed_candidate_corpus`：只支持 baseline 并写 candidate/validated corpus。
- `LiteratureRetrievalService`：硬绑定 published gate 和 baseline identity。
- `LiteratureRepository.retrieve`：依赖 release-bound production vector，且不能暴露 rerank top-50 pool。
- `run_benchmark`：单系统、固定 top-10、指标不足。
- `literature benchmark` CLI：包含 candidate rebuild 和 BGE composition。
- `canonical_embedding_sha256`：hash metadata 把 pooling 写死为 CLS，不适合其他 representation contracts。

## 8. Phase 1 新接口与模块

| 新接口/模块 | 责任 |
|---|---|
| `ModelArtifactManifest` | exact model/revision/license/files/representation/runtime identity；canonical hash |
| `ModelArtifactVerifier` | 本地 only、exact file set、size/SHA、symlink/path escape、runtime contract validation |
| `ModelRepresentationContract` | dimension/pooling/norm/similarity/query/passage format/max length/truncation |
| `RerankerProvider` | 用户指定的 positional `score(query, passages)` 协议 |
| `VerifiedEmbeddingAdapter` | 现有 `EmbeddingProvider` + representation contract + truncation/latency telemetry |
| `VerifiedRerankerAdapter` | score length/finite/order validation + batching/truncation/latency telemetry |
| `CorpusSnapshotReader` | 从 published DB 只读并生成 ordered checksum-bound snapshot/fingerprint |
| `SidecarVectorIndex` | exact float32 matrix、ordered keys、dimension/hash checks、full/summary filtering |
| `AblationRetriever` | exact FTS、dense branches、anchors、RRF、candidate pool、tier-aware rerank |
| `AnnotationManifest` | category/evidence groups/excluded/review status；仅 approved selection |
| `MetricEngine` | Recall@1/3/5/10、MRR@10、nDCG@10、category aggregates、exact quantiles |
| `TelemetryCollector` | phase latencies、RSS/accelerator memory、model/index sizes、truncation |
| `TrustedRunGate` | real verified providers、approved gold、same corpus/hardware、no mutation、complete outputs |
| `ReportGenerator` | canonical JSON/CSV 到 deterministic Markdown；无模型/DB access |

## 9. 依赖审计与建议

当前直接可选依赖只有 `sentence-transformers>=6,<7`，见 [pyproject.toml](../pyproject.toml#L36)。当前 lock 通过该 extra 间接包含 NumPy、PyTorch、Transformers、tokenizers 和 safetensors，但实验代码不应长期依赖未声明的 transitive dependencies。

Phase 1 在验证本地 artifacts/runtime 兼容性后，可提出一个独立 optional dependency group，例如 `embedding-ablation`，候选直接依赖包括：

- `torch`
- `transformers`
- `tokenizers`
- `safetensors`
- `numpy`
- `sentence-transformers`（只有 adapter 确实需要时）
- `psutil`（只有 stdlib `resource` 无法提供一致的 per-process peak RSS 时）

首轮 1464 chunks 使用 NumPy exact scoring 即可，不需要 Faiss/ScaNN/额外 ANN dependency。Phase 0 没有修改 `pyproject.toml` 或 `uv.lock`。

依赖版本不能先拍脑袋决定：必须先证明 pinned local MedCPT/Qwen artifacts 能在 `trust_remote_code=False`、offline 模式下由选定 runtime 加载。若不能，则 system 被拒绝，而不是打开 remote code。

## 10. 必需的模型 artifacts 与尚缺信息

### A — Current baseline

- BGE-small local directory。
- BGE artifact manifest + approved SHA-256。
- 当前 exact revision/representation 已由代码固定。

### B — BGE + MedCPT reranker

- A 的全部 artifacts。
- `ncbi/MedCPT-Cross-Encoder` local directory/manifest。
- exact revision、license、max length、pair serialization、truncation、score extraction/logit semantics。

### C — MedCPT retrieval

- `ncbi/MedCPT-Query-Encoder` local directory/manifest。
- `ncbi/MedCPT-Article-Encoder` local directory/manifest。
- 一个 bundle manifest 证明 query/article revisions 与 representation 兼容。
- exact dimension/pooling/norm/query/article format/max length。
- 可选 Cross-Encoder artifact。

### D — Qwen3 retrieval

- `Qwen3-Embedding-0.6B` local directory/manifest。
- exact revision/license/runtime compatibility。
- 官方/本地可验证的 384 output contract；不能以实验自定义截断代替。
- 可选 `Qwen3-Reranker-0.6B` local directory/manifest 及 pair/score contract。

仓库中没有这些 MedCPT/Qwen retrieval artifacts、pinned revisions 或 manifests，Phase 0 也没有联网核验。它们是 Phase 1 的外部准入材料，不应在设计文档中猜测。

## 11. 本机只读观察到的 baseline evidence

以下数据来自 `.gitignore` 已忽略的 `.artifacts/`（忽略规则见 [.gitignore](../.gitignore#L7)），用于把用户给出的 preliminary numbers 与本地状态交叉核对。它们**不是 tracked repository source of truth**，其他 clone 不能据此复现；Phase 1 必须显式传入 approved paths/hashes。

| Evidence | 观察值 |
|---|---|
| Corpus release | `corpus:endoviho-rag:v0:20260829:001` |
| Corpus canonical manifest SHA-256 | `a96fe244fa82ddbba0c24f7cee16753a5f1194b91c37af9cf27380c6368be929` |
| Corpus manifest file SHA-256 | `7de4ac177e11a1a79feeac885617b766380eeca087aaa8aca2f21f9f903ef1c4` |
| Documents/chunks/embeddings | 11 / 1464 / 1464 |
| Anchor count / canonical hash | 30 / `43e5010c1cd8af747b451f602099da659a31daeb1ea9d8514ffb6de251617ef7` |
| BGE artifact manifest file SHA-256 | `0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a` |
| Existing question count | 13 |
| Existing gold SHA-256 | `ded2a89f666ee8293cb422abecd95581688bd3e626018c25df5f2fd7097b7d2b` |
| Existing benchmark manifest SHA-256 | `a8ec010feff9ccc08b95c04dd37a71a09e5c5150bfabcd530b06823e3b8dc080` |
| Existing benchmark definition file SHA-256 | `2f8dd91d407bf043e421f614ae1131cf845abcd45f208ac36f05a21791318a90` |
| Preliminary Recall@5 / Recall@10 | `0.846153846154` / `1.000000000000` |
| Existing report file SHA-256 | `8d4670da9e7ca8b150f831ff0a424ac61a785225ed997cbc41910833804d9038` |

现有 13 题的 fields 只有 `anchors`、`question`、`question_key`、`relevant_chunk_keys`。这些 labels 不会在 Phase 0 被修改；迁移到新 annotation schema 后默认必须是 `pending`，直到专家批准。

## 12. 已有测试可提供的基础

当前 test suite 已覆盖一些可复用的安全性质：

- `tests/literature/test_local_bge.py`：missing model、manifest file set、wrong revision、checksum mismatch、cold import。
- `tests/literature/test_embeddings.py`：维度、finite、normalization 和 deterministic fake。
- `tests/literature/test_m33_embedding_postgres.py`：artifact identity 和原子 embedding build。
- `tests/literature/test_m34_retrieval_postgres.py`：retrieval model/artifact mismatch。
- `tests/literature/test_m35_validation_postgres.py`：validated corpus mutation rejection。
- `tests/literature/test_benchmarking.py`：benchmark identity、hash 和 Recall@5/10 精确复算。
- `tests/literature/test_fusion.py`：RRF 排名与 tie-break。

这些测试不覆盖：reranker、multi-system、MRR/nDCG、latency/resource、category、approved-only gold、sidecar isolation、candidate top20/50 或 deterministic report。因此 Phase 1 需要设计文档第 14 节的新矩阵。

## 13. 阻断项

### 必须在真实模型运行前解决

1. **模型 provenance 缺失。** MedCPT/Qwen3 各 component 的 exact revision、license、local manifest SHA-256 和本地目录尚未提供。
2. **384 维能力未确认。** 本地仓库没有足够信息证明 MedCPT/Qwen3 可安全输出 384；禁止猜测、投影或修改 pgvector。
3. **Gold approval 缺失。** 现有 13 题没有 category、alternatives/excluded 或 review status；在专家审核前只能保留为 preliminary。
4. **多系统 benchmark contract 缺失。** 无 system key、rerank、所需 quality/latency/resource metrics 或 plot-ready outputs。
5. **数据库模型不适用。** 当前 release/model FK 与 `VECTOR(384)` 阻止并行模型；必须使用 sidecar 或另批独立 schema。
6. **候选接口不适用。** Production repository 内部 depth 为 100，但 public request/output 最大 20，且 repository 在 hydrate 前按 final top-k 截断，不能直接拿到 top 50。
7. **硬件记录不完整。** 当前 runtime fingerprint 不足以证明同 hardware/backend/thread 条件。

### 不阻断 Phase 1 框架实现

- Sidecar exact matrix 能在不改数据库的情况下支持框架与 fake/test fixtures。
- 现有 pure RRF、BGE provider、hashing 和 DB immutable identities 可复用。
- 模型 artifacts 到位前，可以先实现/测试 trust gates、annotation schema、metrics、report generator 和 baseline parity harness，但不能产生真实可信比较报告。

## 14. 可能需要的 schema 变化

Phase 1 推荐值为 **无 production schema 变化**。详细 proposal 见 [设计文档第 13 节](embedding_ablation_design.md#13-pgvectorschema-proposal仅提案不实施)。

若用户后续明确要求 DB-backed non-384 index，则需要另行 proposal，至少包括：

- 将 content corpus identity 与 retrieval index/system identity 解耦；不改变当前 release 的 baseline binding/receipt。
- 在独立 experiment database/schema 中建立 run/model/system/index tables。
- 按 dimension 使用 `VECTOR(N)` 专用表和匹配 index，避免 mixed-dimension untyped vector。
- 只从 production 只读 snapshot 导入 keys/vectors；不复制可提交的论文全文。
- 权限、容量、rollback、hash binding 和 exact-vs-ANN parity 证明。

本 Phase 0 没有创建 migration，也没有修改 ORM。

## 15. 指定验证命令

以下命令已在完成两份 Phase 0 文档后从仓库根目录原样执行：

| 命令 | 结果 |
|---|---|
| `uv run pytest` | PASS — `988 passed, 1 warning in 67.79s`；warning 是 FastAPI TestClient 引入的第三方 `StarletteDeprecationWarning` |
| `uv run ruff check .` | PASS — `All checks passed!` |
| `uv run mypy src app` | PASS — `Success: no issues found in 111 source files` |
| `uv lock --check` | PASS — `Resolved 114 packages in 6ms` |
| `uv run alembic check` | PASS — PostgreSQL autogenerate parity 完成，`No new upgrade operations detected.` |

这些命令没有运行模型、下载 artifact、执行 Alembic upgrade、运行新真实 benchmark 或修改数据库 schema。

## 16. 下一阶段建议

Phase 1 建议先做“可信框架”，后接真实模型：

1. 新增独立实验 contracts、annotation schema、artifact/trust gates 和 deterministic metric/report tests。
2. 新增 production read-only corpus snapshot/fingerprint 与 `.artifacts/` sidecar exact index。
3. 用现有 verified BGE 做 baseline branch/rank/RRF/top-10 parity；不运行新的正式 benchmark。
4. 实现 `RerankerProvider` 与 fake-only tests，验证长度、finite、order、tier 和 telemetry。
5. 用户提供并批准每个真实 component manifest 后，再实现 MedCPT/Qwen adapters。
6. 13 题经专家迁移/审批后，才运行可信真实 benchmark 并生成机器结果与 Markdown 报告。

Phase 0 完成后停止，等待 Phase 1 明确批准。
