# RAG value ablation: Phase 0 experiment design

## 1. Decision

Build the RAG-value benchmark as an isolated evaluation package that composes the repository's
existing structured and literature services. Do not add a production route, provider choice,
database schema, release, corpus, embedding, or default.

Phase 0 deliberately stopped before implementation. Following explicit approval on 2026-09-02,
the first contract-only implementation tranche was added under
`eve_relation_rag.experiments.rag_value_ablation`. It does not approve a benchmark question,
author gold or oracle evidence, construct a provider, execute a model/retriever/database, or report
a real or synthetic result.

A separately approved question-authoring tranche now adds 64 candidate questions (16 per family)
and blank Gold/Oracle worksheets. All 64 remain `pending`; the authoring audit records zero Gold
and zero Oracle annotations. Grammar acceptance and route classification are software checks only,
not expert approval.

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
4. **Structured protection:** S4/S5 versus S0-S3 on counts, record sets, accessions, coordinates,
   locus identities, and release provenance.
5. **EndoViHo hybrid value:** S5 versus S3 on hybrid questions, holding the generation model fixed.
6. **Grounding and refusal:** change in unsupported claims, required limitations, correct refusal,
   false refusal, unsafe acceptance, and forbidden downstream execution.
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

The future `experiment_manifest.json` should be a strict, frozen, self-checksummed manifest. At
minimum it records:

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

The 60-80 future templates should use stable family-prefixed IDs and contain 15-20 items in each
family. Templates created by Codex remain `pending`; all real gold fields remain empty or null.
Only a human may change `review_status` to `approved` and supply the required gold. The loader must
select only approved questions and must fail if the selected set is empty.

Structured and hybrid template wording should stay inside the current controlled-English grammar
where the question is expected to reach structured retrieval. This reuses the existing planner
rather than adding a second interpretation system
([`planning/parser.py`](../src/eve_relation_rag/planning/parser.py#L605)). Literature templates may
use the current routed literature prefixes; unsupported templates deliberately include requests
that the existing scope policy refuses.

### 5.2 Structured gold

The structured gold variant supports nullable, question-dependent fields:

- exact integer count and metric/deduplication key;
- exact canonical record set;
- assembly accession.version set;
- sequence accession.version set;
- locus-key set;
- exact coordinate tuples `(sequence_accession.version, start0, end0, strand,
  "0-based-half-open")`;
- detection-call key set/count;
- exact release key and release manifest identity; and
- required deterministic limitation codes.

Approved structured gold must be independently derived and reviewed from the approved release. It
must not be copied from a model answer. A benchmark loader may verify the gold against an already
approved immutable release, but it may not turn the current query result into a new human approval.

### 5.3 Literature gold

The literature variant records:

- required document keys;
- required chunk keys grouped into evidence units;
- acceptable alternative chunks per evidence unit;
- excluded or misleading chunks;
- required concepts stated as human-authored rubric items;
- required limitations; and
- forbidden claims.

Evidence groups should reuse the prior ablation semantics: one required chunk and its manually
approved substitutes satisfy one evidence need, without double-counting alternatives
([`experiments/embedding_ablation/contracts.py`](../src/eve_relation_rag/experiments/embedding_ablation/contracts.py#L46)).
Lexical similarity, retriever rank, or model selection never creates an acceptable alternative.

### 5.4 Hybrid gold

The hybrid variant contains both structured and literature gold plus:

- required relationships between exact structured facts and literature evidence;
- required caveats, such as locus count not being integration-event count; and
- forbidden transitions from assembly source taxonomy to ancient host, modern infection,
  prevalence, absence, co-divergence, or independent integration.

The structured portion remains a typed object; it is not embedded as prose in the annotation.

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

The loader fetches only those exact approved values. It must reject pending entries, missing chunks,
release/corpus mismatch, or any oracle entry derived from a current system output. Codex may create
the schema and empty template but must never generate a real oracle label.

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

Production `ContextPack` may be retained as a checksum-bound provenance object for S3/S5 where its
eight-chunk constraints match the answer path. It is not the common provider input because its
contract deliberately fixes only literature/hybrid production routes
([`hybrid/contracts.py`](../src/eve_relation_rag/hybrid/contracts.py#L294)).

### 6.2 Common prompt policy

The future prompt manifest must checksum the exact system instruction, request template, evidence
schema, and answer schema. Its instruction includes, verbatim in substance:

- answer in English;
- use only the provided evidence;
- do not use external knowledge;
- do not invent accessions, locus keys, coordinates, counts, releases, papers, or citations;
- preserve structured values exactly and do not modify the supplied structured object;
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

### 9.4 Refusal

- **Correct refusal rate:** appropriate refusals / approved unsupported questions.
- **False refusal rate:** refusals / approved answerable questions.
- **Unsafe acceptance rate:** non-refusals that accept or execute the prohibited premise / approved
  unsupported questions.
- **Downstream calls after refusal:** count and rate of cases whose prohibited call trace contains a
  stage after refusal.

An abstention is a refusal only when its category/explanation satisfies the gold rubric. Empty or
malformed output is a failure, not a correct refusal.

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

Phase 2 deterministic fake providers and synthetic fixtures must be labeled `test_only` in every
manifest, per-question file, CSV, summary, and report banner. They validate software only. They may
not be merged with real systems or used in a scientific conclusion.

### `trusted`

A run is trusted only when all required questions/gold/oracle items are approved; provider and
model artifacts are allowlisted and checksum-verified; release/corpus gates pass; exact prompt and
runtime identities match; no forbidden fallback/call occurs; pre/post fingerprints match; and all
required result files validate.

Human-dependent metrics can be `pending_human_review` inside an otherwise machine-valid run; they
must not be populated with zeroes or inferred labels.

### `failed`

Any schema mismatch, checksum drift, missing approved input, route fallback, forbidden downstream
call, incomplete output set, provider mismatch, production mutation, or unapproved truncation makes
the run failed. Record a sanitized `FailureRecord`; never substitute another system or omit the
question.

The prior ablation trust gate already makes fake providers test-only and rejects missing approved
questions or source/corpus drift
([`experiments/embedding_ablation/trust.py`](../src/eve_relation_rag/experiments/embedding_ablation/trust.py#L135)).

## 13. Isolation implementation

Use an experiment-specific import boundary:

```text
eve_relation_rag.experiments.rag_value_ablation
```

The production composition root does not import it. The experiment accepts explicit dependency
objects and approved paths/hashes; it does not read production settings implicitly. Real database
connections must verify `transaction_read_only=on`. S0 and S2 constructors receive capability-
limited dependency sets so forbidden providers cannot even be constructed.

Use `.artifacts/rag_value_ablation/<experiment_key>/` for transient provider/runtime files and the
future `benchmark/rag_value_ablation/` for canonical, sanitized outputs. Do not write model weights,
raw restricted documents, credentials, or production embeddings to the result directory. Never
overwrite an existing experiment directory; use create-once atomic publication like the existing
reporter ([`experiments/embedding_ablation/reporting.py`](../src/eve_relation_rag/experiments/embedding_ablation/reporting.py#L32)).

Capture the existing production source guard before and after execution. It already covers all
non-experiment Python modules, the app, migrations, `pyproject.toml`, and `uv.lock`
([`experiments/embedding_ablation/source_guard.py`](../src/eve_relation_rag/experiments/embedding_ablation/source_guard.py#L54)).

## 14. Machine outputs and deterministic reporting

Later approved phases should materialize the required tree:

```text
benchmark/rag_value_ablation/
├── QUESTION_AUTHORING.md
├── candidate_question_audit.json
├── experiment_manifest.json
├── oracle_annotation_schema.json
├── oracle_annotations_template.jsonl
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

Add plot-ready derived CSV files, generated from the same revalidated per-question records:

```text
plot_no_rag_vs_rag.csv
plot_structured_correctness.csv
plot_claim_support.csv
plot_refusal.csv
plot_retrieval_quality.csv
plot_quality_latency.csv
```

`docs/rag_value_ablation.md` is generated only from a complete reloaded machine-result directory.
It never calls a model, retriever, or database and never contains manually copied metrics. A
test-only synthetic report stays under the experiment output with an unmistakable banner; it does
not create the formal documentation result.

## 15. Phase plan and explicit gates

### Phase 1 - contracts and metrics

The first implementation tranche was explicitly approved on 2026-09-02. It now provides:

- strict experiment, question, gold, oracle, evidence, answer, prompt, system, and result contracts;
- approved-only checksum-bound annotation and oracle loaders;
- trusted-set admission requiring 60-80 approved questions and 15-20 per family;
- the canonical S0-S6 dependency/stage graph and common-generation comparison checks;
- exact structured, retrieval, grounding, refusal, efficiency, and agreement metrics;
- deterministic system-blinded review packets, two-reviewer completeness, and human adjudication;
- create-once deterministic machine outputs, plot-ready CSV generation, and revalidation; and
- isolated unit/golden/import-boundary tests.

The question-authoring tranche supplies 64 pending candidates, with 16 in each family. It records
wording provenance, expected routes, expected `QueryPlan` intents, semantic-boundary codes, and a
deterministic duplicate/parser audit. Thirty-two structured clauses (16 structured and 16 hybrid)
are accepted by the current controlled-English parser against synthetic resolver fixtures. The
remaining work is human wording review and real Gold/Oracle annotation without deriving labels
from a model or current retriever.

No provider, database, model, or result execution is part of Phase 1.

### Phase 2 - synthetic harness

Add deterministic fake provider/retriever/structured fixtures and run S0-S6 as `test_only`. Prove
prompt parity, route/call isolation, immutable structured facts, metric arithmetic, failure capture,
and report derivation.

### Phase 3 - real retrieval only

Only with approved local releases/artifacts, run S1-S4 without a real LLM. Verify read-only
fingerprints, S2 FTS parity, S3 current BGE parity, S1 context accounting, and structured exactness.
The Phase 3 manifest carries no generation-provider identity: S1-S3 records use the explicit
`retrieval_only` status, construct no LLM provider, and contain no generated-answer fields. S0,
S5, and S6 remain `not_applicable`; S4 may complete deterministically.

### Phase 4 - real LLM comparison

Blocked until the user explicitly approves provider, exact model/revision/artifacts, common prompt,
credentials and egress policy, maximum cost, release, corpus, and approved questions. Then run every
LLM condition with the same generation identity.

### Phase 5 - human review

Export blinded packets, import two complete independent reviews and adjudication, validate every
binding, and compute support/citation/refusal/agreement metrics. Never fabricate missing review
data.

### Phase 6 - final analysis

Regenerate all tables/CSVs/report from machine results, perform paired error analysis, state one of
the four permitted conclusions, and leave production unchanged.
The Phase 6 manifest retains the exact verified Phase 4 generation identity and requires complete
human review; it does not invoke the provider again.

## 16. Phase 1 acceptance tests

The implemented first-tranche tests prove:

- strict schemas reject extras, coercion, duplicate IDs, noncanonical order, and checksum drift;
- pending templates cannot enter scoring and approved entries require human provenance;
- family-specific gold cannot be mixed or left incomplete when approved;
- oracle entries require separate human approval and cannot reference system output;
- all six LLM system definitions share one generation identity and question checksum;
- S0/S2/S4 forbidden dependencies are absent by construction;
- the S5 final result contains the byte-identical `StructuredResult` supplied upstream;
- Recall/MRR/nDCG match existing ablation golden values;
- exact sets, coordinates, identifiers, refusal denominators, and nearest-rank p50/p95 are correct;
- undefined metrics serialize as null with a reason, never as invented zero;
- fake provider provenance can produce only `test_only`;
- reviewer packets contain no system names and imports bind every claim hash;
- two-reviewer disagreement cannot become adjudicated automatically;
- reporting is deterministic, create-once, and derived only from revalidated machine files; and
- no production source/default/migration is changed.

The question-authoring tests additionally prove:

- the candidate set contains exactly 64 questions and exactly 16 per family;
- all rows remain pending with null approval and Gold fields;
- normalized question text and evaluation focus are unique;
- all 64 questions reach their declared structured, literature, hybrid, or unsupported route;
- all 32 parser-applicable structured clauses yield the declared `QueryPlan` intent with no
  unresolved conditions or unconsumed semantic spans;
- family-specific semantic-boundary allowlists reject scope drift; and
- Oracle rows remain pending and contain no selected facts, chunks, releases, attestation, or
  approval.

No new dependency is required for these contracts, calculations, CSV/JSON generation, or tests.

## 17. Current boundary

The contract/metric/review/reporting tranche and pending question-authoring tranche are
implemented. Human wording approval, real Gold/Oracle labels, the Phase 2 fake execution harness,
local real-data retrieval, real LLM calls, human answer review, and benchmark results remain
unstarted. Fixture entities must be replaced with approved release values and re-parsed before a
candidate can be approved. Proceed to later phases only after their corresponding explicit
approval and inputs are available.
