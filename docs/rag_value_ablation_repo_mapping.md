# RAG value ablation: repository mapping and Phase 1-3 status

## 1. Audit outcome

This document began as a repository map at base commit
`d73a69a0264fa33c2437fb042b25128ee2f07604` and now records the implemented Phase 1 contracts and
metrics, Phase 2 deterministic synthetic harness, and Phase 3 offline preflight. No real model was
loaded or called, no real retrieval was executed, no database/corpus/release/embedding was changed,
no scientific benchmark result was created, and no production setting was changed.

The central finding is that EndoViHo-RAG already has the important scientific and safety
abstractions needed by the benchmark:

- a fail-closed controlled-English planner and fixed structured query compiler;
- immutable, release-bound `StructuredResult` and `QuerySuccess` contracts;
- published and validation-candidate capability gates for structured and literature data;
- PostgreSQL English FTS, current BGE dense retrieval, summary retrieval, and exact RRF;
- exact structured-to-literature anchor resolution and release-pair binding;
- checksum-bound context, provider, mechanical validation, and deterministic rendering;
- an isolated experiment package with approved-only annotations, exact association and retrieval
  metrics, read-only corpus patterns, offline execution, issuer-only trust, canonical reporting, a
  deterministic synthetic runner, and a fail-closed Phase 3 preflight.

The benchmark is therefore implemented as an experiment package that adapts these contracts. It
does not add another production RAG route or loosen any production type.

Two requested status documents are not present in the current tree:
`docs/development_status.md` and `docs/v0_release_checklist.md`. Git history shows that commit
`70120595a0c8eb13b28d895b7162ab35c72dbed3` deleted both during the public-repository cleanup.
They were inspected from their last tracked revisions as historical context only. Neither is a
current canonical status record, so Phase 0 does not recreate or update either file.

## 2. Current end-to-end architecture

The current routed path is:

```text
RagQueryRequest
  -> DeterministicRouter
  -> structured: PublishedReleaseGate -> resolver -> ControlledEnglishPlanner
                 -> StructuredRetrievalService -> StructuredResult -> deterministic answer
  -> literature: PublishedCorpusGate -> BGE query embedding
                 -> PostgreSQL FTS + dense + title/abstract dense -> RRF
                 -> RetrievedChunks -> ContextPack -> one provider call
                 -> mechanical validation -> deterministic answer
  -> hybrid: exact DatasetRelease/CorpusRelease binding
             -> structured preflight and retrieval -> immutable QuerySuccess
             -> exact curated anchor resolution -> current literature retrieval
             -> ContextPack -> one provider call -> mechanical validation
             -> deterministic merge that preserves StructuredResult
  -> unsupported: refusal before downstream construction or execution
```

The routed application is dependency-injected and explicitly promises one route without fallback
([`application/rag.py`](../src/eve_relation_rag/application/rag.py#L112)). Its hybrid route verifies
the release/corpus binding before fact retrieval, resolves anchors after structured success, and
preserves structured output when literature evidence is empty
([`application/rag.py`](../src/eve_relation_rag/application/rag.py#L325),
[`application/rag.py`](../src/eve_relation_rag/application/rag.py#L439)). This is the reference
behavior for S5.

## 3. Structured truth path

### 3.1 Planning

The public structured input is a question plus exact release selector; callers cannot submit SQL,
a `QueryPlan`, or a release capability. `StructuredQueryApplication` authorizes the release,
constructs a release-scoped resolver, invokes the planner, and then performs fixed retrieval
([`application/structured.py`](../src/eve_relation_rag/application/structured.py#L30)). The
pre-fact hook used by hybrid orchestration runs only after authorization, planning, cursor checks,
and semantic checks and before any fact query
([`application/structured.py`](../src/eve_relation_rag/application/structured.py#L85)).

The controlled-English parser accepts six closed intents:

- assembly detail;
- locus detail;
- list loci;
- list assemblies;
- list source taxa;
- one exact aggregate.

The supported aggregate keys include included-locus, contig, assembly, source-taxon, and detection-
call counts ([`planning/query_plans.py`](../src/eve_relation_rag/planning/query_plans.py#L115)).
Plans are strict, frozen, server-authored objects bound to the exact release and original question
([`planning/query_plans.py`](../src/eve_relation_rag/planning/query_plans.py#L207)). The parser uses
explicit `show`, `list`, and `count` grammars and rejects negation, OR, and range comparators rather
than broadening a query ([`planning/parser.py`](../src/eve_relation_rag/planning/parser.py#L80)).

Implication for the benchmark: keep grammar-shaped questions as system-regression fixtures, but do
not rewrite the scientific benchmark into `Show/List/Count` syntax. Natural scientific templates
must instead record their parser, intent, composition, and data gaps until a separately reviewed
typed planning boundary exists. The benchmark must not add a free-form SQL escape hatch or let an
LLM author executable SQL or trusted QueryPlans.

### 3.2 Retrieval and exact results

`StructuredQueryCompiler` maps only the approved plan variants to bound SQLAlchemy statements
([`retrieval/structured/compiler.py`](../src/eve_relation_rag/retrieval/structured/compiler.py#L58)).
The detection-call metric is a distinct count over release-bound call keys
([`retrieval/structured/compiler.py`](../src/eve_relation_rag/retrieval/structured/compiler.py#L635)).
The repository accepts a validated plan, not caller-authored statements, through the narrow
`FactRepository` protocol
([`retrieval/structured/service.py`](../src/eve_relation_rag/retrieval/structured/service.py#L56)).

The existing result graph already carries the fields needed by S4 metrics:

| Required benchmark value | Existing contract |
|---|---|
| assembly accession.version and key | `AssemblySummary` |
| sequence accession.version, coordinates, strand, coordinate convention | `ExactPlacement` |
| represented source species, assembly-source taxon, locus key, and role-qualified viral-lineage projections | `LocusSummary` |
| detection calls supporting one locus | `LocusDetailData.calls` |
| source `HCVR`, `viral_major_taxon`, and `vr_type` assertions | `LocusDetailData.public_assertions` |
| exact aggregate and deduplication identity | `AggregateData` |
| release key, manifest, status, and publication/candidate timestamp | `StructuredReleaseRef` |
| deterministic scientific limitations | `StructuredResult.limitations` |

`ExactPlacement` enforces versioned accessions and 0-based half-open coordinates
([`retrieval/structured/results.py`](../src/eve_relation_rag/retrieval/structured/results.py#L242)).
`LocusDetailData` keeps sorted typed calls and public assertions
([`retrieval/structured/results.py`](../src/eve_relation_rag/retrieval/structured/results.py#L438)).
`StructuredResult` is frozen and requires the limitations implied by its data variant
([`retrieval/structured/results.py`](../src/eve_relation_rag/retrieval/structured/results.py#L785),
[`retrieval/structured/results.py`](../src/eve_relation_rag/retrieval/structured/results.py#L829)).

The new scientific templates require one additional tuple field that these contracts do not
currently define: the approved relation class `Transferred gene` or `Integrated virus`.
`PublicAssertionDetail.assertion_type="vr_type"` preserves a source value; it is not an approved
relation ontology. `Integration`, `Viral contig`, and `HCVR` must not be converted to either
requested class. The source taxon is specifically `assembly_source_taxonomy`, and viral lineages
already retain their role and exact snapshot.

**Reuse verdict:** S4 should use `StructuredQueryApplication`, `QuerySuccess`, and
`StructuredResult` unchanged. S5 should carry the same validated object by reference/value and
must never reconstruct structured facts from prose. An experiment-only association projection may
adapt approved fields around that object after a separate relation contract exists; it must not
weaken or relabel the existing result graph.

## 4. Literature retrieval path

The current literature identity is frozen to:

- PostgreSQL 16 English weighted FTS v2;
- `BAAI/bge-small-en-v1.5` at exact revision
  `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`;
- CLS pooling, L2 normalization, 384 dimensions, cosine similarity, and the approved query prefix;
- full-chunk and title/abstract dense branches;
- branch depth 100 and RRF `k=60`.

The exact constants are source-pinned in
[`literature/contracts.py`](../src/eve_relation_rag/literature/contracts.py#L22). A published corpus
gate replays the policy graph, model identity, artifact checksum, membership counts, and trusted
receipt before issuing a capability
([`literature/gate.py`](../src/eve_relation_rag/literature/gate.py#L90),
[`literature/gate.py`](../src/eve_relation_rag/literature/gate.py#L171)).

`LiteratureRepository.retrieve` uses a PostgreSQL read-only transaction, runs anchored retrieval
first when anchors exist, fills corpus-wide without duplicates, and hydrates checksum-bound chunks
([`retrieval/literature/repository.py`](../src/eve_relation_rag/retrieval/literature/repository.py#L68)).
Its three branches are English FTS, full dense, and title/abstract dense, fused by the current exact
RRF implementation ([`retrieval/literature/repository.py`](../src/eve_relation_rag/retrieval/literature/repository.py#L156),
[`retrieval/literature/fusion.py`](../src/eve_relation_rag/retrieval/literature/fusion.py#L23)).

`LiteratureRetrievalService` verifies that the query provider exactly matches the corpus capability
before embedding and retrieval ([`application/literature.py`](../src/eve_relation_rag/application/literature.py#L26)).
`RetrievedChunks` then fixes the current BGE hybrid retrieval policy, citation order, component
ranks, anchors, and checksums
([`literature/contracts.py`](../src/eve_relation_rag/literature/contracts.py#L422),
[`literature/contracts.py`](../src/eve_relation_rag/literature/contracts.py#L461)).

### S2 seam

The production repository does not expose keyword-only retrieval as a public result condition.
However, the existing embedding ablation already contains a production-equivalent
`PostgresFtsCandidateProvider` that executes the same `websearch_to_tsquery`, `ts_rank_cd(..., 32)`,
tie-break, and depth-100 query in a verified read-only transaction
([`experiments/embedding_ablation/retrieval.py`](../src/eve_relation_rag/experiments/embedding_ablation/retrieval.py#L30)).

**Reuse verdict:** use this FTS provider for S2 and hydrate the returned keys from the frozen corpus
snapshot. Do not construct an embedding provider in S2. Add a parity test against the production
FTS branch; do not widen `RetrievedChunks` or pretend S2 used the hybrid retrieval policy.

### S3 seam

**Reuse verdict:** S3 should call the current `LiteratureRetrievalService` with the current
published/candidate capability and approved local BGE provider. The experiment manifest must record
the actual corpus, model artifact, policy graph, and returned `ContextPack` checksum. A different
retriever is admissible only through a separately frozen experiment system definition; it must not
replace the production baseline.

## 5. Structured-first hybrid path

The exact release-pair registry authorizes only checksum-approved DatasetRelease/CorpusRelease
pairs. Its default implementation refuses every pair when no manifest is configured
([`hybrid/bindings.py`](../src/eve_relation_rag/hybrid/bindings.py#L29),
[`hybrid/bindings.py`](../src/eve_relation_rag/hybrid/bindings.py#L94)).

`StructuredAnchorResolver` derives only locus, assembly, lineage, and method targets from a
round-trip-validated `QuerySuccess`; it then resolves only exact curated anchors inside the same
corpus. It refuses more than 64 targets instead of truncating them
([`retrieval/hybrid/anchors.py`](../src/eve_relation_rag/retrieval/hybrid/anchors.py#L45),
[`retrieval/hybrid/anchors.py`](../src/eve_relation_rag/retrieval/hybrid/anchors.py#L148),
[`retrieval/hybrid/anchors.py`](../src/eve_relation_rag/retrieval/hybrid/anchors.py#L220)).

**Reuse verdict:** S5 should reuse the binding registry, structured application, immutable
`QuerySuccess`, anchor resolver, and current literature service. The experiment runner may adapt
their outputs into a benchmark evidence envelope, but it must keep the structured object intact,
retain unmatched-anchor diagnostics, and preserve the current no-fallback execution order.

## 6. Generation and validation contracts

### 6.1 What can be reused unchanged

The following are reusable without production changes:

- canonical JSON and SHA-256 helpers from `hybrid.contracts`;
- the narrow one-call `LLMProvider` shape for deterministic Phase 2 test doubles;
- `ProviderIdentity` concepts such as exact model/revision, prompt checksum, temperature zero,
  timeout, output-byte limit, and retry count zero;
- the mechanical checks for current-response citations, exact quoted spans, identifier/numeric
  provenance, and forbidden inferences;
- deterministic literature/structured rendering patterns;
- execution flags proving which stages ran.

The provider protocol itself is deliberately small: it exposes one identity and one
`generate(context_json)` call with no tools, streaming, or retries
([`generation/providers.py`](../src/eve_relation_rag/generation/providers.py#L10)). The composer
revalidates context and identity, calls the provider exactly once, validates the returned typed
draft, and renders deterministically
([`generation/composer.py`](../src/eve_relation_rag/generation/composer.py#L66)). Mechanical
validation checks provenance but explicitly does not establish semantic entailment
([`generation/validators.py`](../src/eve_relation_rag/generation/validators.py#L79)).

### 6.2 What cannot be reused as the common S0-S6 contract

`ContextPack` cannot be the common benchmark input unchanged. It allows only `literature` and
`hybrid`, requires a current `RetrievedChunks`, fixes the current query checksum, and caps the pack
at eight chunks and 131,072 UTF-8 bytes
([`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L294)). It therefore cannot
faithfully represent:

- S0 with no evidence;
- S1 with a deterministic raw/long context and explicit truncation record;
- S2 with FTS-only retrieval identity;
- S4 with structured-only deterministic output; or
- S6 with manually approved oracle evidence that was not retrieved.

`GeneratedAnswerDraft` also lacks `answer_text`, abstention, claim type, and general limitation
fields, and it requires citations for every generated claim
([`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L376),
[`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L406)). It is unsuitable as the
shared S0/S1/S2/S3/S5/S6 answer schema without weakening its production meaning.

The concrete `LocalOpenAICompatibleProvider` also cannot be reused unchanged for the common
benchmark prompt. It reparses every input as the approved production `ContextPack`
([`generation/local_provider.py`](../src/eve_relation_rag/generation/local_provider.py#L401)), and
its prompt manifest is explicitly bound to `context-pack-v1` and
`generated-answer-draft-v1` ([`generation/policy.py`](../src/eve_relation_rag/generation/policy.py#L135)).
Its transport, artifact attestation, no-redirect/no-proxy policy, and exact generation settings are
excellent reference behavior, but relaxing its schema guard would change production safety.

**Provider verdict:** the abstract one-call pattern is reusable; the current real provider adapter
is not a generic benchmark provider. Phase 2's experiment-only deterministic fake receives a
checksum-bound complete request containing the exact system instruction, canonical user payload,
generation identity, temperature, and output limits. Phase 4 still needs an explicitly approved
experiment adapter/prompt manifest that preserves the same local artifact and transport
attestations and returns token/latency telemetry. It must not modify or subclass around the checks
of the production provider.

**ContextPack verdict:** preserve it unchanged. An ASCII S3 request or an existing mechanical S5
route may retain a validated production `ContextPack` as provenance. The exact natural Hybrid
templates cannot use it directly, even with one structured result, because its validation binds the
original question to a controlled structured question plus one fixed suffix. All LLM conditions
therefore need a separate strict experiment evidence envelope and a common experiment draft/output
contract; composite scientific questions additionally need a validated multi-plan/multi-result
envelope.

## 7. Existing experiment infrastructure

The embedding ablation is the closest reusable benchmark architecture. It already provides:

| Component | Reusable behavior |
|---|---|
| `contracts.py` | frozen types, self-checksummed manifests, approved/pending/rejected annotations, explicit alternatives/exclusions |
| `annotations.py` | checksum-approved file loading and create-once canonical output |
| `corpus_snapshot.py` | published gate, read-only snapshot, text-free corpus fingerprint |
| `retrieval.py` | production-equivalent FTS and exact retrieval branch assembly |
| `metrics.py` | group-aware Recall@1/3/5/10, MRR@10, nDCG@10, nearest-rank p50/p95 using Decimal |
| `offline.py` | model-call-scoped offline flags and Python socket denial |
| `artifacts.py` | exact local artifact inventory, hash, size, license, and no-symlink verification |
| `source_guard.py` | pre/post hashes of production Python, settings, app files, migrations, `pyproject.toml`, and `uv.lock` |
| `trust.py` | non-forgeable `trusted`, `test_only`, and `failed` decisions; fake providers cannot become trusted |
| `reporting.py` | create-once atomic outputs and Markdown regenerated only from revalidated machine files |

Approved-only admission is enforced in
[`experiments/embedding_ablation/contracts.py`](../src/eve_relation_rag/experiments/embedding_ablation/contracts.py#L71),
and its retrieval metrics use exact evidence groups rather than lexical overlap
([`experiments/embedding_ablation/metrics.py`](../src/eve_relation_rag/experiments/embedding_ablation/metrics.py#L67)).
The source guard recursively includes all non-experiment production modules and all migrations
([`experiments/embedding_ablation/source_guard.py`](../src/eve_relation_rag/experiments/embedding_ablation/source_guard.py#L54)).
The trust gate marks fake providers `test_only` and fails runs without approved questions or with
source/corpus drift ([`experiments/embedding_ablation/trust.py`](../src/eve_relation_rag/experiments/embedding_ablation/trust.py#L135)).
The reporter creates outputs atomically without overwriting and regenerates Markdown from canonical
machine files ([`experiments/embedding_ablation/reporting.py`](../src/eve_relation_rag/experiments/embedding_ablation/reporting.py#L32),
[`experiments/embedding_ablation/reporting.py`](../src/eve_relation_rag/experiments/embedding_ablation/reporting.py#L81)).

The tracked output under [`benchmark/embedding_ablation/`](../benchmark/embedding_ablation/) is
preliminary and records zero approved questions, consistent with the README warning
([`README.md`](../README.md#L99)). It supplies reporting conventions, not gold labels for the new
benchmark.

## 8. S0-S6 component map

| System | Existing components to reuse | Implemented experiment seam / remaining real blocker |
|---|---|---|
| S0 closed-book | provider identity/one-call pattern; common refusal policy | Empty evidence envelope, dependency/stage proof, and fake path implemented; a real common provider remains Phase 4-only. |
| S1 raw/long context | published corpus snapshot; structured result serializers; local model tokenizer identity | Policy/accounting and synthetic segments implemented; approved real materials, tokenizer, truncation policy, and export remain absent. |
| S2 keyword literature RAG | `PostgresFtsCandidateProvider`, published corpus snapshot, chunk identities | FTS-only system policy and fake-rank path implemented; real published-corpus hydration/parity have not run. |
| S3 current literature hybrid | published/candidate corpus gate, local BGE, current service/repository/RRF | Common evidence/answer/telemetry contracts and fake FTS+dense+summary+RRF path implemented; real retrieval has not run. |
| S4 structured retrieval | router/planner/resolver/gate/service/compiler/repository, `StructuredResult`, deterministic renderer | Synthetic structured application and production deterministic renderer execute; approved relation assertions/Gold and a published release are missing. |
| S5 structured-first Hybrid RAG | release/corpus gates, binding, structured app, anchor resolver, current literature service, mechanical validators | Production binding registry, structured target extraction and structured rendering execute over synthetic inputs; persisted-anchor SQL resolution, production `ContextPack`/generation composition, and approved real inputs remain missing. |
| S6 oracle evidence | immutable structured types and literature chunk identities | Strict approved Oracle contracts/loaders bind structured facts exactly to question Gold and chunks to complete human-approved evidence groups while rejecting excluded/arbitrary evidence; a deliberately distinct synthetic test fixture exists, but manually approved real Oracle evidence is absent. |

No existing abstraction is replaced. The implemented layer covers contracts, exact measurement,
synthetic orchestration, and offline readiness gating; the remaining work is approved real-data
adaptation and execution, not a parallel RAG architecture.

### Association-template contract

The 64 pending scientific templates are organized into four answerable tasks with four Structured,
four Literature, and four Hybrid questions each, plus 16 unsupported questions:

| Task | Answerable templates | Primary projection |
|---|---:|---|
| `source_taxon_association` | 12 | source taxonomic scope -> represented/source-reported species -> downstream relation |
| `viral_lineage_association` | 12 | role-qualified viral lineage -> source taxon/species/assembly/locus/class |
| `source_viral_lineage_association` | 12 | one source-lineage x one/two viral-lineage scopes -> relation tuples |
| `assembly_locus_association` | 12 | assembly/locus -> source species/class/viral lineage |

All 48 answerable records have status `requires_relation_contract`; none is marked
`supported_now`. Their output domains remain separate:

- Structured: `exact_association_set` plus applicable exact structured projections;
- Literature: `source_reported_association_set`, required documents/evidence groups, and no
  structured `exact_*` projection. A source-reported host taxon, species, named assembly/region,
  or viral lineage remains `null` when absent from the source, and a normalized viral-lineage
  binding cannot appear without source lineage text; and
- Hybrid: both source-specific sets plus `cross_source_association_set`.

Every answerable row also preserves `required_limitations` and `forbidden_claims`. Removing
how/why/method/limitations questions did not remove the safety rubric needed to detect source-taxon
overinterpretation, relation-label fabrication, lineage-role conflation, or event-count claims.

## 9. Current activation and data blockers

### 9.1 Repository-distributed blockers

The public repository intentionally does not distribute real structured data, full-text papers, or
model weights; a fresh database is empty and unavailable routes refuse
([`README.md`](../README.md#L109)). The tracked `data/` directory contains only small manifests and
audits, not source workbooks or genome/report artifacts
([`data/README.md`](../data/README.md#L1)). Therefore a clean clone cannot run S1-S6 as a trusted
real experiment.

The current semantic document says no public EVE release exists and describes candidate-only
membership ([`data_semantics.md`](data_semantics.md#L9),
[`data_semantics.md`](data_semantics.md#L87)). Before Phase 3, that statement must be reconciled with
the actual selected database through `PublishedReleaseGate`/`PublishedCorpusGate`; documentation or
untracked filenames are not substitutes for gate-issued capabilities.

The requested `Transferred gene` and `Integrated virus` categories are also absent from the
approved structured vocabulary. The currently inspected local candidate cohort contains only the
source `VR Type` label `Integration` and the study-defined lineage `Orthopolintovirales`; it cannot
exercise category or viral-lineage discrimination. This ignored candidate material is neither a
public release nor human Gold. A later run needs an approved relation contract and a frozen,
reviewed dataset/corpus with enough category and role-qualified lineage diversity for the intended
metrics.

### 9.2 Current local-runtime observations

- `.env` is absent, so no experiment-specific release/model paths or approved hashes are selected.
- The 64 scientific authoring records are all `pending`. The trusted question template contains no
  approved question/Gold rows; the real Oracle manifest is absent; the relation contract is a
  pending blank worksheet; the relation-assertion JSONL is empty; and all 11 entity-binding rows are
  pending with no selected key, snapshot, release, scope, or approval.
- Ignored local corpus-manifest/document, BGE artifact, anchor, and binding files exist, and their
  inspected offline file/checksum relationships are internally consistent. This is useful input to
  a later preflight, but it grants no capability: the corpus release is `validated`, not
  `published`.
- The local structured packet identifies
  `release:endoviho-rag:v0:20260826:001` as a candidate and the activation packet itself as
  `candidate_for_owner_approval`. It explicitly does not authorize publication. Its recorded
  structured validator identity is stale against the current validator implementation, no owner
  approval is present, and no separately approved audit proves a strictly read-only experiment
  database role.
- The base shell does not expose `docker`, `psql`, or `pg_isready` directly. The documented
  repository-local environment
  ([`scripts/local-dev-env.sh`](../scripts/local-dev-env.sh#L5)) exposes those tools, and the current
  verification used it explicitly. Tool availability does not establish data/release approval.
- The historical `uv run alembic check` reached a PostgreSQL endpoint through the configured Python
  driver and found no pending schema operations. That proves only schema connectivity for that
  check; it does not prove approved benchmark data or a read-only experiment role.
- `.artifacts/` is excluded from version control ([`.gitignore`](../.gitignore#L7)); local presence
  cannot establish owner approval, publication, current validator identity, benchmark licensing, or
  reproducibility on another clone.
- The production API and example environment still select `EVE_RAG_LLM_PROVIDER=disabled`
  ([`compose.yaml`](../compose.yaml#L60), [`.env.example`](../.env.example#L1)). Current settings do
  support a checksum-gated loopback provider, but activating it requires exact artifacts, prompt and
  model policies, authentication, and release selectors
  ([`config/settings.py`](../src/eve_relation_rag/config/settings.py#L39),
  [`config/settings.py`](../src/eve_relation_rag/config/settings.py#L68)).

### 9.3 Approval blockers for later phases

The following remain unavailable for a trusted RAG-value run:

1. human approval for the 64 pending scientific questions and every release/snapshot-scoped entity
   binding;
2. a versioned relation-class contract and independently approved `Transferred gene`/
   `Integrated virus` assertions or mapping policy, with no inference from `Integration`,
   `Viral contig`, or `HCVR`;
3. enough approved class and role-qualified viral-lineage diversity to make the requested
   comparisons eligible;
4. real structured association Gold, source-reported literature Gold, cross-source alignment Gold,
   limitations, forbidden claims, and refusal labels;
5. a separately human-approved S6 Oracle evidence manifest;
6. a published DatasetRelease and CorpusRelease, a current trusted validation identity/receipt,
   owner approval, and an exact approved release-pair binding;
7. an approved raw-material set and deterministic S1 context policy;
8. an exact LLM provider, model/revision, model artifact checksum, common prompt policy, tokenizer,
   output-token limit, credentials/egress policy, and maximum cost approved specifically for Phase 4;
9. at least two independent EVE/virology reviewers and complete review imports;
10. preregistered decision thresholds for any claim of superiority; and
11. for Phase 3, a separately validated strictly read-only experiment database role. The local
   Docker/Alembic checks prove runtime and schema connectivity but not approved benchmark data or
   role isolation.

The local artifacts observed during this audit do not waive any item above.

### 9.4 Documentation drift to resolve later

[`data_semantics.md`](data_semantics.md#L208) states that `EVE_RAG_LLM_PROVIDER` has only the value
`disabled`. Current settings also allow `local_openai_compatible`, while the shipped defaults remain
disabled ([`config/settings.py`](../src/eve_relation_rag/config/settings.py#L39)). This experiment
does not edit that broader semantic document; source code and shipped defaults remain runtime
authority, and any general documentation correction is a separate change.

## 10. Production isolation boundary

The experiment is confined to:

```text
src/eve_relation_rag/experiments/rag_value_ablation/
tests/experiments/
benchmark/rag_value_ablation/        # tracked empty/pending templates; explicit runtime outputs elsewhere
benchmark/system_regression/         # frozen mechanical software fixtures only
docs/rag_value_ablation*.md
docs/scientific_question*.md
.artifacts/rag_value_ablation/       # ignored runtime scratch, never committed
```

It is not imported by `bootstrap.py`, the API, the normal CLI query command, or Streamlit. It adds
no settings, migrations, database tables, production provider choices, release publication calls,
or corpus ingestion calls.

For real retrieval, use gate-issued capabilities and PostgreSQL read-only transactions. Capture
before/after:

- the existing production source fingerprint;
- DatasetRelease/CorpusRelease manifest and receipt identities;
- corpus document/chunk/anchor fingerprints;
- production embedding row identities/counts;
- selected release membership identities/counts; and
- the Git commit and clean/dirty state.

Any drift makes the run `failed`, not merely a warning. Runtime model files and any temporary index
belong under `.artifacts/rag_value_ablation/<experiment_key>/`. Machine results may contain keys,
checksums, metrics, timings, and approved excerpts only; they must not contain credentials, model
weights, restricted source bytes, or an unapproved full-text export.

## 11. Phase 1-3 implementation map

All implementation remains inside the experiment namespace. The source files are:

```text
src/eve_relation_rag/experiments/rag_value_ablation/
├── __init__.py
├── annotations.py       # approved-only question/gold/oracle loading and template export
├── associations.py      # exact/source-reported/cross-source tuples and empty relation templates
├── contracts.py         # manifests, questions, gold variants, evidence, answers, results
├── human_review.py      # blinded packets/import validation; no labels generated
├── metrics.py           # association/structured, grounding, refusal, retrieval, efficiency metrics
├── preflight.py         # checksum-bound, diagnostic-only Phase 3 readiness evaluation
├── prompting.py         # one frozen prompt/output policy shared by all LLM conditions
├── reporting.py         # create-once reports, exact refusal/efficiency summaries, paired cohorts
├── runner.py            # explicit-output five-case Phase 2 matrix orchestration
├── scientific_questions.py # pending association templates and empty binding worksheet
├── synthetic.py         # deterministic fake provider/ranks/structured/raw/oracle-like fixtures
├── system_regression.py # frozen legacy-question loader plus pure route/parser audit
├── systems.py           # frozen S0-S6 definitions and applicability/call policies
└── trust.py             # issuer-only Phase 2 test-output authority bound to the full run
```

The corresponding tests include:

```text
tests/experiments/test_rag_value_annotations.py
tests/experiments/test_rag_value_associations.py
tests/experiments/test_rag_value_contracts.py
tests/experiments/test_rag_value_human_review.py
tests/experiments/test_rag_value_isolation.py
tests/experiments/test_rag_value_metrics.py
tests/experiments/test_rag_value_phase3_preflight.py
tests/experiments/test_rag_value_prompting.py
tests/experiments/test_rag_value_reporting.py
tests/experiments/test_rag_value_synthetic_runner.py
tests/experiments/test_rag_value_systems.py
tests/experiments/test_scientific_question_templates.py
tests/experiments/test_rag_value_system_regression.py
```

The tracked authoring layer contains:

```text
benchmark/rag_value_ablation/question_schema.json
benchmark/rag_value_ablation/questions_template.jsonl
benchmark/rag_value_ablation/oracle_evidence_template.jsonl
benchmark/rag_value_ablation/human_review_template.csv
benchmark/rag_value_ablation/relation_contract_template.json
benchmark/rag_value_ablation/relation_class_assertions_template.jsonl
```

The question/Gold and Oracle JSONL worksheets are empty. The relation contract is a self-checksummed
pending worksheet with no supplied definitions or
source-label mappings; the assertion JSONL is empty. The regenerated question schema includes the
association records and relation identities. Every authored question remains
`review_status="pending"` until human review, while trusted-set admission still requires 60-80
approved questions with 15-20 in each family.

Trusted admission additionally exact-matches every structured or Hybrid structured-Gold release
key/checksum to the question manifest. Oracle coverage is family-specific: structured facts must be
identical to Gold, literature chunks must come only from manually approved evidence-group members
and cover every group, and unsupported questions must carry an empty approved
`no_supporting_evidence` entry. Each trusted entrypoint requires the exact manifest model type and
canonically round-trips it through nested and self-checksum validation; copied, subclassed,
serialized-shape, and checksum-stale objects are rejected with `AnnotationError`. These checks
grant no approval to the blank worksheets. Human source attestation is required for Oracle entries,
while verifying that the declared provenance reflects an independent manual workflow remains an
external review responsibility.

The scientific authoring and regression resources remain:

```text
benchmark/system_regression/rag_value_route_questions_v1.jsonl
benchmark/rag_value_ablation/scientific_questions_template.jsonl
benchmark/rag_value_ablation/scientific_entity_bindings_template.json
```

The first file preserves the original 64 route-oriented pending questions as software fixtures and
retains SHA-256 `9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34`.
The second contains exactly 64 association templates, all pending and placeholder-based: 48 require
the missing relation contract and 16 are unsupported by design; its SHA-256 is
`4ba8ad0291e57ed6eb6bbdad67cebf1c612f5b7b4bdb65fb8fbd53832c273227`. The third is an empty
checksum-bound binding worksheet. None is approved Gold, Oracle evidence, a benchmark result, or
directly admissible to the trusted question loader.

### 11.1 Phase 2 synthetic execution boundary

`execute_synthetic_harness()` runs five self-checksummed synthetic cases—one structured, one
literature, one Hybrid, and two distinct unsupported routes—against S0-S6 and returns 35
per-question records. It performs exactly 22 deterministic fake
generation calls. The comparable structured and Hybrid cases contribute 12 fairness records, one
for every LLM system and case. All output is `test_only`; the fixture itself is
`synthetic_tests_only`; no human grounding metric is populated.

The HMMER case stops under the shared production scope policy at `request_validation` for every
system, before any dependency. The evidence-insufficient case is not routed from Gold: S0/S6 model
outputs abstain, S1-S3 outputs are scored as unsafe acceptance, and S4/S5 stop under their
structured route policy. Each observation records the refusal origin. The matched refusal cohort
uses identical questions with observations across all six LLM-based systems and does not represent
an early policy refusal as a generation call. The Phase 2 issuer rejects a missing applicable
observation, any applicability-matrix drift, or an origin that differs from replaying the fixed
scope/route policy; a generated model abstention therefore cannot be relabeled as a policy refusal.
It also recomputes appropriate-refusal and unsafe-acceptance flags from the fixture and observed
abstention, and requires a zero post-refusal call count consistent with the fail-closed trace.
S4 has no generation, and S4/S5 are `not_applicable` for the pure literature case. The fake evidence paths
cover empty context, fixed raw segments, FTS-only ranking, fake dense/summary plus the repository's
pure RRF function, an in-memory structured application/repository, immutable structured-result
preservation, and synthetic Oracle-like evidence that is intentionally not a real
`OracleEvidenceEntry`.

Each fake-provider call receives a checksum-bound complete request containing the exact system
instruction, canonical user payload bytes, generation identity, temperature, and output limits.
S4 executes the production deterministic structured renderer. S5 executes the production
release-pair binding registry and structured target extractor, then persists a checksum-bound
test-only deterministic merge. Because the aggregate fixture has no anchor target, no persisted
anchor-store SQL resolution occurs. Production `ContextPack` and `GenerationComposition` also remain
unexercised; the common benchmark envelope and final merge are explicit experiment adapters.

`run_synthetic_benchmark(path)` writes only to the explicit new path. An issuer-only trust decision
binds the entire run checksum, manifest checksum, exact fixture/result projection, and paired
comparison denominator; copied, replaced, serialized, or caller-built decisions have no authority.
The writer revalidates the complete run and authority,
requires explicit test-output permission, writes the result/plot CSVs and
`TEST_ONLY_REPORT.md` atomically, and rejects an existing directory. Synthetic outputs are created
in test temporary directories or other explicit scratch paths and are not committed as scientific
benchmark results. No formal `docs/rag_value_ablation.md` is generated.

### 11.2 Phase 3 diagnostic boundary

`preflight.py` evaluates an explicit self-checksummed input covering question/Gold/entity approval,
relation contract/assertions and diversity, database-role audit, DatasetRelease, CorpusRelease, S1
raw context, retrieval/BGE, anchors, and release-pair binding. It imports no production settings,
opens no database, loads no model, and executes no retrieval. Its S1-S5 readiness report is
diagnostic only and cannot construct or authorize runtime dependencies.

The current local audit is blocked: the 64 scientific questions and 11 entity bindings are pending;
real Gold, Oracle evidence, and relation assertions are empty/absent; the relation contract is
unapproved; the structured release is candidate with stale validator identity and lacks owner plus
read-only-role approval; and the corpus is validated rather than published. Local checksum-valid
corpus/BGE/anchor/binding files do not override those states. Therefore no real Phase 3 retrieval
has run.

These additions require no new dependency and change no production source, default, migration,
release, corpus, embedding, or provider activation.

## 12. Current software verification

The repository-local environment was loaded with `. scripts/local-dev-env.sh`, then the requested
checks were executed from the repository root on 2026-09-03. No check activated a provider,
published a release, built embeddings, or ran a model.

| Exact command | Exit | Exact result summary |
|---|---:|---|
| `uv run pytest` | 0 | `1160 passed, 1 warning in 67.26s (0:01:07)`; the warning is the existing Starlette `httpx` deprecation warning |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run mypy src app` | 0 | `Success: no issues found in 152 source files` |
| `uv lock --check` | 0 | `Resolved 114 packages in 3ms` |
| `uv run alembic check` | 0 | PostgreSQL autogeneration completed with `No new upgrade operations detected.` |
| `uv run python scripts/check_docs.py` | 0 | No output |
| `docker compose config --quiet` | 0 | No output |

The first full test run inside the network sandbox passed 1053 tests and skipped 85 PostgreSQL
integration tests because localhost access was denied. The reported final run was repeated outside
that network sandbox against the repository's isolated local test database and executed all 1160
tests with zero skips. `alembic check` likewise required localhost access; its final run passed.

## 13. Current stop condition

Phase 1 contracts/metrics and Phase 2 deterministic synthetic software validation are implemented.
Phase 3 stops at a diagnostic-only, fail-closed offline preflight. The relation contract/assertions,
association-set Gold, entity bindings, instantiated/approved questions, and real Oracle evidence
remain human-dependent; the local structured release and corpus do not satisfy published/current-
validation/read-only authority requirements. No real retrieval, real generation, human review data,
scientific benchmark result, or production change is included. Proceed only after those blockers
are independently resolved and the corresponding phase is explicitly approved.
