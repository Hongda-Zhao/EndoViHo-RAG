# RAG value ablation: experiment design and Phase 1-3 status

## 1. Decision

The RAG-value benchmark is implemented as an isolated evaluation package that composes the
repository's existing structured and literature contracts. It adds no production route, provider
choice, database schema, release, corpus, embedding, or default.

Phase 1 now supplies the strict contracts, association projections and exact metrics. Phase 2 now
supplies a deterministic fake-provider harness over five synthetic questions and all seven systems.
It emits 35 per-question records only as `test_only`, makes 22 fake generation calls, and records 12
answer-quality fair-comparison inputs. Phase 3 has advanced only to an offline, fail-closed readiness preflight; it
has not constructed a real database/retriever/model dependency or executed real retrieval. Nothing
implemented here approves a scientific question, authors real Gold or Oracle evidence, or produces
a scientific benchmark conclusion.

The companion [`rag_value_ablation_repo_mapping.md`](rag_value_ablation_repo_mapping.md) records
the source-level audit and exact reuse decisions.

## 2. Research questions and estimands

The experiment must estimate seven paired effects over the same approved questions and frozen
inputs:

1. **RAG value:** the change from S0 to each evidence-bearing LLM system in exact correctness,
   support, citation quality, refusal behavior, and efficiency.
2. **Keyword sufficiency:** S2 versus S0, and S2 versus S3, on literature and hybrid questions.
3. **Semantic hybrid value:** S3 versus S2 with identical corpus, chunking, question, prompt, model,
   context limit, and output limit.
4. **Structured protection:** S4/S5 versus S0-S3 on exact association sets, represented source
   species, assemblies, loci, relation classes, role-qualified viral lineages, identifiers,
   coordinates, counts, and release provenance.
5. **EndoViHo hybrid value:** S5 versus S3 on hybrid questions, holding the generation model fixed.
6. **Grounding and refusal:** change in unsupported claims, required safety limitations, correct
   refusal, false refusal, unsafe category mapping, unsafe taxonomy/lineage expansion, and forbidden
   downstream execution.
7. **Generation ceiling:** S6 error with manually approved complete evidence. Residual S6 error is
   generation/interpretation error, not retrieval error.

All comparisons are paired by `question_id`. A system is never credited using questions or gold
annotations unavailable to another system. Missing or failed executions remain explicit failures;
they are not silently removed from a denominator or rerouted.

## 3. Non-negotiable invariants

### 3.1 Fair generation

For every LLM-based condition (S0, S1, S2, S3, S5, S6), freeze one exact:

- provider implementation and provider artifact identity;
- model key, repository identity, exact immutable revision, and local artifact manifest;
- tokenizer identity and revision;
- system-instruction bytes and checksum;
- request-template bytes and checksum;
- output JSON schema and schema checksum;
- temperature `0`, all other sampling parameters, and seed policy;
- maximum output tokens and output bytes;
- timeout, retry count `0`, concurrency `1`, and conversation state policy;
- question text bytes; and
- approved model context limit.

The prompt presented to the model must not contain the system key (`S0`-`S6`) or a human-readable
condition name. The evidence slot is the only intentional prompt-content difference. S4 has no LLM
and is excluded from model-parity assertions.

The production provider already demonstrates exact temperature, sampling, output, timeout, and
retry pinning ([`generation/policy.py`](../src/eve_relation_rag/generation/policy.py#L189)), but its
prompt is bound to the production `ContextPack`. The benchmark must use a separately approved
experiment prompt contract without changing that production policy.

### 3.2 Frozen data

Every trusted run binds one exact:

- DatasetRelease key, manifest SHA-256, dependency graph, receipt, and membership fingerprint;
- CorpusRelease key, manifest SHA-256, policy graph, receipt, document/chunk/anchor fingerprint;
- release-pair binding manifest where S5 is used;
- corpus chunking and parser policies;
- BGE artifact and retrieval policy for S3/S5;
- raw-context source manifest and construction policy for S1;
- question/gold manifest and approved-question projection; and
- oracle-evidence manifest for S6.

Publication status and checksum identity must be established by existing capability gates, not by a
caller-authored Boolean. Candidate data are allowed only in an explicitly validation-only,
checksum-bound experiment manifest; candidate output can never be described as a public result.

### 3.3 No hidden capabilities

During generation:

- no live web search or external network access;
- no SQL, retrieval, tool, function, code-execution, or HMMER capability exposed to the LLM;
- no conversation memory or prior answer;
- no provider-side document store or hidden system context;
- no fallback model, retriever, corpus, release, prompt, or route; and
- no retry that could choose a more favorable answer.

S0 additionally proves that no database, corpus, retriever, embedding provider, or evidence loader
was constructed. S2 proves that no embedding provider, vector branch, or summary-vector branch was
constructed. A call trace is part of every per-question result.

### 3.4 No production mutation

All production PostgreSQL access is capability-scoped and read-only. The run captures pre/post
source, release, corpus, membership, and embedding fingerprints. Any difference fails the run.
Publication, import, embedding rebuild, receipt creation, settings mutation, and activation code are
not dependencies of the runner.

## 4. Experiment identity

`ExperimentManifest` is now a strict, frozen, self-checksummed contract. The Phase 2 builder binds
the complete synthetic fixture identity in addition to the question, Oracle-like test evidence,
source, generation, retrieval, raw-context, runtime, system, and output-policy identities below.
Real phases must fill the applicable release/corpus approval fields rather than reuse synthetic
identities. At minimum the manifest records:

| Group | Required fields |
|---|---|
| experiment | schema version, experiment key, preregistration timestamp, phase, `test_only/trusted/failed`, trust reasons |
| source | Git commit, initial tree cleanliness, production-source fingerprint, `pyproject.toml` and `uv.lock` hashes |
| questions | question manifest hash, gold hash, approved question count, family counts, oracle manifest hash or null |
| structured data | release key/status/manifest, receipt and dependency graph hashes, membership fingerprint |
| literature data | corpus key/status/manifest, receipt/policy graph, document/chunk/anchor counts and fingerprint |
| hybrid | binding manifest hash and exact release pair |
| retrieval | S2 FTS policy, S3/S5 BGE model/revision/artifact, branch depths, RRF key, retrieval depth, generation chunk limit |
| generation | provider, model/revision/artifact, tokenizer, prompt and schema hashes, temperature/sampling, output limits, timeout/retry/concurrency |
| context | model context limit, byte limit, token-count policy, S1 ordering/truncation policy, evidence-pack schema hash |
| runtime | Python/uv/OS/architecture, dependency lock plus installed dependency versions, PostgreSQL/pgvector, CPU/RAM/accelerator/backend/thread settings |
| measurement | warm-up policy, repetitions, order/randomization, clocks, latency stages, memory sampler, cost manifest |
| outputs | expected file set and report generator source hash |

Do not populate an absent value with a plausible default. Use a typed `null` or make the trusted run
ineligible. Cost is `null` when no approved pricing manifest exists; it is never inferred from a
current public price during an offline run.

## 5. Question and gold contracts

### 5.1 Admission model

Use a single strict `EvaluationQuestion` union with:

- `question_schema_version`;
- stable `question_id`;
- `family`: `structured`, `literature`, `hybrid`, or `unsupported`;
- exact English `question_text`;
- optional non-gold authoring notes;
- `review_status`: `pending`, `approved`, or `rejected`;
- human reviewer key and review timestamp, required only when approved;
- one family-specific gold object, required only when approved; and
- a self/checksum binding to the containing annotation manifest.

The trusted manifest must contain 60-80 stable-ID questions with 15-20 items in each family. The
current 64 Codex-authored templates remain `pending`; all real gold fields remain empty or null.
Only a human may change `review_status` to `approved` and supply the required gold. The loader must
select only approved questions and must fail if the selected set is empty. Trusted-set admission
also requires every structured Gold object, including the structured portion of Hybrid Gold, to
match the outer question manifest's DatasetRelease key and manifest checksum exactly.

The frozen scientific authoring set now contains exactly 64 natural templates: 16 structured, 16
literature, 16 Hybrid, and 16 unsupported. Its 48 answerable questions ask only for associations
among taxonomic scope, represented/source-reported species, assembly, locus or named region,
`Transferred gene` versus `Integrated virus`, and a role-qualified viral lineage. They do not ask
for methods, causal explanations, evidence rationales, or interpretation essays.

Natural wording must not be rewritten into the current controlled-English `Show/List/Count`
grammar merely to claim support. The earlier 64 grammar-shaped questions remain a separate,
checksum-frozen system-regression resource. Scientific templates declare their missing typed
capabilities and remain pending until deterministic planning/readiness and human review are
complete.

The current repository has no approved `Transferred gene`/`Integrated virus` relation contract.
`Integration`, `Viral contig`, and `HCVR` are source fields and must not be mapped to those requested
classes. Consequently, all 48 answerable templates have primary status
`requires_relation_contract`; none is `supported_now`.

### 5.2 Structured gold

The structured gold variant supports nullable, question-dependent fields:

- an exact canonical association set whose tuple preserves represented source species, assembly,
  locus, approved relation class, and role/snapshot/scope-qualified viral lineage;
- exact integer count and metric/deduplication key;
- exact canonical record set;
- assembly accession.version set;
- sequence accession.version set;
- locus-key set;
- exact coordinate tuples `(sequence_accession.version, start0, end0, strand,
  "0-based-half-open")`;
- detection-call key set/count;
- exact release key and release manifest identity; and
- required deterministic limitation codes and forbidden claims.

For source-lineage scopes, “species within” means only source species represented through public
membership in the exact selected release. It is not a complete biological descendant inventory.
An assembly-source taxon is not an ancient or modern host assertion.

Approved structured gold must be independently derived and reviewed from the approved release. It
must not be copied from a model answer. A benchmark loader may verify the gold against an already
approved immutable release, but it may not turn the current query result into a new human approval.

### 5.3 Literature gold

The literature variant records:

- a canonical `source_reported_association_set` preserving source wording and provenance for any
  reported host taxon/species, named assembly/region, reported relation class, and viral-lineage
  role/scope; fields absent from the source remain `null` rather than being completed from
  structured truth;
- required document keys;
- required chunk keys grouped into evidence units;
- acceptable alternative chunks per evidence unit;
- excluded or misleading chunks;
- required concepts stated as human-authored rubric items;
- required limitations; and
- forbidden claims.

Literature gold contains no structured `exact_*` association projection. A literature-only system
must not consult DatasetRelease membership, inject an internal locus key, or inherit a structured
relation class. Missing source fields remain missing rather than being filled from structured
truth.

Evidence groups should reuse the prior ablation semantics: one required chunk and its manually
approved substitutes satisfy one evidence need, without double-counting alternatives
([`experiments/embedding_ablation/contracts.py`](../src/eve_relation_rag/experiments/embedding_ablation/contracts.py#L46)).
Lexical similarity, retriever rank, or model selection never creates an acceptable alternative.

### 5.4 Hybrid gold

The hybrid variant contains the unchanged structured `exact_association_set`, the independent
`source_reported_association_set`, and a separately reviewed `cross_source_association_set`, plus:

- required relationships between exact structured facts and source-reported literature evidence;
- explicit structured-only, literature-only, both, unmatched, and ambiguous states;
- required safety limitations, such as source taxa not being ancient/modern host claims and locus
  count not being integration-event count; and
- forbidden transitions from assembly source taxonomy to ancient host, modern infection,
  prevalence, absence, co-divergence, or independent integration.

The structured portion remains a typed object; it is not embedded as prose in the annotation.
Literature wording and labels cannot overwrite it, and cross-source identity cannot be inferred
from lexical similarity.

### 5.5 Unsupported gold

The unsupported variant records:

- `expected_refusal=true`;
- a controlled refusal category;
- prohibited downstream stages;
- a required explanation rubric; and
- forbidden acceptance/claims.

Suggested categories include `insufficient_release_scope`, `unsupported_biological_inference`,
`biological_absence_not_established`, `prevalence_not_established`,
`independent_event_not_established`, `modern_infection_not_established`,
`external_computation_requested`, and `instruction_override_attempt`.

The runner must not transform a refused request into an unfiltered database query. Existing route
errors already carry execution flags and enforce pre-routing no-call invariants
([`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L163)).

### 5.6 Oracle evidence

S6 reads a separate `OracleEvidenceManifest`. Each entry binds:

- one approved `question_id` and question checksum;
- manually approved structured facts, if applicable;
- manually approved literature chunk keys, if applicable;
- an explicit `evidence_supplied` or `no_supporting_evidence` disposition, so unsupported
  questions can carry a reviewed empty oracle without fabricated evidence;
- corpus/release manifest identities;
- human reviewer key, review time, and approval status; and
- entry and manifest checksums.

The loader fetches only those exact approved values. Trusted admission accepts only the exact
manifest model types and canonically round-trips them through all nested and self-checksum
validators, so a copied, subclassed, serialized-shape, or checksum-stale Pydantic object has no
authority. It rejects pending entries, missing chunks, and release/corpus mismatches. The schema has
no field for system-output provenance and requires a human source attestation; confirming that the
attestation reflects a genuinely independent manual workflow remains an external review duty. The
Oracle is a strict projection of that question's human Gold: structured questions require exactly equal
structured facts and no chunks; literature questions require no structured facts and at least one
approved required-or-alternative chunk from every evidence group; Hybrid questions require both;
and unsupported questions require the approved `no_supporting_evidence` disposition with neither
facts nor chunks. Arbitrary and excluded/misleading chunks are rejected even when the entry has a
valid human-approval envelope. Codex may create the schema and empty template but must never
generate a real oracle label.

### 5.7 Implemented association boundary

Phase 1 implements three experiment-only, immutable association records:

- `ExactAssociation` binds assembly-source species, exact assembly accession.version, locus key,
  approved relation class, relation-assertion key/hash/manifest, and role/snapshot/scope-qualified
  viral lineage;
- `SourceReportedAssociation` preserves literature wording and evidence-group provenance without
  importing structured identities. Host taxon, species, named assembly/region, and viral lineage
  are nullable when the source does not report them; at least one host/region descriptor is
  required, and a normalized viral-lineage binding cannot exist without source lineage text; and
- `CrossSourceAssociation` records a human-reviewed `both`, `structured_only`,
  `literature_only`, `unmatched`, or `ambiguous` relationship between the two truth domains.

Structured, literature, and Hybrid Gold carry these sets only with one exact relation-contract and
assertion-manifest identity. Sets must be homogeneous, canonical, and unique; Hybrid alignment must
cover each supplied record exactly once. The exact metric reports set equality, missing/extra
records, and conservative class-, lineage-role-, and lineage-scope-corruption counts. It does not
use fuzzy matching or lexical overlap.

The committed relation-contract worksheet remains `pending`, supplies no definitions or source
label mapping, and explicitly leaves `HCVR`, `Integration`, and `Viral contig` unmapped. The relation
assertion JSONL is empty. These are annotation templates, not scientific assertions.

## 6. Common evidence and answer contracts

### 6.1 `EvaluationEvidencePack`

Do not change production `ContextPack`. Add an experiment-only, strict, immutable,
self-checksummed `EvaluationEvidencePack` with a common shape:

- exact question and question hash;
- optional immutable `QuerySuccess`/`StructuredResult`;
- ordered evidence items with stable citation IDs, document/chunk identity, locators, text hashes,
  and approved text where permitted;
- optional S1 raw-material segments with `structured_export`/`document` source identity and exact
  byte offsets;
- context construction record: approved context limit, input/context token count, byte count,
  truncation flag, omitted files/segments, and policy checksum;
- retrieval observation kept outside model-visible evidence: ranks, scores, anchors, warnings, and
  retrieval policy; and
- pack checksum.

The model-visible serialization must omit `system_key`, condition names, gold annotations, review
labels, retrieval metrics, and oracle status. The same serializer and field order are used in all
LLM conditions; absent evidence is represented canonically by empty collections/nulls.

Production `ContextPack` may be retained as a checksum-bound provenance object for an ASCII S3
request or an existing mechanical S5 route where its eight-chunk and fixed-suffix constraints match
the answer path. It cannot directly represent the new natural Hybrid templates, even when they need
only one structured result, because its validator binds the original question to the controlled
structured question plus a fixed mechanical suffix. It is not the common provider input because
its contract deliberately fixes only literature/hybrid production routes
([`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L294)).

### 6.2 Common prompt policy

Phase 1 implements a checksum-bound common prompt policy over the exact system instruction, request
template, evidence schema, and answer schema. Its instruction includes, verbatim in substance:

- answer in English;
- use only the provided evidence;
- do not use external knowledge;
- do not invent accessions, locus keys, coordinates, counts, releases, papers, or citations;
- preserve structured values exactly and do not modify the supplied structured object;
- preserve assembly-source taxonomy and every viral-lineage role, snapshot, and
  exact-versus-descendant scope;
- do not convert `Integration`, `Viral contig`, `HCVR`, or a literature label into
  `Transferred gene` or `Integrated virus` unless the provided evidence contains an approved
  relation-class assertion;
- cite every literature-derived factual claim;
- state when evidence is insufficient; and
- do not infer modern infection, prevalence, biological absence, co-divergence, or independent
  integration unless explicitly supported and permitted.

S0 receives the same instruction with an empty evidence pack. Because external knowledge is
forbidden, appropriate S0 behavior may be abstention; that is an intended consequence of the
specified closed-book policy, not a hidden prompt difference.

### 6.3 `EvaluationAnswer`

All LLM systems return one common strict schema, and S4 is mechanically adapted to the same final
envelope:

```json
{
  "answer_text": "...",
  "abstained": false,
  "claims": [
    {
      "claim_id": "C1",
      "text": "...",
      "claim_type": "structured_fact",
      "citation_ids": []
    }
  ],
  "limitations": [],
  "cited_chunk_ids": []
}
```

The Phase 1 contract may add typed machine-only fields for exact structured values and provenance,
but it must not change the model-visible shape between systems. Claims use contiguous IDs and are
required to be atomic. Literature claims require a current `D*` chunk or `R*` raw-segment evidence
reference. `cited_chunk_ids` contains only the exact chunks reached through `D*`; raw-segment
references do not masquerade as chunk identities. A structured claim is mechanically admissible
only when a typed structured result or a declared structured-export segment was supplied.

For S5, the original `StructuredResult` is stored separately and copied mechanically into the final
structured section/rendering. The raw provider draft is retained for audit, but it cannot overwrite
that object. Any provider-authored identifier/count/coordinate that conflicts with the immutable
result causes mechanical validation failure. This follows the existing structured-first rendering
boundary, where generated prose is combined with—not substituted for—the structured result
([`application/rag.py`](../src/eve_relation_rag/application/rag.py#L520)).

Mechanical validation remains distinct from human support review. Exact citation/span and token
checks can reject impossible provenance, but they cannot label scientific entailment
([`docs/data_semantics.md`](data_semantics.md#L160)).

## 7. System definitions

All system definitions are frozen data, not branches scattered through the runner.

### S0 - closed-book LLM

Input is the exact question and an empty evidence pack. Dependency construction for database,
corpus, retrieval, embeddings, structured planning, web, or tools is forbidden. The model may
abstain. Any citation or project identifier not present in the empty pack is invented.

### S1 - raw/long context

Build one fixed context from the approved structured export and approved document files. Freeze:

- source manifest and checksums;
- ordering: structured export first, then documents in corpus-manifest order, then source order
  within each document;
- separators and metadata included;
- tokenizer and approved model context limit;
- reserved tokens for system prompt, question, and output;
- segment-level truncation policy; and
- omission reporting.

Never silently exceed the context limit. Prefer whole-segment inclusion. If the policy permits a
final partial segment, record exact source byte and token offsets. `truncated`, `omitted_files`, and
input/context token counts are required even when zero/empty. A reconstruction from database chunks
must be called a normalized chunk export, not a raw document export.

### S2 - keyword literature RAG

Run only PostgreSQL English FTS at the frozen production expression and depth. Hydrate exact chunks
from the frozen corpus snapshot. Do not construct or call an embedding provider, dense index,
summary branch, or RRF across empty dense branches. Final order is FTS rank plus the production
chunk-key tie-break. The evidence projection uses the same generation chunk limit as S3/S5.

### S3 - current literature hybrid retrieval

Use the current approved BGE baseline unless the experiment manifest explicitly selects another
frozen system. Preserve FTS, full dense, title/abstract dense, anchor policy where applicable, RRF
`k=60`, branch depth, and tie-breaks exactly. The current implementation and baseline identity are
defined in [`literature/contracts.py`](../src/eve_relation_rag/literature/contracts.py#L27) and
[`experiments/embedding_ablation/baseline.py`](../src/eve_relation_rag/experiments/embedding_ablation/baseline.py#L14).

### S4 - structured retrieval

Use `StructuredQueryApplication` and return deterministic structured output. No LLM, literature
retrieval, or arbitrary SQL is involved. Score only exact fields represented by the approved gold.
Literature-only requests are not forced through S4; their applicability is recorded as
`not_applicable`, not as a correct answer.

### S5 - EndoViHo structured-first Hybrid RAG

Reuse the current order:

1. exact route and release selectors;
2. structured release authorization, controlled-English plan, and semantic preflight;
3. exact release/corpus binding before fact retrieval;
4. structured retrieval into immutable `QuerySuccess`/`StructuredResult`;
5. exact trusted target extraction and curated anchor resolution;
6. current literature retrieval;
7. common experiment evidence pack and same LLM;
8. mechanical fact/citation validation; and
9. deterministic merge preserving structured facts.

No match, anchor miss, or refusal may trigger a broader query or alternate route. Current hybrid
orchestration already preserves structured output and avoids generation when literature evidence is
empty ([`application/rag.py`](../src/eve_relation_rag/application/rag.py#L487)).

### S6 - oracle evidence plus same LLM

Use only the separately approved oracle manifest. Do not run a retriever to fill missing oracle
evidence. The same prompt, model, limits, serializer, and validator apply. Retrieval metrics are not
reported for S6; evidence coverage is defined as complete only because a human approved the oracle
entry. Residual unsupported/contradictory claims, missed required facts, false refusal, or identifier
corruption measure generation error.

## 8. Retrieval and generation depths

Freeze two distinct values:

- `retrieval_metric_depth = 10`, used for Recall/MRR/nDCG; and
- `generation_context_chunk_limit`, identical across S2/S3/S5 and no greater than the approved
  context policy (the current production `ContextPack` limit is eight).

If the answer path uses eight chunks, retrieval ranks 9-10 remain metric-only and never enter the
prompt. The run must say so explicitly. A separate retrieval probe, if necessary to preserve the
production eight-chunk path, is recorded outside answer latency and must return the same rank prefix
as the answer retrieval. Do not claim Recall@10 from only eight observed ranks.

## 9. Exact metrics

Use Decimal serialization with one documented rounding rule for ratios. Counts and Boolean exact
matches remain integers/Booleans. Macro summaries include only their declared eligible denominator;
failure counts are reported beside every denominator.

### 9.1 Structured correctness

- **Numeric exact match:** `1` only when the typed predicted integer equals gold exactly.
- **Exact record-set accuracy:** `1` only when canonical predicted and gold sets are identical; no
  partial set receives exact-match credit. Also report missing and extra record counts.
- **Exact association-set accuracy:** apply the same all-or-nothing comparison to the complete
  source-species/assembly/locus/relation-class/role-qualified-lineage tuples; additionally report
  missing, extra, class-corrupted, role-corrupted, and scope-corrupted tuple counts.
- **Coordinate exact match:** exact equality of the complete typed coordinate tuple; report missing,
  changed, and invented tuples.
- **Identifier preservation:** fraction of required identifiers reproduced byte-for-byte with
  accession versions intact, plus an all-identifiers-exact Boolean.
- **Release provenance accuracy:** exact release key and, when required, manifest/status identity.
- **Invented identifier count:** unique identifier tokens in the answer absent from permitted
  evidence/structured truth. Do not count formatting-only citation IDs as biological identifiers.

S4 values come directly from typed results. For LLM systems, use the typed output fields and a
mechanical identifier scan; do not infer correctness from semantic similarity.

### 9.2 Retrieval quality

Reuse the existing evidence-group definitions and formulas:

- Recall@1, Recall@3, Recall@5, Recall@10: satisfied evidence groups divided by required groups;
- MRR@10: reciprocal rank of the first chunk satisfying any required group;
- nDCG@10: binary gain once per evidence group, with approved alternatives sharing the group; and
- excluded-hit count at 10 as a diagnostic.

The repository already implements these exact formulas and nearest-rank latency quantiles
([`experiments/embedding_ablation/metrics.py`](../src/eve_relation_rag/experiments/embedding_ablation/metrics.py#L67)).
Phase 1 should reuse or extract that experiment-only math with regression tests, not implement a
different interpretation.

### 9.3 Answer grounding

- **Required-fact coverage:** required gold facts explicitly and correctly present / required facts.
- **Structured-fact preservation:** exact structured facts retained / structured facts supplied.
- **Fully/partially/unsupported/not-assessable claim rates:** human labels / assessable reviewed
  atomic claims; also report raw counts and `not_assessable` separately.
- **Citation precision:** cited claim-passage links judged to support the claim / reviewed cited
  claim-passage links.
- **Citation recall:** required evidence groups cited in supporting claims / required evidence
  groups.
- **Required-limitation coverage:** required limitations present / required limitations.
- **Contradictory claim count:** human-adjudicated claims contradicting approved structured or
  literature gold.

Document correctness, passage correctness, and support are distinct review fields; a correct paper
with the wrong passage is not a supporting citation.

The answerable association questions do not ask reviewers for a methods or limitations essay.
`required_limitations` remains a safety rubric: it tests whether the answer preserves necessary
scope statements and avoids turning release representation into biological completeness or source
labels into approved relation classes.

### 9.4 Refusal

- **Correct refusal rate:** appropriate refusals / approved unsupported questions.
- **False refusal rate:** refusals / approved answerable questions.
- **Unsafe acceptance rate:** non-refusals that accept or execute the prohibited premise / approved
  unsupported questions.
- **Downstream calls after refusal:** count and rate of cases whose prohibited call trace contains a
  stage after refusal.

An abstention is a refusal only when its category/explanation satisfies the gold rubric. Empty or
malformed output is a failure, not a correct refusal.

Execution must never consult `expected_refusal` to decide whether a request proceeds. Each refusal
records its origin as shared scope policy, system route policy, or model abstention. Answer-quality
pairing requires an actual call to all six LLM conditions; refusal uses a separate matched
end-to-end cohort with an observation for the identical question in every LLM-based system, so an
appropriate early policy refusal remains measurable without pretending that generation occurred.

### 9.5 Efficiency

Record stage and end-to-end latency in integer nanoseconds, then compute discrete nearest-rank p50
and p95. Record:

- input, output, and context tokens from the exact model tokenizer;
- provider-reported usage as a cross-check, not the sole source;
- cost per question only from an approved pricing manifest;
- peak process RSS and, where available, accelerator memory;
- context bytes, evidence items, truncation, and omitted files; and
- cold-start/warm-up separately from measured requests.

Do not use BLEU or ROUGE as primary metrics.

## 10. Paired analysis and conclusions

Every plot/table row must include system, question family, eligible count, completed count, failure
count, and trust status. Primary comparisons are paired per approved question:

- S0 versus S2/S3/S5/S6;
- S2 versus S3;
- S3 versus S5 on hybrid questions;
- S0-S3/S6 versus S4/S5 for structured exactness; and
- S6 versus the best non-oracle condition for retrieval-gap versus generation-gap attribution.

The machine summary treats `comparison_eligible_question_ids` plus their complete six-system
`comparison_inputs` as the sole answer-quality/efficiency LLM denominator. Both a completed answer
and a generated, scored abstention are valid paired outcomes; dropping refusals would hide
false-refusal errors. If an efficiency observation is missing for any LLM condition, that question
is removed from the shared efficiency cohort for every LLM condition.

Refusal has a distinct matched end-to-end denominator: a question is included only when all six
LLM-based systems have a refusal observation, whether the outcome came from generation or a
pre-generation policy boundary. This preserves valid early refusal behavior without inventing a
`comparison_input` or provider call. S4 remains outside the matched LLM-system cohort but is present
in the separately labelled per-system operational summaries.

`summary.json` records exact refusal numerator/denominator/value/undefined-reason structures and
nearest-rank efficiency summaries. `plot_refusal.csv` flattens correct-refusal, false-refusal,
unsafe-acceptance, and downstream-after-refusal count/rate fields. The quality-latency CSV includes
both shared answer-quality/efficiency and separately labelled observed p50/p95 latency, token
totals, cost availability, and peak process/accelerator memory.

Do not select a winner using Recall@10 alone. Do not claim statistical or biological significance
from a small pilot. Phase 1 should encode thresholds only after they are explicitly preregistered;
until then the final conclusion category is one of:

- no production change;
- retrieval-only improvement;
- EndoViHo Hybrid RAG advantage; or
- insufficient evidence.

The experiment never changes production settings automatically, whatever the outcome.

## 11. Blinded human review

### 11.1 Export

Generate a reviewer packet from completed machine outputs:

- at least 20-30 real answers and at least 100 atomic claims for the target review;
- opaque `blind_answer_id` and `blind_claim_id` values;
- exact question, answer, claim, cited document identity, cited passage, and approved comparison
  evidence needed by the rubric;
- no system key, condition name, retrieval scores/ranks, latency, provider logs, or file path that
  reveals the system; and
- deterministic shuffled order from a recorded seed, with the unblinding map withheld from
  reviewers.

Claims come from the typed answer contract. Reviewers must be able to flag a non-atomic claim and
split it into checksum-bound child rows; no LLM performs semantic claim splitting for trusted
review.

### 11.2 Labels

For each atomic claim, each reviewer records:

- `fully_supported`, `partially_supported`, `unsupported`, or `not_assessable`;
- cited document correct;
- cited passage correct;
- passage supports the claim;
- overinterpretation present;
- required limitation present at answer level; and
- refusal appropriate at answer level.

Reviewer identity, role, timestamp, packet hash, claim hash, and attestation are mandatory on import.
No reviewer names, labels, signatures, or agreement values are prefilled.

### 11.3 Independence and agreement

Require at least two independent EVE/virology reviewers. Compute raw agreement and four-category
Cohen's kappa only when both complete the same claim set. If expected agreement is one, report
kappa as undefined/null with its reason. With two reviewers, disagreements are not resolved by an
automatic majority; final support metrics remain unavailable until an explicit human adjudication
submission is imported. Report reviewer-specific rates, agreement, disagreement count, and
adjudicated rates separately.

The existing human-review module supplies useful checksum and packet patterns but is fixed to one
ten-case hybrid cohort, one named reviewer, and three labels
([`generation/human_review.py`](../src/eve_relation_rag/generation/human_review.py#L202),
[`generation/human_review.py`](../src/eve_relation_rag/generation/human_review.py#L384)). Reuse its
binding ideas, not its schema unchanged.

## 12. Trust states and failure policy

### `test_only`

Phase 2 deterministic fake providers and synthetic fixtures are labeled `test_only` in every
manifest, per-question file, CSV, summary, and report banner; the fixture itself is additionally
marked `synthetic_tests_only`. They validate software only. They may not be merged with real systems
or used in a scientific conclusion.

Serializable data do not grant publication authority. The only implemented RAG-value output issuer
accepts an exact checksum-valid synthetic fixture and can issue only `test_only` authority. It
revalidates and binds the complete run checksum and manifest checksum, fixed trust reasons, fake
generation identity, exact fixture/result projection, comparison denominator, and canonical S0-S6
definitions. The issued decision is an in-process
identity: serialization, copying, or `dataclasses.replace` cannot transfer its authority. The writer
round-trips the complete `BenchmarkRun`, rechecks the issued decision against that exact run, and
requires an explicit test-output flag before atomically creating a new directory.

### `trusted`

A run is trusted only when all required questions/gold/oracle items are approved; provider and
model artifacts are allowlisted and checksum-verified; release/corpus gates pass; exact prompt and
runtime identities match; no forbidden fallback/call occurs; pre/post fingerprints match; and all
required result files validate.

No issuer for a real `trusted` RAG-value result is implemented in the current branch.

Human-dependent metrics can be `pending_human_review` inside an otherwise machine-valid run; they
must not be populated with zeroes or inferred labels.

### `failed`

Any schema mismatch, checksum drift, missing approved input, route fallback, forbidden downstream
call, incomplete output set, provider mismatch, production mutation, or unapproved truncation makes
the run failed. Record a sanitized `FailureRecord`; never substitute another system or omit the
question.

The current writer deliberately refuses to publish a `failed` run because no runtime authority for
failed-result publication has been designed. This prevents a caller-authored failure manifest from
smuggling unverified metrics into the output tree.

The prior ablation trust gate supplied the pattern for making fake providers test-only and rejecting
missing approved questions or source/corpus drift
([`experiments/embedding_ablation/trust.py`](../src/eve_relation_rag/experiments/embedding_ablation/trust.py#L135)).

## 13. Isolation implementation

Use an experiment-specific import boundary:

```text
eve_relation_rag.experiments.rag_value_ablation
```

The production composition root does not import it. The experiment accepts explicit dependency
objects and approved paths/hashes; it does not read production settings implicitly. Real database
connections must verify `transaction_read_only=on`. The frozen S0-S6 stage graph begins with
`request_validation`. A refusal at that stage requires an empty constructed-dependency ledger and
therefore happens before a loader, database, retriever, provider, or Oracle adapter can be
constructed. Completed and retrieval-only traces must construct their exact declared dependency
set, not merely a permitted subset.

Use `.artifacts/rag_value_ablation/<experiment_key>/` for transient provider/runtime files and
`benchmark/rag_value_ablation/` for canonical, sanitized outputs. Do not write model weights,
raw restricted documents, credentials, or production embeddings to the result directory. Never
overwrite an existing experiment directory; use create-once atomic publication like the existing
reporter ([`experiments/embedding_ablation/reporting.py`](../src/eve_relation_rag/experiments/embedding_ablation/reporting.py#L32)).

Capture the existing production source guard before and after execution. It already covers all
non-experiment Python modules, the app, migrations, `pyproject.toml`, and `uv.lock`
([`experiments/embedding_ablation/source_guard.py`](../src/eve_relation_rag/experiments/embedding_ablation/source_guard.py#L54)).

## 14. Machine outputs and deterministic reporting

The reporter now materializes the required tree for an authority-bearing run:

```text
benchmark/rag_value_ablation/
├── experiment_manifest.json
├── question_schema.json
├── questions_template.jsonl
├── systems/
├── per_question/
├── retrieval_metrics.csv
├── answer_metrics.csv
├── refusal_metrics.csv
├── latency_metrics.csv
├── human_review_template.csv
├── summary.json
└── failures.jsonl
```

The tracked `questions_template.jsonl` is the empty Gold-bearing question-manifest worksheet, and
`oracle_evidence_template.jsonl` is a separate empty S6 annotation worksheet. Neither contains a
row, approval, or inferred label; the latter is authoring material and is not copied into a result
directory.

The authoring layer is separate from those trusted/result artifacts. It now preserves the frozen
route-oriented software fixtures under `benchmark/system_regression/` and stores 64 natural,
pending association templates plus an empty checksum-bound entity-binding worksheet under
`benchmark/rag_value_ablation/`. All answerable rows require a future approved relation contract;
the placeholder templates are not `EvaluationQuestion` records and cannot enter execution or
scoring.

Add plot-ready derived CSV files, generated from the same revalidated per-question records:

```text
plot_no_rag_vs_rag.csv
plot_structured_correctness.csv
plot_claim_support.csv
plot_refusal.csv
plot_retrieval_quality.csv
plot_quality_latency.csv
```

`docs/rag_value_ablation.md` is reserved for a complete, reloaded, trusted and human-reviewed
machine-result directory. It never calls a model, retriever, or database and never contains manually
copied metrics. The Phase 2 harness instead writes `TEST_ONLY_REPORT.md` inside the caller-selected
new output directory with an unmistakable banner; it never creates the formal documentation result.
All result and plot CSVs are derived from revalidated per-question records. Output creation is
atomic and create-once: an existing directory or report path is rejected rather than overwritten.

## 15. Phase plan and explicit gates

### Phase 1 - contracts and metrics

**Status: implemented for software validation.** It provides:

- strict experiment, question, gold, oracle, evidence, answer, prompt, system, and result contracts;
- strict exact, source-reported, and cross-source association records bound to approved
  relation-assertion identities;
- approved-only checksum-bound annotation and oracle loaders;
- trusted-set admission requiring 60-80 approved questions and 15-20 per family;
- the canonical S0-S6 dependency/stage graph, shared pre-dependency request validation, and
  common-generation comparison checks;
- exact association/structured, retrieval, grounding, refusal, efficiency, and agreement metrics;
- deterministic system-blinded review packets, two-reviewer completeness, and human adjudication;
- create-once deterministic machine outputs, plot-ready CSV generation, and revalidation; and
- isolated unit/golden/import-boundary tests.

A separately approved scientific-question redesign now provides exactly 64 pending authoring-only
templates: 16 structured, 16 literature, 16 Hybrid, and 16 unsupported, organized primarily by
four association tasks. Structured templates require `exact_association_set`; literature templates
require `source_reported_association_set` without structured `exact_*` fields; Hybrid templates
retain both plus `cross_source_association_set`. Every answerable template also retains
`required_limitations` and `forbidden_claims`. The earlier 64 route-oriented questions remain
system-regression fixtures, and the entity-binding worksheet remains empty and checksum-bound.
This does not change trusted `EvaluationQuestion` admission rules.

The authoring vocabulary names `Transferred gene` and `Integrated virus`, but the repository has
not approved those relation classes or a mapping from `Integration`, `Viral contig`, or `HCVR`.
The currently inspected candidate cohort also lacks relation-class and viral-lineage diversity.
These are explicit readiness blockers, not labels to infer during question construction.

Phase 1 therefore adds only a checksum-bound pending relation-contract worksheet and an empty
relation-assertion JSONL template. It does not fill a definition, mapping, class assertion, reviewer,
or approval.

The remaining question work is human-dependent:

1. approve a versioned relation-class contract and independently reviewed assertions/mapping;
2. bind placeholders to approved release/corpus-scoped objects, including lineage role and scope;
3. instantiate self-contained question text and complete parser, diversity, pagination, and
   capacity checks;
4. obtain independent scientific wording review; and
5. author and separately approve real Gold and Oracle evidence without deriving labels from a
   model or current retriever.

No real provider, database, model, retriever, or scientific result execution is part of Phase 1.

### Phase 2 - synthetic harness

**Status: implemented and tested.** Five checksum-bound synthetic questions—one structured, one
literature, one Hybrid, one evidence-insufficient unsupported request, and one external-tool policy
request—are evaluated across S0-S6, producing 35 canonical
per-question records. The run is irreversibly `test_only` and uses only in-memory fake generation,
rank, structured-repository, raw-context, and Oracle-like test fixtures. Its Oracle-like fixture is
deliberately not a real `OracleEvidenceEntry` and cannot be admitted as human-approved evidence.

The matrix makes exactly 22 fake generation calls. The HMMER request is rejected by the frozen
production scope policy for all seven systems at `request_validation`, with no dependency
constructed and no generation call. The evidence-insufficient question is admitted without
consulting its expected-refusal label: S0 and S6 abstain from their model-visible evidence, S1-S3
produce measurable unsafe acceptances, and S4/S5 refuse under their structured route policy before
constructing a dependency. S4 never calls a provider; S4 and S5 are explicitly `not_applicable` for
the pure literature fixture.
The paired fairness ledger contains exactly 12 records: the six LLM systems on each of the
structured and Hybrid questions, all bound to the same question text/hash, generation identity,
prompt-policy checksum, temperature zero, limits, and no-tool/no-web/no-memory settings. Every fake
provider invocation receives and records one checksum-bound request containing the exact system
instruction, canonical user payload bytes, full generation identity, temperature, and output
limits. This exercises the common two-message policy rather than merely counting untransported
system-prompt bytes.

Refusal aggregation is intentionally separate from that answer-quality ledger. Its matched
LLM-system cohort contains the structured, Hybrid, and two unsupported questions because all six
LLM-based systems have an end-to-end refusal observation for each; an early S5 route refusal is not
misreported as a model call. Per-system refusal tables also retain all available observations,
including deterministic S4. The Phase 2 issuer requires an observation for every applicable result,
enforces the fixed applicability matrix, and replays the shared scope and structured-route policies
before accepting a recorded refusal origin; deleting a false refusal or relabeling model abstention
as policy refusal therefore invalidates the run. The issuer also recomputes appropriate-refusal and
unsafe-acceptance flags from fixture Gold plus the observed abstention, and requires the
post-refusal call count to agree with the fail-closed trace, so rehashing a result cannot alter those
synthetic metric numerators.

The runner writes only to an explicit caller-supplied new directory. The writer revalidates the
complete run and issuer-only run authority, emits the required machine/plot CSV tree plus a
conspicuous `TEST_ONLY_REPORT.md`, and rejects overwrite. Synthetic structured results exercise the
existing structured application/result contracts. S4 calls the production deterministic structured
renderer. S5 calls the production immutable release-pair binding registry and structured target
extractor, verifies the generated typed projection against the immutable `StructuredResult`, and
persists a checksum-bound deterministic structured-first output. S0-S3 and S6 structured metrics
come only from the model output projection; S6 is never scored from its input evidence.

The aggregate fixture yields no structured anchor targets, so Phase 2 exercises production target
extraction but intentionally performs no persisted anchor-store SQL resolution. It also does not
construct the production `ContextPack`: the common S0-S6 envelope has a different, documented
scope. The S5 final merge is an explicit experiment-only adapter because the common
`EvaluationAnswer` is not the production `GenerationComposition`. Persisted-anchor resolution,
production literature hydration, and production composition remain work for an approved real-data
phase. The current values are software assertions, not scientific benchmark results.

### Phase 3 - real retrieval only

**Status: offline preflight only; real retrieval has not run.** The preflight consumes one explicit,
self-checksummed evidence object covering questions/Gold/bindings, relation contract/assertions and
diversity, database-role audit, DatasetRelease, CorpusRelease, S1 raw context, retrieval/BGE,
anchors, and the release-pair binding. It reads no production setting or path, opens no database,
loads no model, and constructs no retriever. It reports canonical S1-S5 blocker codes.

The preflight report is diagnostic only. It cannot authorize or construct a database, retriever,
model, or other runtime dependency even when every reported check is ready; a later execution gate
must separately revalidate runtime capabilities. Candidate or merely validated releases never
satisfy the diagnostic `published` requirement.

The current local inputs fail readiness: all 64 scientific questions are `pending`; real Gold,
Oracle evidence, relation assertions, and approved entity bindings are absent; the relation contract
is unapproved; the structured release is candidate rather than published and its validation
identity is stale, without owner approval or a separately verified strictly read-only database role;
and the corpus is validated rather than published. Consequently S1-S5 real construction is blocked,
and this phase has executed no real FTS, dense/summary retrieval, RRF, structured query, or raw-context
export.

After those blockers are independently resolved, the intended Phase 3 matrix remains: S1-S3 use
`retrieval_only` with no LLM provider or answer payload, S4 may complete deterministically, and S0,
S5, and S6 are `not_applicable`.

### Phase 4 - real LLM comparison

**Status: blocked and not started.** It requires every Phase 3 data/approval blocker to be cleared
and explicit user approval of provider, exact model/revision/artifacts, common prompt, credentials
and egress policy, maximum cost, release, corpus, and approved questions. Then every LLM condition
must run with the same generation identity.

### Phase 5 - human review

**Status: not started.** Export blinded packets, import two complete independent reviews and
adjudication, validate every binding, and compute support/citation/refusal/agreement metrics. Never
fabricate missing review data.

### Phase 6 - final analysis

**Status: not started.** Regenerate all tables/CSVs/report from machine results, perform paired error
analysis, state one of the four permitted conclusions, and leave production unchanged.
The Phase 6 manifest retains the exact verified Phase 4 generation identity and requires complete
human review; it does not invoke the provider again.

## 16. Phase 1-3 software acceptance tests

The implemented tests prove:

- strict schemas reject extras, coercion, duplicate IDs, noncanonical order, and checksum drift;
- pending templates cannot enter scoring and approved entries require human provenance;
- family-specific gold cannot be mixed or left incomplete when approved;
- association records remain separated by truth domain, bind exact assertion provenance, require
  canonical sets, and distinguish class, viral-lineage-role, and lineage-scope corruption;
- the pending relation worksheet cannot prefill a mapping, class assertion, or approval;
- oracle entries require separate human approval and source attestation, and trusted entrypoints
  canonically revalidate exact model types plus nested/self checksums;
- all six LLM system definitions share one generation identity and question checksum;
- every non-inapplicable trace begins at `request_validation`, and early refusal constructs no
  dependency;
- completed and retrieval-only traces require the exact declared dependency set, while S0/S2/S4
  forbidden dependencies are absent by construction;
- the S5 final result contains the byte-identical `StructuredResult` supplied upstream;
- Recall/MRR/nDCG match existing ablation golden values;
- exact sets, coordinates, identifiers, refusal denominators, and nearest-rank p50/p95 are correct;
- undefined metrics serialize as null with a reason, never as invented zero;
- five synthetic cases produce 35 S0-S6 records, 22 complete fake-provider requests, and 12 paired
  answer-quality records; unsupported execution is independent of expected-refusal Gold, every
  applicable result must retain its refusal observation, and matched end-to-end refusal binds both
  policy and model-abstention origins to the executed path while recomputing the Phase 2 refusal
  outcome flags and rejecting post-refusal calls; production binding, structured
  rendering, and target extraction execute, while
  persisted-anchor SQL resolution and production `ContextPack` remain explicitly unexercised;
- fake provider provenance can produce only issuer-authorized `test_only`, and copied/replaced
  decisions have no authority;
- reviewer packets contain no system names and imports bind every claim hash;
- two-reviewer disagreement cannot become adjudicated automatically;
- reporting round-trips and revalidates the complete run, is deterministic and create-once, and
  derives every CSV/report from machine files;
- Phase 3 diagnostics report candidate/validated releases, missing approvals, checksum mismatch,
  incomplete diversity, and a non-read-only database role without exposing dependency construction;
  and
- no production source/default/migration is changed.

No new dependency is required for these contracts, calculations, CSV/JSON generation, or tests.

## 17. Current boundary

Phase 1 contracts/metrics and the Phase 2 deterministic synthetic harness are implemented. Phase 3
contains only the offline, fail-closed preflight and is currently blocked. The 64 scientific
questions, relation contract/assertions, entity bindings, real Gold, and real Oracle evidence still
require human approval; the local structured release and corpus are not published, and the
structured validation/read-only authority is incomplete. No real retrieval, real LLM call, human
label import, scientific benchmark result, or production recommendation has been made. Stop here
until those inputs are supplied and the next phase is explicitly authorized.
