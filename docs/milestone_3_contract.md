# Milestone 3 literature ingestion and retrieval contract — Fulfilled Draft A + Amendment v2

> Status: **FULFILLED — MILESTONE 3 COMPLETE; EXACT V2 CORPUS VALIDATED AND PUBLISHED**
>
> Product version: V0
>
> Project: EndoViHo-RAG
>
> Draft date: 2026-08-27 (Asia/Tokyo)
>
> Approval date: 2026-08-27 (Asia/Tokyo)
>
> Amendment v2 approval date: 2026-08-28 (Asia/Tokyo)
>
> Fulfillment date: 2026-08-28 (Asia/Tokyo)
>
> Scope: fixed-corpus ingestion, deterministic chunking, PostgreSQL FTS, pgvector retrieval,
> Reciprocal Rank Fusion, anchor filtering, and typed `RetrievedChunks`

## 1. Purpose and authority

This document is the fulfilled implementation contract for Milestone 3. The user approved Draft A
and authorized M3.0 on 2026-08-27, then explicitly authorized completion of all M3 engineering
stages before creating a pull request. The user separately approved the exact v2 corpus and anchor
manifests on 2026-08-28. After the frozen v1 pilot benchmark failed its recall thresholds, the user
approved Amendment v2; the resulting exact v2 release passed independent rebuild and pilot gates,
received an immutable trusted receipt, and was explicitly published on 2026-08-28. The amendment
changed only the retrieval/output policy described below; it did not change source membership,
labels, model, parser behavior, chunking parameters, FTS storage semantics, anchors, or thresholds.

Instruction precedence remains:

1. explicit user decisions in the project discussion;
2. `docs/data_semantics.md` and the approved Milestone 1 contract;
3. the approved Milestone 2 contract;
4. `EVE_RELATION_RAG_V0_AGENT_BUILD_GUIDE.md`;
5. the older source guides as background only.

This contract does not alter the EVE definition, structured truth schema semantics, candidate
release state, or Milestone 2 fail-closed publication gate. Literature is an explanatory and
retrieval layer. It cannot create an `EVELocus`, change a structured count, publish the Zhao
candidate, or substitute a document claim for an approved scientific assertion.

## 2. Approved target

Milestone 3 adds this deterministic path:

```text
exact published CorpusRelease + validated English question
    -> corpus capability gate
    -> optional system-derived anchor set
    -> PostgreSQL English full-text search
    -> pinned local embedding query
    -> pgvector cosine search
    -> Reciprocal Rank Fusion
    -> anchored tier followed by corpus-wide fill when required
    -> typed RetrievedChunks with stable locators and checksums
```

The implemented offline path is:

```text
explicitly approved local corpus manifest
    -> byte/checksum/license verification
    -> format-specific safe parser
    -> canonical document blocks and locators
    -> deterministic section-aware chunker
    -> PostgreSQL FTS vectors
    -> pinned local embeddings
    -> corpus validation and benchmark receipt
    -> explicit publication action
```

Milestone 3 does not add an LLM, router, answer composer, `ContextPack`, claim generation,
citation-to-claim validation, arbitrary PDF parsing, OCR, live web search, automatic document
discovery, reranking, arbitrary SQL, or changes to structured scientific truth.

## 3. M3-D01 — corpus release and publication boundary

**Approved decision:** every retrieval request must provide one exact, published
`corpus_release_key`.

- Key syntax is `corpus:endoviho-rag:v0:YYYYMMDD:NNN`.
- `latest`, aliases, silent defaults, and fallback to another corpus are forbidden.
- Corpus status is one of `candidate`, `validated`, `published`, `retired`, or `rejected`.
- Only `published` is queryable in V0. A later contract may permit exact retired releases.
- A published corpus is immutable. Corrections create a new release and explicit supersession.
- Candidate import, validation, and benchmark execution do not publish the corpus.
- Publication requires an exact manifest checksum and an immutable passing validation receipt.
- The fulfilled pilot is the exact published release
  `corpus:endoviho-rag:v0:20260828:001`; its corpus and anchor manifests were separately approved
  by checksum before staging and publication.
- A `CorpusRelease` is independent from `DatasetRelease`. No automatic `latest` or inferred
  relationship is allowed. A future Milestone 4 contract must bind exact structured and corpus
  releases for a hybrid request.

The Milestone 1/2 Zhao release remains candidate-only. Publishing a literature corpus does not
publish structured data and does not bypass `release_not_published`.

## 4. M3-D02 — separately approved corpus manifest

**Approved decision:** this contract approves the corpus mechanism, not a set of papers.

Before any non-synthetic document import, the user must explicitly approve one canonical corpus
manifest by SHA-256. The manifest must contain:

```text
manifest schema version
exact corpus release key
release title and purpose
document count and expected chunk count range
parser, chunking, embedding, FTS, retrieval, and anchor policy keys
one exact local file entry per document
file byte size, SHA-256, media type, source URI, and retrieval timestamp
title, authors, document version, DOI/PMID/PMCID when available
declared license and evidence URI
license-review status and whether retrieval text may be returned
expected document keys
manifest-level canonical SHA-256
```

Rules:

- The importer accepts local files listed in the manifest; it does not download documents.
- A URL, DOI, PMID, PMCID, title, or citation alone is not an import authorization.
- All document bytes, versions, and licenses must be frozen before import.
- The pilot corpus should contain 10–30 English documents covering the approved V0 questions,
  but the exact documents require separate user approval.
- The Zhao et al. paper is eligible for proposal in that manifest, but this contract does not
  include it automatically.
- A document with pending, unknown, incompatible, or rejected text-use rights may appear only in
  the manifest-validation audit report; it is not imported into corpus tables and cannot enter a
  published corpus.
- License review is an explicit human/curator decision. The importer does not make legal
  conclusions from a license string.

## 5. M3-D03 — accepted document formats and trust boundary

**Approved decision:** V0 imports only these UTF-8 formats:

| Format | Accepted boundary |
|---|---|
| Markdown | `.md`; CommonMark block structure; embedded HTML disabled and treated as text or rejected |
| Plain text | `.txt`; paragraphs separated by one or more blank lines |
| JATS XML | `.xml`; one `<article>` root; DTDs, external entities, XInclude, and network access disabled |

Explicitly rejected:

- PDF, scanned PDF, OCR output not manually approved as a separate text artifact;
- HTML, DOCX, RTF, EPUB, ZIP, and arbitrary binary formats;
- remote URLs as importer inputs;
- symbolic links, paths escaping the approved import root, and unmanifested files;
- files larger than 50 MiB or normalized text larger than 5,000,000 Unicode code points;
- malformed UTF-8, XML with prohibited constructs, or content that cannot produce stable
  locators.

Document content is untrusted data. Markdown HTML is not rendered, links are not followed,
JATS processing does not resolve external resources, and text resembling instructions is stored
and retrieved only as quoted document content. Milestone 3 never executes document text.

Implementation uses `markdown-it-py` with HTML disabled, the Python standard library for
plain text, and `defusedxml`/safe ElementTree behavior for JATS. These become direct locked
dependencies under the approved contract.

## 6. M3-D04 — normalized literature objects

**Approved decision:** Alembic revision `0006_m3_literature_retrieval` installs pgvector and adds
the literature layer without changing existing M1/M2 rows or semantics. Revisions 0007–0010
harden release-scoped anchor identity, child reparenting, validated-release freezing, and
validation locking without changing those semantics.

The approved conceptual objects are:

| Object | Responsibility |
|---|---|
| `LiteraturePolicy` | immutable parser/chunking/FTS/embedding/retrieval/anchor policy JSON and code checksum |
| `EmbeddingModel` | exact model identity, revision, dimension, pooling, normalization, instruction, license, and artifact manifest |
| `CorpusRelease` | exact immutable corpus version and all pinned policy dependencies |
| `Document` | immutable source artifact identity and bibliographic/license metadata |
| `CorpusDocumentMembership` | explicit corpus-to-document membership |
| `DocumentChunk` | corpus-scoped normalized text, section path, locator, token counts, FTS vector, and checksum |
| `DocumentEmbedding` | one model-qualified vector and checksum per chunk |
| `DocumentAnchor` | typed, provenance-bearing retrieval signal; never scientific truth |
| `CorpusImportRun` | checksum-bound importer execution and terminal counts |
| `CorpusImportLedger` | one terminal outcome per manifest document |
| `CorpusValidationReceipt` | immutable manifest, rebuild, license, completeness, and benchmark result |

Required relational invariants include:

- each chunk belongs to one document and one corpus release that explicitly contains it;
- each embedding belongs to one chunk and the release-pinned embedding model;
- a published corpus has exactly one passing receipt for its exact manifest and policy graph;
- every published document has approved retrieval-text rights and a verified source checksum;
- every published chunk has a non-empty locator, text checksum, FTS vector, and embedding;
- every embedding has exactly 384 finite values and the expected embedding checksum;
- all anchors refer to a document that is a member of the same corpus release;
- published and retired releases, memberships, chunks, embeddings, anchors, policies, and
  receipts are database-immutable;
- candidate replay with identical inputs reuses rows; any mismatch fails instead of updating;
- corrections require a new corpus release key.

No literature table is allowed to foreign-key into release membership in a way that implies a
literature anchor is structured truth.

## 7. M3-D05 — stable keys and canonical hashing

**Approved decision:** all derived keys are SHA-256 keys over canonical UTF-8 JSON with sorted
object keys, compact separators, Unicode NFC, and no non-finite numbers.

| Object | Key form |
|---|---|
| Corpus release | `corpus:endoviho-rag:v0:YYYYMMDD:NNN` |
| Document | `document:sha256:<64 lowercase hex>`; hash binds exact source bytes and canonical identity metadata |
| Chunk | `chunk:sha256:<64 lowercase hex>` |
| Anchor | `anchor:sha256:<64 lowercase hex>` |
| Import run | `corpus-import:sha256:<64 lowercase hex>` |
| Validation receipt | `corpus-receipt:sha256:<64 lowercase hex>` |

The chunk-key preimage contains:

```text
key schema version
corpus release key
document key
parser policy key
chunking policy key
section path
canonical locator JSON
zero-based chunk index
normalized chunk text
normalized text SHA-256
```

The document-key preimage contains the key-schema version, exact source-artifact SHA-256, media
type, declared document version, normalized DOI/PMID/PMCID values, and canonical title. The raw
artifact checksum remains a separate required field. A metadata correction therefore creates a
new immutable document identity instead of rewriting an old row.

The embedding checksum preimage contains the exact model key, chunk key, float32 vector encoded
in a canonical little-endian byte representation, dimension, pooling, normalization flag, and
query/passages mode. Changing any policy, model, document bytes or identity metadata, locator, or
chunk text produces a new derived identity or requires a new corpus release.

## 8. M3-D06 — parsing and canonical locators

**Amended identity:** parser policy key is `parser:endoviho-documents-v2`; parsing parameters and
normalization behavior are unchanged from Draft A.

Common normalization:

- decode strict UTF-8;
- normalize line endings to LF and Unicode to NFC;
- preserve case, punctuation, accession numbers, DOI text, equations, and scientific symbols;
- remove only format-control characters explicitly forbidden by the policy;
- trim line-end whitespace and canonicalize blank-line runs without rewriting prose;
- retain title, abstract, section hierarchy, paragraphs, list items, table text, table/figure
  captions, Methods, references, and supplementary text as distinct typed blocks;
- exclude executable markup, navigation chrome, styling, scripts, and external resources;
- record both exact source-artifact SHA-256 and normalized-document SHA-256.

Locator rules:

| Format | Canonical locator |
|---|---|
| Markdown | heading path plus typed block ordinal and source line range |
| Plain text | paragraph ordinal plus source line range |
| JATS | article section-title path plus element type, typed ordinal, and stable XML element path |

Locators are structured JSON, not display strings. A deterministic renderer produces the human
locator. Every locator must resolve back to the normalized source block in rebuild tests.

References are retained as searchable text but are not silently treated as evidence that the
cited external paper is present in the corpus. Tables and captions remain separate blocks; image
content is not interpreted.

## 9. M3-D07 — section-aware chunking

**Approved decision:** chunking policy key is
`chunking:bge-small-en-v1.5:384-64-448-v2`. Target, overlap, maximum, boundary rules, and tokenizer
are unchanged from Draft A; the identity is versioned with the corrected immutable v2 graph.

The exact policy is:

| Parameter | Value |
|---|---:|
| Tokenizer | tokenizer from the pinned BGE model revision |
| Target content tokens | 384 |
| Overlap content tokens | 64 |
| Hard maximum content tokens | 448 |
| Minimum | 64, except a terminal or indivisible typed block |
| Indexing | zero-based, monotonically increasing within one document/corpus release |
| Boundary priority | section, typed block, paragraph, sentence, then token boundary |

Rules:

- chunks never cross top-level sections or typed table/caption/reference boundaries;
- overlap is used only between consecutive splits of the same long logical block;
- overlap never crosses a section or typed-block boundary;
- a chunk is never silently truncated by the embedding provider;
- the title and section path remain metadata and are not prepended to the stored quoted text;
- token count is computed with the pinned tokenizer without query instruction and excluding
  model-added special tokens;
- an overlong indivisible token sequence is split at the hard token boundary with an explicit
  locator token span;
- empty or whitespace-only chunks are forbidden;
- reordering documents does not change document or chunk keys.

## 10. M3-D08 — embedding model and provider

**Approved decision:** the V0 production/local embedding model is
[`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5) at exact revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.

The approved model policy is:

| Parameter | Value |
|---|---|
| Model key | `embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1` |
| Provider | local `sentence-transformers`; no network at retrieval time |
| Dimension | 384 |
| Maximum model sequence | 512 tokens |
| Pooling | model-defined CLS pooling, verified against the frozen model artifact |
| Output | float32, L2-normalized |
| Passage prefix | none |
| Query prefix | `Represent this sentence for searching relevant passages: ` |
| Similarity | cosine |
| Model license metadata | MIT, verified and stored with the artifact manifest |

The model revision, dimension, sequence length, retrieval instruction, and normalization guidance
are recorded in the official model card and metadata. The real embedding build used an exhaustive
local model-artifact manifest listing every model file with byte size and SHA-256; its approved
manifest SHA-256 is
`0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a`. A Hugging Face revision
alone is necessary but not sufficient provenance.

Provider interface:

```python
class EmbeddingProvider(Protocol):
    @property
    def model_key(self) -> str: ...
    @property
    def dimension(self) -> int: ...
    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
    def embed_query(self, text: str) -> Sequence[float]: ...
```

- Tests use a deterministic fake provider and never call a paid or network API.
- The release benchmark uses the pinned local BGE provider, not the fake provider.
- Remote embedding providers are not implemented in Milestone 3.
- Provider/model failure, dimension mismatch, non-finite values, non-unit vectors outside the
  approved tolerance, or silent truncation fail closed.
- The query plus exact prefix must fit the 512-token model limit; otherwise return
  `query_too_long` and perform no retrieval.
- Changing model revision, pooling, normalization, instruction, or dimension requires a new
  embedding model key, rebuilt embeddings/index, benchmark, and corpus release.

The local embedding dependency is an optional project extra so ordinary CI can use fake
providers without downloading model weights. The exact Python packages are frozen in `uv.lock`.

## 11. M3-D09 — pgvector storage and index

**Approved decision:** migration 0006 runs `CREATE EXTENSION IF NOT EXISTS vector` and stores
embeddings as `vector(384)`.

The index policy is:

```text
index type: HNSW
operator class: vector_cosine_ops
m: 16
ef_construction: 64
query ef_search: 100, set locally per retrieval transaction
iterative_scan: strict_order
max_scan_tuples: 20000
```

Cosine distance uses pgvector `<=>`. Component ordering is distance ascending, then `chunk_key`
ascending. Every query is scoped to one exact published corpus and one exact embedding model.
No similarity threshold is applied in V0; relative rank, not an uncalibrated absolute BGE score,
is the retrieval signal.

The HNSW index and its usage are verified with PostgreSQL integration tests. Approximate search
must still satisfy the fixed corpus Recall@5/10 gates. If it does not, Milestone 3 stops; the
agent may not silently switch parameters or add a reranker without a contract amendment.

The verified publication runtime uses PostgreSQL `16.15` and pgvector `0.8.6`; the extension is
installed through the approved Alembic schema-change path.

## 12. M3-D10 — PostgreSQL full-text search

**Amended identity:** FTS policy key is `fts:postgres16:english-weighted-v2`; stored-vector weights,
configuration, query parsing, ranking, and depth are unchanged from Draft A.

- Text-search configuration is always explicitly `english`; no database default is relied upon.
- Each chunk stores a positional `tsvector` with weights:
  - document title: `A`;
  - section path and typed block label: `B`;
  - chunk text: `D`.
- The importer computes/stores the vector using PostgreSQL functions and verifies it on replay.
- A GIN index is created on the stored `tsvector`.
- Query parsing uses `websearch_to_tsquery('english', question)` because it accepts unformatted
  user text without exposing raw `to_tsquery` syntax.
- Ranking uses `ts_rank_cd(..., 32)`; component ordering is rank descending, then `chunk_key`
  ascending.
- FTS candidate depth is 100 chunks.
- If the normalized `tsquery` has no indexable terms, the FTS branch returns an empty ranked
  list; vector retrieval still runs and the response records the empty branch.
- PostgreSQL is the only online text/vector retrieval store. No separate search service is added.

The weighting and query behavior follow the PostgreSQL 16 full-text-search documentation. FTS
scores are used only to establish component rank; RRF combines ranks rather than raw score scales.

## 13. M3-D11 — vector candidates and Reciprocal Rank Fusion

**Approved decision:** retrieval policy key is
`retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2`.

| Parameter | Value |
|---|---:|
| FTS candidate depth | 100 |
| Vector candidate depth | 100 |
| Summary-vector candidate depth | 100 |
| Summary-vector block types | `title`, `abstract` |
| RRF constant `k` | 60 |
| Default `top_k` | 8 |
| Allowed `top_k` | 1..20 |
| Reranker | none |

Fusion is at chunk level across three equal branches:

1. strict PostgreSQL English FTS over all chunks;
2. cosine dense retrieval over all chunks;
3. cosine dense retrieval filtered to typed `title` and `abstract` chunks.

The summary branch reuses the exact pinned passage embeddings and HNSW query parameters. It does
not add another model, query rewrite, learned weight, or content mutation.

```text
rrf(chunk) = sum(1 / (60 + rank_in_branch))
```

Ranks are one-based. A chunk absent from a branch receives no contribution from that branch.
Duplicate chunk keys are collapsed before fusion. Final ordering is:

1. retrieval tier (`anchored` before `corpus_fill`);
2. RRF score descending;
3. number of contributing branches descending;
4. best component rank ascending;
5. `chunk_key` ascending.

`rrf_score` is serialized as a decimal string rounded to 12 decimal places. Component raw FTS
rank and cosine distance are internal diagnostics; the response exposes `fts_rank`,
`vector_rank`, and `summary_vector_rank`, which are the actual fusion inputs.

No weighted fusion, score threshold, cross-encoder, LLM reranker, diversity heuristic, or
document-level deduplication is added in Milestone 3. Multiple chunks from one document may be
returned when their final ranks warrant it.

## 14. M3-D12 — typed anchors and anchor-first retrieval

**Approved decision:** anchors are corpus-scoped, curated retrieval signals. They are never
scientific assertions, release membership, or evidence by themselves.

Allowed anchor types:

```text
locus
assembly
lineage
method
document
keyword
```

Typed target fields avoid ambiguous polymorphic strings:

- locus: exact `locus_key`;
- assembly: exact M2 `assembly_key`;
- lineage: exact `snapshot_key` plus exact `term_key`;
- method: exact `method_definition_key`;
- document: exact `document_key`, normalized DOI, PMID, or PMCID;
- keyword: exact curator-approved English phrase normalized by the anchor policy.

Exactly one typed target is populated per anchor. Each anchor records its source manifest row,
curation method, locator/provenance, and SHA-256. LLM-generated anchors and fuzzy automatic
anchors are forbidden.

Anchor-first behavior:

1. System-derived anchors are canonicalized, deduplicated, and validated.
2. Documents matching any supplied anchor form the anchored document set.
3. FTS/vector/RRF run within that set and produce the `anchored` tier.
4. If fewer than `top_k` chunks are returned, the same retrieval runs corpus-wide.
5. Already returned chunks are removed and the remainder fills a `corpus_fill` tier.
6. An empty anchor match does not become a silent unrestricted query: the response records
   `anchor_miss` before performing the explicitly defined corpus-fill stage.

Multiple supplied anchors use OR for document eligibility. The response records which anchors
matched each returned document. User-authored arbitrary anchors are not a public M3 input;
Milestone 4 may pass only anchors derived from a validated `StructuredResult`.

## 15. M3-D13 — request and `RetrievedChunks` contract

**Approved decision:** the internal retrieval request schema is
`literature-retrieval-request-v1` and the amended result schema is `retrieved-chunks-v2`.

The M3 application service accepts:

```json
{
  "request_schema_version": "literature-retrieval-request-v1",
  "corpus_release_key": "<exact published corpus release key>",
  "question": "What methods were used to identify these elements?",
  "top_k": 8
}
```

Rules:

- question is one line, `1..2000` Unicode code points, with no control characters;
- V0 questions are English; M3 trusts a typed internal boundary and does not add a statistical
  language detector or router;
- unknown fields, empty keys, `latest`, unsupported top-k values, and client-authored anchors
  are rejected;
- system anchors use a separate internal typed argument unavailable in the public request model;
- canonical `query_sha256` covers exact corpus key, question, top-k, sorted system anchors, and
  retrieval-policy key.

Successful result shape:

```json
{
  "result_schema_version": "retrieved-chunks-v2",
  "status": "ok",
  "corpus_release_key": "<exact key>",
  "corpus_manifest_sha256": "<64 lowercase hex>",
  "retrieval_policy_key": "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2",
  "embedding_model_key": "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1",
  "query_sha256": "<64 lowercase hex>",
  "requested_top_k": 8,
  "returned_count": 1,
  "retrieval_executed": true,
  "anchor_mode": "none",
  "anchors_applied": [],
  "warnings": [],
  "chunks": [
    {
      "citation_id": "D1",
      "chunk_key": "chunk:sha256:<64 lowercase hex>",
      "document_key": "document:sha256:<64 lowercase hex>",
      "title": "...",
      "doi": null,
      "pmid": null,
      "pmcid": null,
      "section": "Methods",
      "locator": {},
      "locator_text": "Methods, paragraph 3",
      "text": "...",
      "text_sha256": "<64 lowercase hex>",
      "retrieval_tier": "corpus_fill",
      "fts_rank": 2,
      "vector_rank": 1,
      "summary_vector_rank": 1,
      "rrf_score": "0.048915917504",
      "matched_anchors": []
    }
  ]
}
```

Citation IDs are response-local, unique, contiguous `D1..Dn`, and assigned after final ordering.
They are not persisted identities. A valid query that returns no chunks has `status = ok`, an
empty list, `retrieval_executed = true`, and an explicit `no_chunks_retrieved` warning. It does
not infer that no relevant literature exists and does not invent an answer.

Typed errors use `literature-retrieval-error-v1`, set `retrieval_executed` honestly, and never
return partial chunks after a capability, integrity, provider, or validation failure.

## 16. M3-D14 — fail-closed capability and integrity gates

Retrieval does not run when any of these conditions holds:

- corpus key is missing, malformed, unknown, candidate, validated, retired, or rejected;
- exact manifest, policy graph, model artifact manifest, or passing receipt is missing/mismatched;
- any published document lacks approved retrieval-text rights or a verified checksum;
- any membership, chunk, embedding, anchor, or expected-count invariant fails;
- any chunk lacks a resolvable locator or checksum;
- any embedding is missing, wrong-dimensional, non-finite, wrong-model, or checksum-invalid;
- query validation or model token-limit validation fails;
- embedding provider initialization or query embedding fails;
- PostgreSQL FTS, vector search, or RRF execution fails;
- a supplied internal anchor is malformed, unresolved under its typed policy, or silently dropped;
- any request condition is ignored.

Representative stable error codes:

```text
corpus_not_found
corpus_not_published
corpus_manifest_invalid
corpus_receipt_invalid
corpus_incomplete
document_license_not_approved
chunk_locator_invalid
embedding_incomplete
embedding_model_mismatch
embedding_provider_failed
query_too_long
anchor_invalid
retrieval_failed
unsupported_request
```

### 16.1 Database and administrative trust boundary

The PostgreSQL owner and administrators with direct write access to the corpus tables are a
trusted control plane and are outside the Milestone 3 adversarial threat model. Their credentials
must not be exposed to document content, retrieval clients, or a public endpoint. The only
supported validation and publication transitions are the administrative
`literature corpus-validate` and `literature corpus-publish` commands; direct SQL state mutation is
unsupported.

Receipt and manifest SHA-256 values are tamper-evident identities, not digital signatures or
proof that an untrusted actor executed the benchmark. A `CorpusRelease.status` value of
`published` alone never grants retrieval authority. `PublishedCorpusGate` must reload and validate
the complete typed receipt evidence, recompute receipt and policy-graph identities, bind the exact
manifest, model artifact, rebuild, benchmark, anchors, and gold judgments, and verify corpus
completeness before issuing a retrieval capability. Database triggers enforce lifecycle shape,
validated/published freezing, and concurrency serialization; they do not authenticate a database
administrator.

No failure may degrade silently to FTS-only, vector-only, a different model, a different corpus,
an unanchored search, or a larger scope. The only allowed empty component list is a successfully
executed FTS branch whose valid English `tsquery` has no indexable/matching lexemes; this state is
recorded and vector retrieval still uses the approved model.

## 17. M3-D15 — idempotent import, rebuild, and publication

The importer is manifest-first and atomic per corpus release:

1. verify manifest checksum and exact policy keys;
2. verify every local path, byte size, SHA-256, format, metadata, and approved license status;
3. parse and normalize all documents without database mutation;
4. deterministically produce blocks, locators, chunks, keys, FTS inputs, and expected counts;
5. generate embeddings with the exact pinned local model and verify vector invariants;
6. write candidate rows in one controlled transaction with batch size `500` chunks;
7. record one terminal ledger outcome for every manifest document;
8. emit a canonical import report with order-independent document/chunk/embedding/anchor digests;
9. replay the same manifest and require exact reuse with no mutations;
10. run the release validator and fixed pilot benchmark;
11. create an immutable validation receipt only when every gate passes;
12. require a separate explicit publish command naming the exact release and receipt checksum.

Import order cannot alter keys, chunks, rankings, counts, or digests. A candidate mismatch aborts
the whole transaction. There is no upsert that overwrites differing bytes or derived content.

Publication commands are administrative CLI operations and are never exposed through the public
HTTP API. The exact pilot corpus was published only after the user separately approved its inputs
and explicitly requested completion after reviewing the manifest and validation boundary.

## 18. M3-D16 — benchmark contract

Milestone 3 has two benchmark tiers.

### 18.1 Deterministic CI tier

- committed synthetic Markdown/text/JATS fixtures;
- deterministic fake embeddings with exact expected vectors;
- exact parser, locator, chunk, FTS rank, vector rank, RRF, anchor-tier, error, and rebuild tests;
- no model download, paid API, network access, or live document fetch;
- PostgreSQL 16 + pgvector production SQL path.

### 18.2 Pilot release tier

- exact approved corpus manifest and exact pinned local BGE model artifact;
- at least 10 English literature questions;
- each question has a curator-reviewed set of relevant `chunk_key` values;
- gold judgments are frozen by checksum and independent of retrieval output;
- macro Recall@K is the mean across questions of
  `|retrieved_at_k ∩ relevant| / |relevant|`;
- a question with no relevant gold chunks is invalid and blocks benchmark publication;
- anchored and unanchored cases are both represented;
- benchmark report records hardware, Python/locked dependencies, PostgreSQL, pgvector, model,
  policy keys, corpus manifest, gold checksum, per-question results, and aggregate metrics.

Required gates:

```text
Recall@5 >= 0.80
Recall@10 >= 0.90
citation ID validity = 100%
locator existence and resolvability = 100%
document/chunk/embedding/anchor rebuild digests = 100% exact
unknown/unpublished/incomplete corpus refusal = 100%
no network or paid provider in tests = 100%
```

Thresholds describe only the frozen pilot corpus. Failure blocks publication and Milestone 3
completion. It does not authorize parameter tuning after seeing test labels without a versioned
policy amendment and a newly recorded benchmark.

The v1 pilot result was Recall@5 `0.769230769231` and Recall@10 `0.846153846154`; citation and
locator validity were both `1.000000000000`, so publication remained blocked. Amendment v2
retained the exact questions and source judgments in a newly checksum-bound release/benchmark;
chunk keys were deterministically remapped to the same reviewed locators and text.

## 19. M3-D17 — application, CLI, and API boundary

Milestone 3 implements reusable application/repository services and administrative/developer CLI
commands for manifest validation, candidate import, rebuild validation, benchmark, publication,
and direct retrieval smoke tests.

Implemented M3 CLI namespace:

```text
eve-relation-rag literature manifest-validate
eve-relation-rag literature corpus-stage
eve-relation-rag literature corpus-validate
eve-relation-rag literature corpus-publish
eve-relation-rag literature retrieve
eve-relation-rag literature benchmark
```

Every mutating command requires exact keys/checksums and refuses implicit defaults. CLI output is
stable JSON; human logs go to stderr.

No public FastAPI literature route is added in Milestone 3. Milestone 4 owns router semantics,
hybrid orchestration, public literature/hybrid endpoints, `ContextPack`, LLM providers, answer
generation, and citation-to-claim validation. M3 retrieval can be exercised directly through the
application service, tests, benchmark, and developer CLI.

## 20. M3-D18 — implementation stages

Implementation proceeded in order and stopped at any failed gate until the required approval or
versioned amendment resolved it:

| Stage | Scope | Final status |
|---|---|---|
| M3.0 | approved contract, strict Pydantic schemas, canonical hashing, protocol interfaces, synthetic fixtures | complete |
| M3.1 | migration 0006, pgvector extension, models, constraints, immutability, capability gate | complete |
| M3.2 | safe Markdown/text/JATS parsing, normalized blocks, locators, chunking, idempotent candidate import | complete |
| M3.3 | fake provider, pinned local provider adapter, embedding validation/storage, GIN and HNSW indexes | complete |
| M3.4 | FTS/vector repositories, RRF, anchor-first retrieval, typed `RetrievedChunks`, CLI | complete |
| M3.5 | rebuild validator, pilot benchmark, documentation, exact exit verification | complete |

Tests were written or synchronized with each implementation stage, and schema changes use Alembic.
The real corpus was populated only after its exact corpus and anchor manifests were separately
approved. No Milestone 4 work was included.

## 21. Dependency and configuration boundary

Approved direct runtime dependencies used by the first stage requiring each dependency:

```text
pgvector Python adapter
markdown-it-py
defusedxml
```

The local BGE provider is an optional `local-embeddings` dependency group based on
`sentence-transformers`; exact package versions and hashes are resolved and frozen in `uv.lock`.
M3 does not add LangChain, LlamaIndex, a vector database service, a reranker, an LLM SDK, or a
remote embedding SDK.

Approved settings have no unsafe defaults:

```text
EVE_RAG_EMBEDDING_PROVIDER=local_bge
EVE_RAG_EMBEDDING_MODEL_PATH=<required verified local directory>
EVE_RAG_EMBEDDING_ARTIFACT_MANIFEST_PATH=<required exhaustive local JSON manifest>
EVE_RAG_EMBEDDING_ARTIFACT_MANIFEST_SHA256=<required approved lowercase SHA-256>
EVE_RAG_CORPUS_IMPORT_ROOT=<required for mutating corpus CLI commands>
```

The application never downloads a model or document at startup or query time. Test environment
uses dependency injection for the fake provider and cannot select a real remote provider.

## 22. Definition of done for Milestone 3

Milestone 3 is complete. Every required condition below was fulfilled:

- [x] this contract was explicitly approved;
- [x] one exact pilot corpus manifest was separately approved and checksum-frozen;
- [x] migrations through `0010_m3_validation_lock_hardening` upgrade a clean PostgreSQL database,
  and `alembic check` reports no drift;
- [x] pgvector is installed and the expected GIN/HNSW indexes exist;
- [x] Markdown, text, and JATS fixtures import reproducibly and prohibited formats fail closed;
- [x] real pilot documents imported with exact artifact and license provenance;
- [x] chunk keys, locators, text checksums, embeddings, and anchors rebuild exactly;
- [x] every published chunk has one exact model-qualified embedding;
- [x] FTS, vector, summary-vector RRF, anchored tier, and corpus-fill behavior match gold tests;
- [x] the pilot benchmark meets all M3-D16 thresholds;
- [x] citation IDs are unique/valid and every locator exists and resolves;
- [x] tests use no paid API, remote model, or network;
- [x] full pytest, Ruff, mypy strict, lock check, migrations, rebuild, and benchmark commands pass;
- [x] `docs/development_status.md` and README accurately describe the corpus and limitations;
- [x] the structured Zhao release remains candidate-only unless separately published through its own
  approved workflow;
- [x] no Milestone 4 code is present.

### 22.1 Final fulfillment and publication record

| Evidence | Final exact value |
|---|---|
| Corpus release | `corpus:endoviho-rag:v0:20260828:001` |
| Lifecycle | `published` at `2026-08-28T06:03:27.166490Z` |
| Corpus manifest SHA-256 | `1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0` |
| Anchor manifest SHA-256 | `75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca` |
| Model-artifact manifest SHA-256 | `0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a` |
| Policy graph SHA-256 | `a64f760dd33aca5e05779899f4fc74bf82ea1e9c6213eba7fd67e5a6411d6484` |
| Benchmark manifest SHA-256 | `856c46bc2ca5402151b95da2fddb8bf8ae44e7b535ed8c45382797b5a9e2db2e` |
| Gold SHA-256 | `470a4191c43c63833b508ce36937767b762fe380143cedc6fb3f2799432d6e82` |
| Receipt key | `corpus-receipt:sha256:d907aa3713b64fc72b9903daffb28da0eaff5eb9c0e29182dec65a131d9cf28e` |
| Receipt SHA-256 | `28f436d57630edd8403b71a503d23528fb7a1640432d8f623eca256b68858e7e` |
| Rebuild SHA-256 | `cb7f81388b9d79bc4588a81afd9a351df1ab87f7d479f8a3b3dc8ee10adac9c5` |
| Benchmark-report SHA-256 | `894dc74002c27e3f2cdf6a47970041d88cb91a8625ec8fad8f00f6c87d7c2565` |
| Rebuilt corpus counts | 11 documents; 1,464 chunks; 1,464 exact model-qualified embeddings; 22 anchors |
| Pilot benchmark | 13 questions; Recall@5 `0.846153846154`; Recall@10 `1.000000000000`; citation validity `1.000000000000`; locator validity `1.000000000000` |
| Final verification | 486 tests passed; Alembic head `0010_m3_validation_lock_hardening` |

## 23. Explicitly deferred

- live web search or automatic document download;
- PDF/HTML/DOCX import and OCR;
- multilingual queries or corpora;
- remote/paid embedding providers;
- rerankers or learned fusion;
- LLM, router, literature answers, hybrid answers, `ContextPack`, and claim generation;
- public literature/hybrid API endpoints;
- automatic anchor generation from model output;
- GraphRAG, Neo4j, or a second vector/search service;
- any structured release publication, flank assessment, inclusion decision, NCBI taxonomy
  completion, or ICTV binding.

## 24. Approval checklist

All approval and implementation items are complete:

- [x] D01 exact published corpus release and immutable publication boundary;
- [x] D02 separate checksum-bound document-manifest approval;
- [x] D03 Markdown/text/JATS-only safe local import;
- [x] D04 literature object model and migration 0006 boundary;
- [x] D05 key syntax and canonical hashing;
- [x] D06 parsing, normalization, and locator policy;
- [x] D07 BGE-tokenizer chunking at target/overlap/max `384/64/448`;
- [x] D08 pinned local BGE model revision, 384 dimensions, CLS/L2 normalization, exact query prefix;
- [x] D09 pgvector HNSW cosine parameters;
- [x] D10 PostgreSQL English weighted FTS;
- [x] D11 v2 FTS/vector/summary-vector depth `100/100/100`, equal RRF `k=60`, top-k default/range `8/1..20`;
- [x] D12 typed curated anchors and anchored-then-corpus-fill behavior;
- [x] D13 strict request/error/`RetrievedChunks` schemas;
- [x] D14 fail-closed capability and integrity gates;
- [x] D15 idempotent import, rebuild, receipt, and separate publication action;
- [x] D16 deterministic CI plus pinned-model pilot benchmark;
- [x] D17 developer/admin CLI with no public M3 FastAPI route;
- [x] D18 staged implementation order and no Milestone 4 work.

Approval chronology: Draft A and M3.0 were approved on 2026-08-27; completion of M3.1–M3.5 before
pull-request creation was then authorized. On 2026-08-28 the user approved Amendment v2 and
separately approved corpus manifest
`1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0` and anchor manifest
`75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca`. Engineering authorization
was not treated as data approval. Amendment v2 superseded the unpublishable v1 retrieval policy;
the v1 candidate and failed benchmark remain audit artifacts. The approved v2 release passed the
amended benchmark, received the exact trusted receipt recorded above, and was explicitly
published.

## 25. Engineering references

- [PostgreSQL 16 full-text query and ranking documentation](https://www.postgresql.org/docs/16/textsearch-controls.html)
- [PostgreSQL 16 text-search table and GIN index documentation](https://www.postgresql.org/docs/16/textsearch-tables.html)
- [pgvector official indexing, filtering, iterative-scan, and hybrid-search documentation](https://github.com/pgvector/pgvector)
- [BAAI/bge-small-en-v1.5 official model card](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [BAAI/bge-small-en-v1.5 exact model metadata](https://huggingface.co/api/models/BAAI/bge-small-en-v1.5)

These sources support engineering choices only. They do not define EVE biology or authorize any
document, structured record, assertion, or release membership.
