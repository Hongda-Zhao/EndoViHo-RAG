# Embedding 与 Reranker 对照实验（Phase 1）

- 状态：**可信实验框架已实现；真实多模型 benchmark 尚未运行**
- 实现基线：`70120595a0c8eb13b28d895b7162ab35c72dbed3`
- 实施日期：2026-09-01
- Production schema/defaults：**无变化**
- 新模型依赖、模型下载、运行时联网：**均未发生**

## 1. 交付结论

Phase 1 已在独立 package
[`experiments/embedding_ablation`](../src/eve_relation_rag/experiments/embedding_ablation/)
中建立 retrieval ablation 的安全骨架。它可以冻结 published corpus、使用 production-equivalent
PostgreSQL FTS、在数据库外建立 exact sidecar 向量、运行固定 RRF/anchor 语义、执行可选 rerank，
并计算及确定性导出指定的质量、延迟和资源结果。

本阶段没有改动 `bootstrap.py`、production settings、production retrieval、ORM、migration、
`pyproject.toml` 或 `uv.lock`。实验 package 也没有被 production composition root、API 或
Streamlit app 导入。

本阶段**没有**生成 `docs/embedding_reranker_ablation.md` 或真实
`benchmark/embedding_ablation/` 结果。正式报告只能由 trusted machine results 生成；当前缺少
MedCPT/Qwen 的获批本地 artifacts、对应 concrete adapters，以及经专家批准的新 annotation
manifest。

## 2. 已实现组件

| 组件 | 实现 | Phase 1 行为 |
|---|---|---|
| 实验与 annotation contracts | [`contracts.py`](../src/eve_relation_rag/experiments/embedding_ablation/contracts.py) | 固定 system、模型表示、硬件、问题类别、evidence groups 与 approved-only 规则 |
| Annotation I/O | [`annotations.py`](../src/eve_relation_rag/experiments/embedding_ablation/annotations.py) | checksum/canonical JSON 校验；旧 13 题只能迁移为 `pending`，不自动补 category 或 approval |
| Artifact verifier | [`artifacts.py`](../src/eve_relation_rag/experiments/embedding_ablation/artifacts.py) | 本地完整 file set、SHA-256、exact revision、license approval、dimension、symlink/path escape 检查 |
| Offline guard | [`offline.py`](../src/eve_relation_rag/experiments/embedding_ablation/offline.py) | 模型调用期间设置 Hugging Face offline flags，并拒绝 Python socket/DNS 访问 |
| Published corpus snapshot | [`corpus_snapshot.py`](../src/eve_relation_rag/experiments/embedding_ablation/corpus_snapshot.py) | gate-authorized published release、只读 transaction、正文不进入 fingerprint、前后 fingerprint 比较 |
| Exact sidecar index | [`sidecar.py`](../src/eve_relation_rag/experiments/embedding_ablation/sidecar.py) | float32 matrix、ordered chunk keys、完整 checksum；拒绝覆盖和错误维度，不写 pgvector |
| Passage indexing | [`indexing.py`](../src/eve_relation_rag/experiments/embedding_ablation/indexing.py) | 对冻结 chunks 按相同顺序批量 embedding，并记录 latency/truncation |
| Hybrid retrieval | [`retrieval.py`](../src/eve_relation_rag/experiments/embedding_ablation/retrieval.py) | 保持 FTS depth 100、full/summary dense、RRF 60、anchor-first/corpus-fill |
| Baseline parity | [`parity.py`](../src/eve_relation_rag/experiments/embedding_ablation/parity.py) | 比较 production 与实验 top-k、branch ranks、tiers 和 RRF score |
| Reranker protocol | [`providers.py`](../src/eve_relation_rag/experiments/embedding_ablation/providers.py) | 保留现有 `EmbeddingProvider`，新增用户指定的 positional `RerankerProvider` |
| Tier-aware reranking | [`reranking.py`](../src/eve_relation_rag/experiments/embedding_ablation/reranking.py) | 长度/finite/order 校验、稳定 tie-break、batch/latency/truncation；禁止跨 anchor tier |
| Measured runner | [`runner.py`](../src/eve_relation_rag/experiments/embedding_ablation/runner.py) | approved-only、统一 warm-up/iterations、稳定排名检查、四阶段 latency |
| Metrics | [`metrics.py`](../src/eve_relation_rag/experiments/embedding_ablation/metrics.py) | Recall@1/3/5/10、MRR@10、nDCG@10、category macro、nearest-rank p50/p95 |
| Trust/result contracts | [`trust.py`](../src/eve_relation_rag/experiments/embedding_ablation/trust.py)、[`results.py`](../src/eve_relation_rag/experiments/embedding_ablation/results.py) | fake=`test_only`、结构相似 provider 仍为 unverified；绑定 corpus/gold/provider/model metadata 与完整题集 |
| Deterministic outputs | [`reporting.py`](../src/eve_relation_rag/experiments/embedding_ablation/reporting.py) | 生成并重新校验 JSON/CSV；正式 Markdown 只接受 trusted machine results |
| Hardware/resources | [`telemetry.py`](../src/eve_relation_rag/experiments/embedding_ablation/telemetry.py) | CPU/core/RAM/OS/backend/thread/lock/PostgreSQL/pgvector、peak RSS |
| Production source guard | [`source_guard.py`](../src/eve_relation_rag/experiments/embedding_ablation/source_guard.py) | pre/post hash 全部非实验 production Python、app、migration、lock/default files |
| A/B/C/D definitions | [`systems.py`](../src/eve_relation_rag/experiments/embedding_ablation/systems.py) | 20/50 rerank depth、batch size、384 gate；MedCPT query/article 保持独立 artifact identity |
| Standalone CLI | [`cli.py`](../src/eve_relation_rag/experiments/embedding_ablation/cli.py) | artifact 验证、pending annotation 迁移/验证、trusted report 再生成；未注册到 production CLI |

## 3. System 支持状态

| System | 框架状态 | 真实运行状态 |
|---|---|---|
| A — BGE + FTS + dense + RRF | representation、sidecar 和 production parity harness 已实现 | 未运行新的正式 13 题 benchmark |
| B — A + MedCPT Cross-Encoder | verified reranker system factory、top 20/50、batch/tier/telemetry 已实现 | 缺本地获批 Cross-Encoder artifact 与 allowlisted adapter |
| C — MedCPT retrieval | query/article 两个 artifact 分开记录；组合 SHA 由两者确定性计算；384/semantics gate 已实现 | 缺两个 pinned artifacts、license/revision approval 与 adapter |
| D — Qwen3 retrieval | embedding/reranker system factory 与 native 384 gate 已实现 | 缺 pinned artifacts；不能证明 384 输出前不会运行 |

`VerifiedModelArtifact` 只证明本地 bytes 与 manifest。它不会自动让任意 Python 对象成为
trusted provider。真实 MedCPT/Qwen adapter 必须在 artifacts 到位后实现并加入明确的 concrete
allowlist；这避免 fake 或 test double 仅伪装 `model_key`/checksum 就生成正式报告。

## 4. 机器输出契约

可信 run 会一次性、拒绝覆盖地生成：

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

`experiment_manifest.json` 直接记录每个模型的 ID、exact revision、license、dimension、pooling、
normalization、similarity、query/passage format、max length、runtime key 与 artifact manifest
SHA-256，而不只记录一个可伪装的 model key。每个 system 必须覆盖相同 approved question IDs，
结果中的完整 system contract 必须与 manifest 相等，latency sample count 必须与 run policy 相等。

`docs/embedding_reranker_ablation.md` 由输出目录重新加载、校验和复算后生成；fake/test-only、
failed、非 canonical、缺文件、多文件、aggregate 不一致或 provider provenance 不完整时均拒绝。

## 5. 测试覆盖

新增测试位于 [`tests/experiments`](../tests/experiments/)，PostgreSQL read-only snapshot、FTS 与
baseline rank parity 集成测试位于
[`test_m34_retrieval_postgres.py`](../tests/literature/test_m34_retrieval_postgres.py)。覆盖：

- wrong model/revision/task/dimension、missing/extra artifact、checksum、symlink 与 non-canonical manifest；
- embedding NaN/+Inf/-Inf、float32 dimension 与 L2 normalization；
- reranker 少分、多分、NaN/+Inf/-Inf、位置绑定、candidate 不删除、anchor tier 与 telemetry；
- pending/approved annotation、alternative evidence group、excluded hits 和精确指标；
- sidecar tampering、错误 identity/dimension、只创建不覆盖；
- fake provider 不得生成 trusted report，结构相似 provider 即使持有 verified artifact 仍不可信；
- production source fingerprint、published corpus 前后 fingerprint、FTS read-only 与 baseline parity；
- 所有指定 JSON/CSV 文件的确定性重建与正式 Markdown trust gate。

最终验证命令与结果将在本文件第 7 节记录。

## 6. 仍阻断真实 benchmark 的材料

1. `ncbi/MedCPT-Query-Encoder`、`ncbi/MedCPT-Article-Encoder`、
   `ncbi/MedCPT-Cross-Encoder` 和 Qwen3 components 的本地绝对目录、canonical artifact manifest、
   approved manifest SHA-256、exact revision、license 与 representation contract。
2. 基于这些 exact artifacts 实现并测试 concrete local-only adapters；禁止 repository ID 加载、
   `trust_remote_code=True` 或联网 fallback。
3. 将现有 13 题人工补齐 category/evidence groups/alternatives/excluded，并由专家填写 reviewer、
   timestamp 与 `review_status=approved`。Codex 不会代填真实 gold labels。
4. 确认正式 run 的只读数据库账号、warm-up/iteration policy、reranker batch size，以及 B20/B50
   是否同时运行。
5. 在提交 Phase 1 代码后使用 clean source commit 执行；dirty source 不能生成 trusted report。

若任一 encoder 无法安全产生 384 维，本阶段不会修改 pgvector 或 production schema；应先提交
独立 schema proposal。当前推荐继续使用 sidecar，production schema 变化仍为 **0**。

## 7. 验证结果

以下命令均从仓库根目录执行：

| 命令 | 结果 |
|---|---|
| `uv run pytest` | PASS — `1031 passed, 1 warning in 74.98s`；warning 为第三方 FastAPI TestClient 的 `StarletteDeprecationWarning` |
| `uv run ruff check .` | PASS — `All checks passed!` |
| `uv run mypy src app` | PASS — `Success: no issues found in 135 source files` |
| `uv lock --check` | PASS — `Resolved 114 packages in 6ms` |
| `uv run alembic check` | PASS — `No new upgrade operations detected.` |
| `uv run python scripts/check_docs.py` | PASS |
| `uv run python -m eve_relation_rag.experiments.embedding_ablation --help` | PASS；CLI 冷启动且未导入模型 runtime |

全量 suite 包含 41 个新增 experiment 单元测试，以及 2 个新增 PostgreSQL 集成场景：published
corpus/FTS 前后不变和 baseline branch/rank/RRF/top-10 parity。
