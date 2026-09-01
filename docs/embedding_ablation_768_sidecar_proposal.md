# MedCPT 768-dimensional experiment sidecar proposal

- Status: **pending approval**
- Scope: retrieval ablation only
- Production PostgreSQL/pgvector schema change: **none**
- Production embedding defaults change: **none**
- Trigger: the released MedCPT Query and Article encoders produce 768-dimensional
  `[CLS]` representations; no approved 384-dimensional representation exists.

## 1. Decision requested

Approve one narrowly scoped exception for System C only:

> Store MedCPT Query/Article embeddings in a new, immutable 768-dimensional experiment
> sidecar while keeping the published corpus, production `vector(384)` rows, database
> schema, FTS branch, chunks, anchors, RRF settings and `top_k` unchanged.

This proposal does **not** authorize downloading model artifacts. Model acquisition,
revision pinning, license review and checksum approval remain separate gates.

## 2. Evidence for the dimension

The official NCBI model cards demonstrate both encoders by taking the last hidden-state
`[CLS]` vector and report an output shape of `[batch, 768]`:

- <https://huggingface.co/ncbi/MedCPT-Query-Encoder>
- <https://huggingface.co/ncbi/MedCPT-Article-Encoder>

No truncation, padding, random projection or learned projection from 768 to 384 is
documented by the released models. Such transformations therefore remain prohibited.

## 3. Proposed isolated representation contract

| Field | Proposed value |
|---|---|
| Model pair | `ncbi/MedCPT-Query-Encoder` + `ncbi/MedCPT-Article-Encoder` |
| Exact revisions | Pending controlled artifact provisioning |
| Dimension | 768 for both query and article encoders |
| Pooling | CLS (`last_hidden_state[:, 0, :]`) |
| Query max length | 64 tokens |
| Article/chunk max length | 512 tokens |
| Query format | Exact question text |
| Passage format | Stable `[title, chunk_text]` pair serialization; no rechunking |
| Normalization | To be fixed after an offline contract test; must match for both encoders |
| Similarity | To be fixed with the normalization contract before a trusted run |
| Output dtype | float32 |
| Runtime | Local absolute paths, `local_files_only=True`, `trust_remote_code=False` |

The normalization and similarity rows are intentionally not guessed from the model
cards. The adapter contract test must establish them before the artifact manifests can
be approved.

## 4. Sidecar and system isolation

The existing exact sidecar format already records its own dimension and checksum. The
approved implementation would add a model-specific gate rather than make dimensions
generally configurable:

- BGE and Qwen3 first-round sidecars remain exactly 384-dimensional.
- MedCPT Query and Article artifacts must both declare exactly 768 dimensions.
- The only new system key is
  `medcpt_biencoder_768d__fts_dense_summary__rrf60`, with an optional separately keyed
  MedCPT Cross-Encoder reranking variant.
- Sidecars are written below a new experiment run directory with exclusive creation;
  no published corpus or embedding path may be reused as an output path.
- Query and article artifact identities plus their pair/bundle SHA-256 are stored in the
  experiment manifest.
- Production SQL is used only for the unchanged read-only FTS branch and corpus snapshot.
  No 768-dimensional value is sent to PostgreSQL or pgvector.

## 5. Comparison validity

Using different embedding dimensions is a declared system-level difference, not a hidden
normalization. Quality metrics remain directly comparable because every system receives
the same corpus bytes, chunks, questions, anchors, FTS candidates, RRF settings and
`top_k`. Resource results must expose the larger MedCPT index size and memory footprint,
so the report can assess the quality–latency–resource trade-off rather than quality alone.

The experiment report must label System C as `768d`; it must not imply a dimension-matched
comparison with the 384-dimensional BGE and Qwen3 systems.

## 6. Alternatives considered

1. **Truncate MedCPT to 384 dimensions — rejected.** The released model does not declare
   Matryoshka support, so truncation would create an unverified representation.
2. **Project 768 to 384 — rejected.** A random or learned projection changes the model and
   requires a separate training/validation experiment.
3. **Add a production `vector(768)` column/table — rejected for this ablation.** It expands
   the production schema and is unnecessary because exact sidecars are already isolated.
4. **Exclude MedCPT retrieval — safe fallback.** System B (BGE candidates + MedCPT
   Cross-Encoder) can still be measured without this proposal.

## 7. Approval gates

Implementation and execution of System C may begin only after all fields below are
resolved:

```text
decision: pending | approved | rejected
reviewer_id:
reviewed_at:
approved_proposal_sha256:
approved_query_encoder_revision:
approved_article_encoder_revision:
approved_query_artifact_manifest_sha256:
approved_article_artifact_manifest_sha256:
approved_encoder_bundle_manifest_sha256:
approved_normalization:
approved_similarity:
```

Approval of this proposal does not approve licenses or model bytes; the artifact verifier
continues to require a canonical manifest, complete file checksums and
`license_review_status=approved` for each component.
