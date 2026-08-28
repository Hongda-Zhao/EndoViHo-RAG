# Development status

> Product version: V0
>
> Current milestone: Milestone 5 — demo/release packaging mechanism fulfilled locally
>
> Status: M5 local engineering gates passed; real activation and external publication remain
> intentionally blocked
>
> Last verified: 2026-08-28 (Asia/Tokyo)

## Milestone 5 status — packaging FULFILLED locally, activation blocked

The user authorized completion of the remaining Milestone 5 scope. The current branch is
`codex/milestone-5-demo-release`, stacked on M4 commit
`d75e6bb8d618ac8f393f34c3d067e06e5601b154` while M4 PR #4 remains open. The approved Draft A
contract is `docs/milestone_5_contract.md`.

Implemented M5 components now include:

- an English-only Streamlit evidence workbench with four strict real-state examples, visible
  fixed selectors, server-owned routing, and a three-stage execution rail;
- an HTTP-only client fixed to `POST /v0/query`, configured from a server environment origin,
  with strict origin validation, zero redirects/retries, a 20-second operation timeout, bounded
  response bytes, complete M4 canonical revalidation, exact request/response binding,
  HTTP/envelope status agreement, and sanitized failures;
- explicit structured, generation, and insufficiency limitations; anchor diagnostics;
  mechanical validation scope; and citation document/chunk/locator/checksum provenance;
- a non-root Docker runtime and Compose chain `db -> migrate -> api -> demo`, with loopback ports,
  one-shot Alembic migration, read-only API/Demo filesystems, dropped capabilities,
  no-new-privileges, separated backend/frontend networks, and no automatic data/model/provider
  activation;
- a security allowlist Docker build context that excludes Git, `.env`, restricted artifacts,
  source/model/data bytes, local tools/environments, caches, tests, and build outputs;
- canonical checksum-bound benchmark and V0 checklist JSON, deterministic Markdown projections,
  drift checks, `DATA_LICENSE`, `CITATION.cff`, and an Unreleased V0 changelog;
- focused client, AppTest, import-boundary, route-example, container-contract, metadata,
  machine-artifact, and legal-boundary tests.

M5 completion is classified separately from product activation:

| Classification | Current state |
|---|---|
| M5 implementation | **FULFILLED locally on 2026-08-28**: full-suite, frozen benchmark, package, migration, documentation, and fresh-volume container gates passed |
| Software distribution | preview ready but not published; no tag, GitHub Release, PyPI upload, or image publication |
| V0 Definition of Done | **BLOCKED** because real structured/literature/hybrid success and real structured publication are not complete |
| Real hybrid activation | **BLOCKED** by structured publication, binding/anchors, provider/egress, and human semantic review |

Final local M5 evidence:

| Gate | Result |
|---|---|
| Full pytest | 724 passed; one upstream Starlette/TestClient deprecation warning |
| Frozen M2/M3/M4 selection | 72 passed |
| Static and lock | Ruff passed; strict mypy passed over 84 source files; 114 packages locked |
| Migrations | one head/current `0010_m3_lock_hardening`; no model drift; clean 0001-to-0010 replay passed |
| Distribution | wheel and sdist built and audited; required examples, metadata, quick-start assets, and full migration chain present; restricted artifacts absent |
| Containers | fresh volume, one-shot migration, loopback health, Demo-to-API UI/refusal path, non-root/read-only runtime, and exact cleanup passed |

Remote pull-request CI is not pre-claimed by this local record and is tracked separately after
push.

The empty Docker quick start proves platform startup and canonical refusal behavior. It does not
bundle or publish a scientific pilot. Synthetic success remains tests-only and is not selectable
by Demo, API, CLI, settings, or Compose.

## Milestone 4 status — mechanism FULFILLED, activation blocked

The user approved Milestone 4 Contract Draft A and authorized implementation through a pull
request. At M4 completion, branch `codex/milestone-4-hybrid-rag` was based on M3 merge commit
`46e2155811978d407776894caab01f19504edb2b`. The routed-RAG mechanism passed its final
repository-wide local acceptance gates and is the base of the current M5 branch. This historical
M4 status does **not** claim real provider generation or real Zhao hybrid activation.

| Classification | Current state |
|---|---|
| Engineering mechanism | **FULFILLED**: strict contracts, four-route router, binding registry, anchor resolver, ContextPack, provider protocol, composer, validators, deterministic rendering, shared application, API, CLI, and deterministic tests passed the final local gates |
| Milestone fulfillment | **FULFILLED locally on 2026-08-28**; requested PR is authorized, while remote CI remains a separate external result |
| Real literature answer generation | Blocked: production bootstrap supplies no composer and `llm_provider` accepts only `disabled` |
| Real Zhao hybrid activation | Blocked independently by structured publication, exact binding, structured-target anchors, provider/egress approval, and human semantic review |

Implemented M4 components:

- strict immutable `rag-query-request-v1`, route, binding, context, provider, draft,
  composition, answer, execution-flag, and error contracts with canonical JSON/SHA-256 identities;
- a side-effect-free deterministic router that imports no database, embedding, or LLM runtime;
- an exact checksum-approved local binding-manifest registry, loaded lazily only for a hybrid
  request and unavailable by default;
- round-trip validation of M2 `QuerySuccess`, trusted extraction of exact structured targets, and
  capability-scoped lookup of existing M3 anchors with manifest-row, typed-shape, preimage,
  anchor-key, and checksum validation;
- immutable `ContextPack` construction, a dependency-free runtime-checkable `LLMProvider`
  protocol, exact provider/prompt identity checks, one-call/no-retry composition, constrained
  drafts, all-or-none mechanical validation, and deterministic application rendering;
- one fixed route orchestration service with explicit actual-call flags and no fallback to a
  different route, release, corpus, or unvalidated partial answer;
- public `POST /v0/query` and `eve-relation-rag rag query`, both using the same application and
  canonical `rag-error-v1`/success serialization while preserving all existing M2/M3 adapters;
- a checksum-bound 30-case router benchmark with route counts `5 structured / 5 literature /
  10 hybrid / 10 unsupported` and manifest SHA-256
  `ad4142226ec986efec6dc26ee8125e679b12489d5322ec797e0acfd7fd66e356`;
- a checksum-bound 14-case mechanical-generation benchmark with 13 hybrid cases across all six
  structured result variants plus unsupported zero-call refusal, manifest SHA-256
  `538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa`.

Primary implementation evidence:

| Area | Local source |
|---|---|
| Contracts, responses, limits | `src/eve_relation_rag/hybrid/contracts.py` |
| Router | `src/eve_relation_rag/planning/router.py` |
| Exact binding manifest | `src/eve_relation_rag/hybrid/bindings.py` |
| Corpus-scoped structured anchors | `src/eve_relation_rag/retrieval/hybrid/anchors.py` |
| Context/provider/composition/validation | `src/eve_relation_rag/generation/` |
| Fixed route orchestration | `src/eve_relation_rag/application/rag.py` |
| Production-safe composition | `src/eve_relation_rag/bootstrap.py`, `src/eve_relation_rag/config/settings.py` |
| Public adapters | `src/eve_relation_rag/api/app.py`, `src/eve_relation_rag/cli.py` |
| Deterministic benchmarks | `tests/fixtures/m4/router_cases.json`, `generation_cases.json`, and matching `tests/benchmark/test_m4_*_benchmark.py` |

### Exact M4 route grammar

The outer router preserves the original printable-ASCII, single-line question and selects only
these fixed families. It does not decide scientific truth; an inner structured clause is still
subject to the unchanged M2 parser, audit, resolver, and release gate.

Clients cannot submit the route, a query plan/result, SQL, anchors, `ContextPack`, provider/model
identity, sampling controls, prompt policy, or citation IDs. Those values remain server-owned.

| Route | Exact question family | Required selector shape | Downstream behavior |
|---|---|---|---|
| `structured` | M2 `show`, `list`, or `count` family, with no literature suffix | `release_key` only; no corpus key or `literature_top_k` | unchanged M2 query once; no literature or generation |
| `literature` | case-insensitive `Explain the literature evidence for <topic>`, `Explain the literature methods for <topic>`, or `Explain the literature limitations for <topic>` | `corpus_release_key` only; no release key or page | exact M3 retrieval once; generate once only when chunks exist |
| `hybrid` | one M2 clause followed by exactly one terminal ` and explain the literature evidence`, ` and explain the literature methods`, or ` and explain the literature limitations` suffix | both exact release keys; optional page and `literature_top_k` | binding preflight, M2 query, trusted anchors, M3 retrieval, then generation when chunks exist |
| `unsupported` | every other family, selector mismatch, duplicate suffix, or prohibited topic | any | zero release, database, embedding, retrieval, or generation side effects |

The prohibited-topic vocabulary includes prevalence, biological absence, infection inference,
co-divergence, independent integration events, host-lineage comparison, new EVE detection,
sequence upload, BLAST/HMMER/Foldseek, phylogenetic placement, live web search, arbitrary or
text-to-SQL, multilingual output, and multi-turn memory. There is no invalid-structured-to-
literature fallback.

### Exact binding and execution order

A hybrid request first checks a strict local
`hybrid-release-binding-manifest-v1`. Each entry binds one exact structured release key and
manifest SHA-256 to one exact corpus release key and manifest SHA-256; the manifest carries its
own canonical self-excluding checksum, and configuration must separately pin that checksum.
Missing path/checksum, malformed or duplicate entries, an unapproved pair, or a gate-issued
manifest mismatch returns `hybrid_binding_unavailable` before fact retrieval. Draft A approves
no real manifest; tests inject only tests-owned bindings. Production configuration requires both
`EVE_RAG_HYBRID_BINDING_MANIFEST_PATH` and
`EVE_RAG_HYBRID_BINDING_MANIFEST_SHA256`; neither has an approved real value in M4.

The hybrid call order is fixed:

```text
route
-> exact binding preflight
-> published structured-release authorization
-> published corpus authorization
-> unchanged M2 QuerySuccess
-> trusted target extraction and exact same-corpus anchor resolution
-> one M3 retrieval
-> ContextPack
-> one provider call when configured and evidence exists
-> all-or-none mechanical validation
-> deterministic HybridRouteAnswer
```

Any upstream refusal stops all downstream calls. Empty literature retrieval returns
`insufficient_evidence` on a literature route; a hybrid route preserves the unchanged structured
result, adds the explicit insufficiency limitation, and does not call generation.

### Frozen M4 limits and trust boundary

| Parameter | Approved and implemented value |
|---|---|
| Public question | one printable-ASCII line, 1..2,000 characters |
| M4 literature `top_k` | default `8`, strict `1..8` even though direct M3 retrieval supports up to 20 |
| Context | canonical `context-pack-v1`, maximum `131,072` UTF-8 bytes and 8 chunks; no silent truncation |
| Answer-instruction policy | `answer:endoviho-rag:v0:grounded-document-claims-v1` |
| Answer-instruction source SHA-256 | `7f30766995041305f47c8ef867103af42d3f2394fc72eef37f3e42a2ad3f7684` |
| Canonical `AnswerInstructions` SHA-256 | `4e906e96688e67956017ee7935952d9aedb2926e087f15bae050a343a58be8c1` |
| Structured anchor targets/system anchors | maximum `64`; exact locus/assembly/lineage/method only; no fuzzy/document/keyword inference |
| Generated claims | maximum `16`; continuous `C1..Cn`; each claim cites 1..4 current `D#` chunks |
| Claim/evidence text | claim at most 1,000 printable-ASCII characters; each exact evidence span at most 500 |
| Provider output | maximum `32,768` UTF-8 bytes |
| Sampling/retry | `temperature = 0`; `retry_count = 0`; one provider call only |
| Provider deadline | the provider must enforce its pinned `timeout_seconds`; composer maps expiry to a sanitized failure and never retries |
| Validation scope | `mechanical`; exact citations/spans and provenance do not prove semantic entailment |
| Persistence | none for prompts, provider output, claims, or generated answers |

`ContextPack` is the only factual value admitted to an LLM boundary. It contains the original
question, exact `RetrievedChunks`, fixed checksum-pinned answer instructions, and—only for
hybrid—the validated M2 plan and unchanged structured result. It excludes engines/sessions, SQL,
capabilities, settings, credentials, embeddings, hidden documents, external results, provider
background knowledge, and conversation history. Every trust boundary uses strict JSON
round-trip validation so unchecked `model_copy` mutations do not bypass validation.

The mechanical validator checks current-response citation identity, exact evidence-span
membership, context/provider/prompt hashes, structured preservation, identifier and numeric-token
provenance, forbidden-inference phrases, required limitations, and size/count constraints. Final
generation records state `validation_scope = "mechanical"`. This proves contract conformance and
traceability only; real activation additionally requires a checksum-bound human claim-support
benchmark with zero unsupported claims.

### Production-safe disabled state and real blockers

Production settings define `llm_provider: Literal["disabled"] = "disabled"`, and
`get_rag_query_application()` always passes `composer=None`. No remote provider SDK, API key,
model revision, prompt-egress approval, or provider-selectable HTTP/CLI field exists. A literature
or hybrid request that reaches non-empty generation therefore returns
`llm_provider_unavailable`; `/health`, unsupported, structured, and existing M2/M3 operations do
not construct an LLM provider.

Real Zhao hybrid activation remains blocked because:

1. `release:endoviho-rag:v0:20260826:001` is candidate-only and cannot pass the M2 publication
   gate;
2. no real dataset/corpus binding manifest and independently approved checksum exist;
3. published corpus `corpus:endoviho-rag:v0:20260828:001` has document and keyword anchors but
   no locus, assembly, lineage, or method anchors derivable from `StructuredResult`;
4. no production provider/generation policy or structured/document egress permission is
   approved; and
5. no checksum-bound human semantic-support benchmark has been approved and passed.

Synthetic successes, release/corpus capabilities, bindings, structured-target anchors, and
providers remain under `tests/` and are not selectable through settings, API, or CLI.

### M4 final local acceptance gates — FULFILLED

The final evidence below was recorded from the consolidated working tree. It closes the M4
engineering-mechanism and local PR exit gates, but not the separate real-activation gate:

| Gate | Final local result |
|---|---|
| Full pre-existing plus M4 pytest suite with local PostgreSQL | `682 passed, 1 warning` |
| Frozen M2/M3/M4 benchmark selection | `72 passed`, including the checksum-bound router and mechanical-generation gates |
| Real-state fail-closed boundary | confirmed: Zhao remains candidate-only; no approved real binding, structured-target anchors, provider/egress policy, or human semantic benchmark exists |
| Ruff | full repository passed |
| strict mypy | passed for `78 source files` |
| `uv lock --check` | passed with `92 packages` |
| Alembic current database | exactly one head, `0010_m3_lock_hardening`; `alembic check` reported no drift |
| Alembic clean-history replay | a temporary empty PostgreSQL database upgraded from `0001_empty_baseline` through `0010_m3_lock_hardening`; final `alembic check` reported no drift; the temporary database was deleted |
| Production-boundary audit | production has no selectable fake or real binding manifest and composes no LLM; synthetic capabilities, bindings, anchors, and providers remain tests-owned |
| Schema/data mutation audit | M4 added no migration, schema change, production-data mutation, or generated-answer persistence path |
| Documentation and patch hygiene | Markdown parse/local-link checks and `git diff --check` passed |

The requested PR may therefore be opened. A future remote-CI result is not part of this local
fulfillment record and real hybrid activation remains blocked.

## Milestone 3 outcome

The user approved Milestone 3 Contract Draft A, Amendment v2, the exact v2 corpus and anchor
manifests, and completion through publication. M3.0–M3.5 and the real pinned-model pilot are
complete. The Milestone 3 DoD is satisfied; the V0 product DoD remains independently blocked.

Implemented scope:

- strict, frozen contracts, canonical JSON/SHA-256 identities, provider protocols, and exact
  corpus capability errors;
- Alembic revisions `0006_m3_literature_retrieval` through `0010_m3_lock_hardening`, eleven
  literature tables, pgvector, weighted English FTS, HNSW cosine indexing, release-scoped anchor
  identity, validation-time child freezing, serialized lifecycle transitions, relational
  invariants, and immutable publication guards;
- strict local UTF-8 Markdown, plain-text, and safe JATS parsing with stable rebuildable locators;
- section-aware BGE-tokenizer chunking at target/overlap/hard-max `384/64/448` tokens;
- manifest-first, atomic, idempotent candidate staging with exact file, license, model, policy,
  document, chunk, FTS, embedding, and ledger verification;
- a verified local-only `BAAI/bge-small-en-v1.5` provider boundary plus deterministic 384D test
  provider; neither downloads a model at runtime;
- PostgreSQL FTS and pgvector retrieval, deterministic three-branch RRF60, typed curated anchors,
  anchored-first tiering, corpus-wide fill, and strict `retrieved-chunks-v2` responses with
  response-local citation IDs;
- independent rebuild validation, deterministic and pinned-model benchmark tiers, trusted receipt
  creation, and exact manifest/receipt publication commands;
- developer CLI commands for manifest validation, staging, benchmarking, validation, publication,
  and direct retrieval. M3 itself added no public literature HTTP route or LLM path; the later M4
  engineering surface is recorded separately above.

Synthetic fixtures continue to prove the mechanism independently of the real release. They use
the deliberately synthetic future key `corpus:endoviho-rag:v0:20990101:001` and are test-only.

| Frozen synthetic artifact | Canonical SHA-256 |
|---|---|
| Corpus manifest | `887bd65b23cc9eca80657250dd0a5233e48c58a5c6a3072b13f2278485ee0b1a` |
| Curated anchor manifest | `93ecd80734ba120ae4b9d83954d1c7b71937ee2ff03a540ff9fa51f64c443599` |
| Benchmark definition | `cca5a1fef9a75581d961d2961ceb4e9f4d710211f7b01f6816873d2ba3e22446` |
| Benchmark gold | `2e11b046bba37359c90d36849a583477453ad2437b4b540d1f58c42f1166278f` |

Published pilot record:

| Publication item | Exact value |
|---|---|
| Corpus release | `corpus:endoviho-rag:v0:20260828:001` |
| Corpus manifest SHA-256 | `1497ea3383bea64d2bc4f17d2376dceb537b4f6c6f57ccb6eaf667b6589732f0` |
| Anchor manifest SHA-256 | `75a523bc6408f13b07ba283e6539734ec3b694f3dab59994a464d40d98b01fca` |
| Model artifact manifest SHA-256 | `0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a` |
| Benchmark manifest SHA-256 | `856c46bc2ca5402151b95da2fddb8bf8ae44e7b535ed8c45382797b5a9e2db2e` |
| Benchmark gold SHA-256 | `470a4191c43c63833b508ce36937767b762fe380143cedc6fb3f2799432d6e82` |
| Receipt key | `corpus-receipt:sha256:d907aa3713b64fc72b9903daffb28da0eaff5eb9c0e29182dec65a131d9cf28e` |
| Receipt SHA-256 | `28f436d57630edd8403b71a503d23528fb7a1640432d8f623eca256b68858e7e` |
| Independent rebuild SHA-256 | `cb7f81388b9d79bc4588a81afd9a351df1ab87f7d479f8a3b3dc8ee10adac9c5` |
| Benchmark report SHA-256 | `894dc74002c27e3f2cdf6a47970041d88cb91a8625ec8fad8f00f6c87d7c2565` |
| Published at | `2026-08-28T06:03:27.166490Z` |

The published graph contains 11 documents, 1,464 chunks, 1,464 embeddings, and 22 curated
anchors. The 13-question pinned-model benchmark passed with Recall@5 `0.846153846154`, Recall@10
`1.000000000000`, citation-ID validity `1.000000000000`, and locator validity
`1.000000000000`.

Verification on 2026-08-28:

| Check | Result |
|---|---|
| Complete pytest suite with local PostgreSQL | 486 passed |
| M3 deterministic benchmark | passed, including anchored and unanchored questions |
| M3 real pinned-model benchmark | 13 questions passed all approved publication thresholds |
| Ruff | full repository check passed |
| mypy | strict check passed for all 62 source files |
| Lock | `uv lock --check` passed; optional local embedding runtime remains locked |
| Alembic | current head `0010_m3_lock_hardening`; model drift check reports no new upgrade operations |
| Publication | exact v2 corpus is published; publication replay returned the same receipt and timestamp |

The separately approved CC-BY Europe PMC sources and approval packets remain Git-ignored under
`.artifacts/milestone3/`. Database administration is the trusted M3 control plane for staging,
receipt creation, and lifecycle mutation. This boundary does not claim protection from a
malicious database administrator. Conversely, a release status alone never authorizes a query:
the application gate verifies the exact manifest, receipt evidence, policy JSON and hashes,
recomputed policy graph, approved model artifact, retrieval-text rights, and embedding
completeness before issuing a capability. The M4 mechanism described above composes that
unchanged M3 capability and does not weaken its authorization boundary.

## Milestone 2 retained outcome

The approved merged Draft B is implemented through M2.5. M2.0 maps the existing 32-table
Milestone 1 truth layer to public retrieval semantics; M2.1 provides the strict plan/result/error
schemas. M2.2 adds deterministic controlled-English parsing and exact release-scoped resolution.
M2.3 adds the published-release capability gate, semantic validator, fixed SQLAlchemy compiler,
membership-rooted repository, and all five metrics. M2.4 adds HMAC-SHA-256 keyset cursors and
deterministic presentation. M2.5 composes the same application through FastAPI and Typer, freezes
the 31-case controlled-English planning/contract benchmark, and verifies facts separately through
a PostgreSQL production-path matrix.

No M2 migration or scientific data mutation was required. The Zhao structured candidate still
cannot produce a public success response: the production gate requires `published` plus a
trusted, immutable validation receipt and rejects it before resolver or fact retrieval. This is
independent of the published M3 literature corpus.

Key artifacts:

- `docs/milestone_2_contract.md`: approved merged Draft B and staged M2.0-M2.5 boundary.
- `docs/milestone_2_schema_mapping.md`: read-only M1-to-M2 authority and projection map.
- `src/eve_relation_rag/planning/query_plans.py`: strict plans, audits, canonical JSON, and hash.
- `src/eve_relation_rag/planning/parser.py` and `resolver.py`: controlled grammar and exact
  release-scoped entity resolution.
- `src/eve_relation_rag/retrieval/structured/results.py`: strict result and error envelopes.
- `src/eve_relation_rag/retrieval/structured/`: capability gate, validator, compiler, repository,
  cursor, pagination, service, serialization, and presentation.
- `src/eve_relation_rag/api/app.py` and `src/eve_relation_rag/cli.py`: question-first public
  adapters over one application service.
- `tests/benchmark/`: 31-case controlled-English planning and contract oracle.
- `tests/retrieval/structured/test_m23_postgres.py`: production compiler/repository fact matrix;
  `tests/planning/` and the remaining retrieval tests cover schema and boundary behavior.

### Repository and CI snapshot

| Item | Verified state on 2026-08-28 |
|---|---|
| Current remote `main` baseline | M3 merge `46e2155811978d407776894caab01f19504edb2b` |
| Milestone 2 integration | [PR #2](https://github.com/Hongda-Zhao/EndoViHo-RAG/pull/2) merged into `main` |
| Milestone 3 integration | PR #3 merged into `origin/main` at `46e2155811978d407776894caab01f19504edb2b` |
| Milestone 4 integration | PR #4 remains open; its remote quality checks were green when queried on 2026-08-28 |
| Active M5 work | branch `codex/milestone-5-demo-release`, stacked on M4 commit `d75e6bb8d618ac8f393f34c3d067e06e5601b154`; local gates fulfilled |
| M5 remote CI | not pre-claimed by this local record; evaluated after the M5 branch and pull request are pushed |
| Standalone GitHub issues | no current claim; issue state is outside this local engineering snapshot |
| `main` branch protection | last recorded as unprotected; current enforcement is unverified and remains a repository-governance task |

### Frozen Milestone 2 parameters

| Parameter | Approved value |
|---|---|
| Plan version / route | `endoviho-query-plan-v0.1` / `structured` |
| Response / result schema | `structured-query-response-v1` / `structured-result-v1` |
| Plan intents / result data kinds | 6 / 6 |
| Exact aggregate metrics | 5, including `distinct_contig_count` |
| Filter combination | AND only; at most 3 distinct filter types |
| Page limit | default 50; strict range 1..100 |
| Canonical plan hash | SHA-256 of sorted canonical JSON; only cursor is normalized to null |
| Release syntax | exact `release:endoviho-rag:v0:YYYYMMDD:NNN`; status remains gate-owned |
| Release authorization | exact key, `published`, valid manifest, trusted immutable receipt |
| Resolver order | assembly, locus, snapshot-qualified term, canonical name, curated alias |
| Cursor | forward keyset; HMAC-SHA-256; runtime secret at least 32 bytes |
| Public adapters | `POST /v0/structured/plan`, `POST /v0/structured/query`, Typer CLI |
| Query technology excluded | arbitrary SQL, LLMs, embeddings, and literature retrieval |

## Milestone 1 retained outcome

Milestone 1 implements the approved Draft B pilot as an auditable staging truth layer. It does
not claim that the staged rows are publishable EVEs. All selected source rows have deterministic
calls and terminal outcomes; source confidence, placement, scientific assertions, evidence,
curation decisions, and public release membership remain separate objects.

## Implemented scope

- PostgreSQL/SQLAlchemy truth schema with 32 domain tables.
- Structured Alembic revisions `0002_milestone_1_truth_layer`,
  `0003_m1_assertion_evidence`, `0004_m1_shared_intervals`, and
  `0005_m1_fail_closed_publication`; the repository migration head is
  `0010_m3_lock_hardening` after the independent literature revisions.
- Streaming XLSX importer for the canonical physical worksheet `S3`.
- Streaming byte-size/SHA-256 verification for the workbook and NCBI JSONL inputs.
- Exact ten-assembly and 12,233-contig resolution against frozen NCBI Datasets v2 reports.
- Independent deterministic identities for the immutable physical source row and each
  method-specific detection call; the latter includes the native assembly/contig/VR occurrence
  plus method/run identity.
- Coordinate-free locus identity using source snapshot, assembly, contig, native VR token, and
  identity-policy version; coordinates live in a separate placement object.
- Complete all-VR audit with frozen counts and order-independent call/locus key digests.
- Atomic, idempotent PostgreSQL staging with source records, calls, assessments, assertions,
  evidence, placements, and quarantine ledger rows.
- Release-membership schema and fail-closed validator gates without automatic membership;
  database triggers hard-disable promotion to `validated` or `published` until a trusted,
  immutable validation-receipt workflow exists.
- Immutable published-release guards and support for distinct source occurrences sharing an
  exact interval.
- Complete M2 deterministic structured-query stack with six intents, five metrics, AND-only
  filters, exact membership projections, and tests-only synthetic success capabilities.
- FastAPI liveness/structured endpoints, Typer CLI, deterministic tests, Ruff, mypy, and
  PostgreSQL-backed CI.

## Frozen pilot results

Selection is exactly the approved ten assembly accession.versions with source column
`J = Orthopolintovirales` and column `M = Bivalvia`; every VR value is included.

| Result | Count |
|---|---:|
| Selected source records / `DetectionCall`s | 39,495 |
| Unique coordinate-free locus keys | 39,495 |
| `source_high` (`HCVR = Yes`) | 71 |
| `source_low` (all other selected HCVR values) | 39,424 |
| `Integration` normalized candidates with exact placements | 38,968 |
| `Viral contig` terminal policy quarantines | 527 |
| Exact assemblies | 10 |
| Distinct source contigs resolved exactly | 12,233 |
| Source organism names | 9 |
| NCBI/source contig-length mismatches | 0 |
| Duplicate call, locus, or row-locator keys | 0 |

The two confidence branches sum to all 39,495 calls. The two VR-type branches also sum to all
39,495 terminal outcomes. No row is silently removed.

## Scientific and publication boundary

The following objects currently have no staged or public rows:

- independent left-flank assessments;
- independent right-flank assessments;
- explicit inclusion decisions;
- public `ReleaseLocusMembership` rows;
- public assertion memberships.

`source_high` is not evidence that both host flanks exist, and `source_low` is not an automatic
exclusion. Likewise, source `VR Type = Integration` is a Zhao et al. assertion, not a substitute
for independent flank evidence.

Public release remains blocked until:

1. both left and right flank assessments are independently recorded as `supported` under the
   same approved policy;
2. an explicit human- or policy-authorized `include` decision exists;
3. complete frozen NCBI Taxonomy assignments and merged/deleted TaxId history are loaded;
4. the required ICTV taxonomy snapshot is loaded and bound to the candidate release;
5. the release validator passes integrity, count, provenance, license, checksum, deterministic
   key, and reproducibility checks.

## Frozen source and authority provenance

### Zhao et al. Data S1

| Parameter | Frozen value |
|---|---|
| Source snapshot | `study-defined:10.1101/2025.04.19.649669:v4:data-s1` |
| Canonical filename | `649669_file12.xlsx` |
| Physical worksheet | `S3` |
| Byte size | `83,851,778` |
| SHA-256 | `79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800` |
| Conservative artifact license basis | `CC-BY-NC-ND-4.0` |
| Remote verification | byte-exact bioRxiv DC6/media-6 artifact verified |

The user's `Data S1.xlsx` is a semantically equivalent metadata-edited working copy, not the
canonical release artifact. Its different bytes and worksheet name are recorded separately.

### NCBI resolution package

| Parameter | Frozen value |
|---|---|
| Authority snapshot | `authority:ncbi-datasets-v2:18.36.0:20260826:pilot-resolution` |
| NCBI Datasets CLI | `18.36.0` |
| Assembly JSONL | 10 records; 39,377 bytes; SHA-256 `adcbef683cbc1ad592464e6a7ec64bd3d5612b91e4d44fb531d5d4cfdf4d81d4` |
| Sequence JSONL | 220,512 records; 59,941,556 bytes; SHA-256 `c96695fc44481f4b08c6bd4e56a439efb9baaf9332c8337d048fe5dab345e425` |
| Resolution result | 10/10 assemblies and 12,233/12,233 selected contigs exact; zero length mismatches |
| Usage basis | `NCBI-MOLECULAR-DATA-USAGE-POLICY` |

The raw reports, retrieval time, exact commands, binary checksum, and usage-basis URL are frozen
in `data/manifests/milestone1_zhao_v4_data_s1.json`.

### Frozen execution parameters

| Parameter | Approved value |
|---|---|
| Importer/method identity | `zhao-data-s1-import-v2` |
| Physical source-record key schema | `zhao-data-s1-source-record-v1` |
| Method-specific call-key schema | `zhao-data-s1-detection-call-v2` |
| Coordinate-free locus policy | `zhao-v4-contig-source-occurrence-v1` |
| Placement coordinate system | `0-based-half-open` |
| Default database batch size | `1,000` rows |
| Importer SHA-256 | `e9ff3cfcbcb3f20a6971b245ddd2d7fbbbe552a96021f33c8ac70a6f8c7be514` |
| Audit module SHA-256 | `93cbead58cdfca828a97c33bed1ab21a6d31c6115e52835a67e139f40f640b98` |
| Staging module SHA-256 | `d66113be75cd02dc5353fb2cce7784d0a7e6c7f6597cf89ed805913569a97ffb` |
| Combined execution-code SHA-256 | `6e112712651bbdfdf96848d857a381a6f7d4e6f618f6f4cbd1a1c6cc7aaf42d6` |
| Sorted call-key set SHA-256 | `0b204b937aa53bcb286f555e85817d360ba5288ad23e3ba865191179730debae` |
| Sorted locus-key set SHA-256 | `cfba1fa2f70f6ea7f297fbffa67ac6f76c67e11be23687bc688896a2830b4fcc` |

The committed audit artifact records these hashes with the tool versions and verified input
checksums; any execution-code change therefore produces a distinct deterministic import run.

## Database status

| Item | Current value |
|---|---|
| Database | PostgreSQL 16 with pgvector `0.8.6` installed |
| ORM / migration | SQLAlchemy 2.x / Alembic |
| Domain tables | 43: 32 structured truth tables plus 11 literature tables |
| Baseline | `0001_empty_baseline` |
| Migration head | `0010_m3_lock_hardening` |
| Published structured release | none; the Zhao release remains candidate-only |
| Public locus membership rows | none |
| Published literature corpus | `corpus:endoviho-rag:v0:20260828:001`; 11 documents / 1,464 chunks / 1,464 embeddings / 22 anchors |

Revision 0004 ensures two distinct source-occurrence loci may share one exact interval without
coordinate-based deduplication. Revision 0005 preserves the structured fail-closed publication
boundary. Revision 0006 installs pgvector and the independent literature policy, model, corpus,
document, membership, chunk, embedding, anchor, import-run, import-ledger, and validation-receipt
objects. Revisions 0007–0010 scope anchor identity by release, close child reparenting, freeze all
release-scoped children at validation, and serialize candidate child DML against lifecycle
promotion. Database triggers require a trusted exact receipt for validation and permit only
`validated → published`; corrections require a new release and explicit supersession.

## API, CLI, and deferred scope

The implemented API surface is `GET /health`, `POST /v0/structured/plan`,
`POST /v0/structured/query`, and the M4 `POST /v0/query`. The CLI exposes `structured plan`,
`structured query`, M4 `rag query`, and the M3 `literature manifest-validate`, `corpus-stage`,
`benchmark`, `corpus-validate`, `corpus-publish`, and `retrieve` commands. The M4 endpoint and CLI
share one application service and canonical response serialization. Existing structured and M3
adapters retain their prior envelopes.

`POST /v0/query` and `rag query` expose the four deterministic route families, but this is an
engineering surface rather than real generation activation. Exact structured and literature
gates still apply; hybrid additionally requires an approved exact binding; and production
generation is disabled. M4 adds no Alembic revision, database table, production data row, or
online write path.

Deferred beyond Milestone 4 Draft A at the time of its approval (the final item is now fulfilled
by M5; the activation items remain blocked):

- publication of the Zhao structured candidate and public locus memberships;
- approval of a real dataset/corpus binding manifest and structured-target corpus anchors;
- approval and configuration of a production LLM/provider/prompt policy and any data egress;
- a checksum-bound human semantic-support benchmark and real generation activation;
- complete global Zhao et al. data beyond the ten-assembly pilot;
- the Guinet adapter and additional source adapters;
- streaming, sessions, memory, personalization, live search, agents, and tool loops; and
- M5 demo, evaluation UI, and release packaging — fulfilled locally by the current milestone.

### Next-decision boundary

M4 engineering acceptance and the local PR exit gate are fulfilled. That completion does not
authorize real generation. Publishing Zhao structured facts, approving a real binding/anchor
package, selecting a provider or data-egress policy, or activating semantically reviewed answers
each requires a separate explicit approval or contract amendment. M3 publication does not weaken
the M2 release gate: the structured Zhao pilot remains candidate-only.

### Approved Milestone 3 parameters

| Parameter group | Approved final M3 value |
|---|---|
| Pilot corpus scope | exact approved 11-document English Europe PMC JATS manifest with license/version/checksum provenance |
| Corpus-release identity | exact `corpus:endoviho-rag:v0:YYYYMMDD:NNN`; the application gate authorizes only an exact published release with complete integrity evidence; no automatic dataset binding |
| Accepted input policy | local UTF-8 Markdown, plain text, and safe JATS XML only; no PDF/HTML/OCR/network fetch |
| Chunking policy | BGE tokenizer; section-aware target/overlap/hard-max `384/64/448` content tokens |
| PostgreSQL FTS | explicit `english`, weighted title/section/text vector, GIN, `websearch_to_tsquery`, depth 100 |
| Embeddings | local `BAAI/bge-small-en-v1.5` revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; 384D CLS/L2-normalized cosine |
| pgvector index | install available extension `0.8.6`; HNSW `vector_cosine_ops`, `m=16`, `ef_construction=64`, `ef_search=100`, strict iterative scan |
| Retrieval policy | `retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2`; equal FTS, full-chunk dense, and title/abstract dense branches |
| RRF | equal FTS/full-chunk-dense/title-abstract-dense depths `100/100/100`, `k=60`, deterministic chunk-level tie-breaking |
| Anchors | typed curated locus/assembly/lineage/method/document/keyword anchors; anchored tier then explicit corpus fill |
| Retrieval response | strict `retrieved-chunks-v2`; response-local `D1..Dn`; `top_k` default 8, range 1..20 |
| Benchmark | deterministic fake-provider CI plus pinned-model pilot tier; Recall@5/10 and locator/citation/rebuild gates |

These are engineering decisions, not permission to alter the approved EVE definition or the
Milestone 1/2 structured truth semantics.

## Development environment and tools

| Tool | Recorded version or project choice |
|---|---|
| Active build host | Local Apple Silicon Mac; gds2 is source/archive only |
| Project Python | CPython `3.12.14` |
| Environment/lock manager | `uv 0.12.5` |
| API | FastAPI `0.141.1`; Uvicorn `0.52.4` |
| CLI | Typer `0.27.1` |
| Validation | Pydantic `2.13.4`, pydantic-settings `2.15.0`, database constraints, rebuild audit, trusted receipts |
| Cursor authentication | HMAC-SHA-256; no default secret or bypass |
| Database | PostgreSQL `16.15`; pgvector extension `0.8.6` installed by migration 0006 |
| Database client / ORM | psycopg `3.3.4`; SQLAlchemy `2.0.52`; pgvector Python `0.5.0` |
| Document parsing | markdown-it-py `4.2.0`; defusedxml `0.7.1` |
| Local embedding runtime | optional sentence-transformers `6.0.0`, locked but not installed by the default development sync |
| M4 outer routing | dependency-free deterministic fixed grammar; checksum-bound 30-case benchmark |
| M4 generation provider | dependency-free `LLMProvider` protocol; production selection fixed to `disabled`; deterministic fakes only in tests |
| M4 validation | strict Pydantic round trips, canonical SHA-256, exact citation/span and identifier checks; semantic entailment remains human-reviewed |
| Migration | Alembic `1.19.1`; head `0010_m3_lock_hardening` |
| Tests | pytest `8.4.2`, including PostgreSQL integration tests |
| Lint / static typing | Ruff `0.16.4`; mypy `1.20.2` strict mode |
| Demo client/UI | httpx `0.28.1`; Streamlit `1.62.0` |
| Build backend | Hatchling `1.32.0`, exactly pinned in `pyproject.toml` |
| CI | GitHub Actions on push and pull request |
| Local container runtime | Colima `0.10.3`; Docker client `29.7.2`, engine `29.5.2`; Compose `5.5.0` |

Exact Python dependency versions and dependency-archive hashes remain in `uv.lock`. Project-local
binaries, the virtual environment, caches, Colima state, and large source artifacts are ignored
by Git. The local project wheel/sdist are content-audited but are not published release artifacts.

## Verification commands

```sh
. scripts/local-dev-env.sh
docker compose up -d db
uv sync --locked --dev --extra demo
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run mypy src app
uv lock --check
uv run alembic check
uv run python scripts/check_m5_artifacts.py --check
uv run python scripts/check_docs.py
docker compose config --quiet
uv build
uv run python scripts/check_distribution.py
sh scripts/container_smoke.sh
git diff --check
```

The focused M4 iteration command is:

```sh
uv run pytest \
  tests/planning/test_m4_router.py \
  tests/benchmark/test_m4_router_benchmark.py \
  tests/benchmark/test_m4_generation_benchmark.py \
  tests/hybrid \
  tests/retrieval/hybrid \
  tests/generation \
  tests/application/test_rag_application.py \
  tests/api/test_rag_api.py \
  tests/test_rag_cli.py
```

Focused tests remain an iteration aid rather than a substitute for the final full-suite,
benchmark, clean-migration, lock, Ruff, mypy, package, container, and diff gates recorded above.
GitHub Actions runs artifact/document drift checks, Compose validation, migration upgrade/check,
full pytest, Ruff, strict mypy over `src` and `app`, distribution build/audit, and an isolated
fresh-volume container smoke on pushes and pull requests. This local mechanism-fulfillment record
does not pre-claim a remote-CI result.

Install the separately locked local embedding runtime only on a host holding the approved model
artifact package:

```sh
uv sync --locked --dev --extra local-embeddings
```

M3 production commands require explicit paths and checksums. They do not discover, download, or
silently select a corpus or model:

```text
eve-relation-rag literature manifest-validate
eve-relation-rag literature corpus-stage
eve-relation-rag literature benchmark
eve-relation-rag literature corpus-validate
eve-relation-rag literature corpus-publish
eve-relation-rag literature retrieve
```

CI runs the artifact/document, Compose, migration, complete pytest, Ruff, mypy, distribution, and
fresh-volume container gates described above. Remote check results are reported on the pull
request; branch-protection enforcement remains a separate repository-governance concern.

Final local M3 verification on 2026-08-28 completed with `486 passed`. Ruff, mypy strict (`62`
source files), lockfile reproducibility, `alembic current`, `alembic check`, and diff whitespace
checks passed. The independently rebuilt real corpus and 13-question pinned-model pilot also
passed before receipt creation and explicit publication.

The current setup run also verified all frozen input hashes and replayed the 39,495-row Milestone
1 import idempotently: the existing run, 39,495 source records, and 39,495 import-ledger rows were
reused; no public release membership was created. Both real CLI smoke requests returned the
expected exit code `4`, `release_not_published`, and `fact_retrieval_executed = false`.

An additional whole-tree `ruff format --check src tests` is not a CI or build-guide exit command
and reports 14 legacy files that would be reformatted. Some of those files are part of the frozen
Milestone 1 execution-code hash. They must not be mechanically reformatted inside an unrelated
milestone because doing so would invalidate the recorded audit hash; formatting changes require
an explicitly versioned importer/audit update and regenerated provenance.
