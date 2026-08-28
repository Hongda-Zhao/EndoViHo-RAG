# Milestone 4 hybrid RAG and generation contract — Approved Draft A; mechanism FULFILLED

> Status: **APPROVED — ENGINEERING MECHANISM FULFILLED**
>
> Product version: V0
>
> Project: EndoViHo-RAG
>
> Draft date: 2026-08-28 (Asia/Tokyo)
>
> Approval date: 2026-08-28 (Asia/Tokyo)
>
> Mechanism fulfillment date: 2026-08-28 (Asia/Tokyo)
>
> Activation status: **BLOCKED — SEPARATE APPROVALS REQUIRED**
>
> Scope: deterministic routing, hybrid orchestration, immutable `ContextPack`, an abstract
> `LLMProvider`, constrained answer composition, fact/citation validation, and typed routed
> answers

## 1. Purpose and authority

This document is the approved and locally fulfilled engineering-mechanism contract for Milestone
4. The user explicitly approved Draft A on 2026-08-28 and authorized implementation, debugging,
verification, and a pull request after the milestone exit gates passed. Section 14 records those
final local gates. Pull-request and remote-CI state are tracked separately and do not change the
activation boundary.

Instruction precedence remains:

1. explicit user decisions in the project discussion;
2. `docs/data_semantics.md` and the approved Milestone 1 contract;
3. the approved Milestone 2 contract;
4. the fulfilled Milestone 3 contract;
5. `EVE_RELATION_RAG_V0_AGENT_BUILD_GUIDE.md`;
6. older source guides as background only.

Milestone 4 does not alter the EVE definition, publish the Zhao candidate structured release,
change a structured fact, mutate the published M3 corpus, or treat generated text as scientific
truth. PostgreSQL remains the only structured truth source. Literature remains explanatory
evidence. Generated answers are a presentation layer over immutable upstream results.

## 2. M4-D01 — approved target and completion classification

**Proposed decision:** Milestone 4 adds the following server-owned path:

```text
strict English request
    -> deterministic router
    -> exact release gate(s)
    -> validated M2 QueryPlan when structured facts are required
    -> unchanged StructuredResult
    -> exact curated-anchor resolution in the requested corpus
    -> M3 RetrievedChunks
    -> immutable ContextPack
    -> pinned LLMProvider invocation
    -> strict draft parsing
    -> fact, identifier, evidence-span, and citation validation
    -> deterministic English answer rendering
    -> typed routed response
```

The four routes are `structured`, `literature`, `hybrid`, and `unsupported`. The outer M4 route
does not modify the frozen M2 `StructuredPlan.route == "structured"` field or any existing plan,
cursor, result, corpus, citation, or receipt hash.

Draft A distinguishes two states:

- **M4 mechanism complete:** code, API/CLI, deterministic synthetic end-to-end tests, mechanical
  hybrid benchmark, real M3 literature retrieval regression, and real Zhao hybrid fail-closed
  smoke tests all pass. **FULFILLED locally on 2026-08-28; see Section 14.**
- **real hybrid activation:** one exact structured release is published, one exact
  dataset/corpus binding manifest is separately approved, suitable curated structured-target
  anchors are published, one production LLM provider/policy is separately approved, and a
  checksum-bound human claim-support review passes.

Draft A authorizes only the first state. It must not be described as real provider activation or
real Zhao hybrid success. Real activation is a later explicit approval and is not required for the
M4 engineering pull request.

Current activation blockers are recorded, not bypassed:

- the Zhao structured release is candidate-only and the structured gate has no public success
  path;
- no approved `DatasetRelease` to `CorpusRelease` binding exists;
- the published v2 corpus has document and keyword anchors, but no locus, assembly, lineage, or
  method anchor that can be derived from a `StructuredResult`;
- no production LLM provider, model revision, prompt policy, or data-egress permission is
  approved.

## 3. M4-D02 — public request and deterministic router

**Proposed decision:** add one strict request:

```json
{
  "request_schema_version": "rag-query-request-v1",
  "release_key": "release:endoviho-rag:v0:YYYYMMDD:NNN or null",
  "corpus_release_key": "corpus:endoviho-rag:v0:YYYYMMDD:NNN or null",
  "question": "one ASCII English line",
  "page": null,
  "literature_top_k": null
}
```

Rules:

- Unknown fields are rejected. Models are strict, frozen, and `extra=forbid`.
- `question` is 1–2,000 characters, contains printable ASCII plus ordinary spaces, has no control
  character, and is preserved byte-for-byte in the response and `ContextPack`.
- Exact release keys are required. `latest`, aliases, defaults, and inferred releases are
  forbidden.
- Clients cannot submit `route`, `QueryPlan`, `StructuredResult`, `ContextPack`, SQL, anchors,
  provider/model/prompt/sampling settings, or citation IDs.
- `page` reuses the frozen M2 `PageSpec` and is accepted only on structured and hybrid routes.
- `literature_top_k` is accepted only on literature and hybrid routes; it defaults to 8 and is
  restricted to 1–8 for generated answers. Direct M3 retrieval retains its approved 1–20 range.

Routing is pure, deterministic, side-effect free, and runs before any release gate, database,
embedding provider, or LLM provider is constructed. It never decides scientific truth.

Approved grammar families proposed by Draft A are:

1. **Structured:** an unchanged M2 controlled-English `show`, `list`, or `count` question with a
   non-null `release_key`, a null `corpus_release_key`, and no literature suffix.
2. **Literature:** one of the case-insensitive prefixes `Explain the literature evidence for `,
   `Explain the literature methods for `, or `Explain the literature limitations for `, followed
   by a non-empty topic; requires only `corpus_release_key`.
3. **Hybrid:** an unchanged M2 controlled-English clause followed by exactly one of
   ` and explain the literature evidence`, ` and explain the literature methods`, or
   ` and explain the literature limitations`; requires both release keys. The suffix is removed
   before the unchanged M2 planner is invoked, while the original full question remains the
   literature query and answer question.
4. **Unsupported:** anything else, including a route/release-field mismatch.

Before route selection, the router rejects requests for prevalence, biological absence,
infection inference, co-divergence, independent integration events, host-lineage comparison,
new EVE detection, sequence upload, BLAST/HMMER/Foldseek, phylogenetic placement, live web
search, arbitrary SQL, text-to-SQL, multilingual output, or multi-turn memory. Negation, `OR`,
ranges, and comparisons remain subject to the unchanged M2 fail-closed audit when a structured
clause is present.

An unsupported request performs no release authorization, retrieval, embedding, or generation.
There is no fallback from an invalid structured/hybrid question to corpus-wide literature search.

## 4. M4-D03 — exact dual-release binding

**Proposed decision:** a hybrid request is authorized only when all of the following are true:

1. the exact `release_key` is authorized by the unchanged M2 publication gate;
2. the exact `corpus_release_key` is authorized by the unchanged M3 publication gate;
3. the exact pair and both immutable manifest identities occur in a server-owned approved binding
   manifest.

The binding manifest is a strict local artifact, not a client input and not an inferred database
relationship:

```json
{
  "binding_schema_version": "hybrid-release-binding-manifest-v1",
  "bindings": [
    {
      "release_key": "release:endoviho-rag:v0:YYYYMMDD:NNN",
      "release_manifest_sha256": "64 lowercase hex",
      "corpus_release_key": "corpus:endoviho-rag:v0:YYYYMMDD:NNN",
      "corpus_manifest_sha256": "64 lowercase hex"
    }
  ],
  "manifest_sha256": "canonical self-excluding SHA-256"
}
```

Production configuration requires both a local manifest path and an independently approved
manifest SHA-256. Missing, unapproved, malformed, duplicate, cross-manifest, or unmatched pairs
return `hybrid_binding_unavailable` before fact retrieval. No real binding row or manifest is
approved by Draft A. Tests inject a tests-only binding provider. Draft A adds no database table.

## 5. M4-D04 — fixed route orchestration

**Proposed decision:** route calls are fixed and are verified with recording spies.

| Route | Structured path | Literature path | Generation path |
|---|---|---|---|
| structured | unchanged M2 gate, plan, and query once | never | never; deterministic M2 renderer only |
| literature | never | exact M3 corpus gate and retrieval once | once only when chunks exist |
| hybrid | pair preflight, then unchanged M2 query once | derive/resolve anchors, then exact M3 retrieval once | once only when chunks exist |
| unsupported | never | never | never |

For hybrid, the required order is:

```text
route
-> binding manifest syntax/pair preflight
-> published structured authorization and validated QueryPlan
-> published corpus authorization and manifest match
-> StructuredResult
-> trusted anchor target extraction and corpus-scoped resolution
-> RetrievedChunks
-> ContextPack
-> LLMProvider
-> validators
-> HybridAnswer
```

An upstream failure stops every downstream call. Hybrid never degrades to literature-only, uses a
different release, or returns an unvalidated generated partial answer.

When literature retrieval returns no chunks:

- literature returns typed `insufficient_evidence`, with retrieval executed and generation not
  executed;
- hybrid returns the unchanged structured result plus a deterministic explicit
  corpus-insufficient limitation, zero literature claims, and generation not executed.

Provider, parsing, or validator failure rejects the entire generated answer. Claims are not
silently deleted or auto-repaired, and there is no automatic retry.

## 6. M4-D05 — trusted anchor derivation and resolution

**Proposed decision:** clients, question text, and the LLM cannot create anchors. M4 first
round-trip revalidates the returned `QuerySuccess`, then extracts only these trusted target types:

- `locus_key` from returned locus objects or a validated locus filter;
- `assembly_key` from returned assembly/locus objects or a validated assembly filter;
- exact `(snapshot_key, term_key)` from returned lineage references or validated lineage filters;
- `method_definition_key` only from typed public assertion detail, never from a detection call's
  `source_method_key`;
- no document or keyword target is inferred from a structured result.

Targets are deduplicated and ordered by `locus`, `assembly`, `lineage`, then `method`, with lexical
ordering inside each type. M4 never computes a `DocumentAnchor.anchor_key`; it queries the exact
published corpus for existing curated anchors, reconstructs the full stored preimage, validates
its SHA-256, and returns the actual typed M3 `RetrievalAnchor` objects in canonical order.

Unmatched targets produce `structured_anchor_unmatched` and then use M3's explicit same-corpus
fill behavior. An aggregate or entire-release result with no exact target performs unanchored
retrieval in the same exact corpus and records the same diagnostic. More than 64 distinct targets
returns `anchor_limit_exceeded`; targets are never silently truncated. The resolver cannot cross
corpus releases or use document/keyword similarity as a substitute for an exact structured
target.

## 7. M4-D06 — immutable ContextPack

**Proposed decision:** `ContextPack` is the only factual payload passed to an LLM. It contains
exactly:

```text
context_schema_version = context-pack-v1
route = literature | hybrid
original_question
validated QueryPlan or null
unchanged StructuredResult or null
RetrievedChunks
fixed AnswerInstructions
context_sha256
```

It cannot contain an engine/session, SQL, a capability token, settings, secret, API credential,
stack trace, embedding vector, hidden document, external search result, conversation history, or
provider-selected background knowledge.

`AnswerInstructions` is a strict versioned object whose canonical JSON and source-text SHA-256
are pinned in code. It repeats the English-only, exact-fact, citation, insufficient-evidence,
forbidden-inference, and no-external-knowledge rules. The context hash is SHA-256 over canonical
JSON excluding only its own hash field.

The fulfilled implementation pins policy key
`answer:endoviho-rag:v0:grounded-document-claims-v1`, source-text SHA-256
`7f30766995041305f47c8ef867103af42d3f2394fc72eef37f3e42a2ad3f7684`, and canonical
`AnswerInstructions` SHA-256
`4e906e96688e67956017ee7935952d9aedb2926e087f15bae050a343a58be8c1` as independent
literals and rejects source or object drift at import time.

The canonical UTF-8 context is limited to 131,072 bytes, at most 8 chunks, and the existing M2
page maximum. Oversize context returns `context_too_large`. Structured facts and chunks are never
silently truncated or summarized before hashing. Every trust boundary performs JSON round-trip
revalidation to prevent unchecked Pydantic `model_copy` updates from bypassing validators.

## 8. M4-D07 — LLM provider and generation policy

**Proposed decision:** production source code defines a dependency-free, runtime-checkable
`LLMProvider` protocol. It receives exactly one canonical `ContextPack` JSON value and returns one
UTF-8 JSON draft. The public request cannot select provider behavior.

Every provider advertises a strict identity:

```text
provider_key
model_key
model_revision
provider_artifact_sha256 or null
generation_policy_key
prompt_policy_key
prompt_policy_sha256
temperature = 0
max_output_bytes = 32768
timeout_seconds
retry_count = 0
```

The composer verifies that the runtime identity equals the server-pinned identity before invoking
the provider. Output is limited to 32,768 UTF-8 bytes and parsed with the strict draft schema.
Malformed JSON, extra fields, identity mismatch, timeout, exception, or oversize output returns a
sanitized error without exposing the prompt, raw output, credential, stack trace, or internal
exception.

Draft A approves no remote provider, SDK, API key, model revision, data egress, or production
generation policy. Production generation therefore returns `llm_provider_unavailable` until a
separate amendment approves and pins them. `/health`, unsupported requests, and existing M2/M3
operations must not construct an LLM provider. Tests use only a deterministic fake in
`tests/support`; the fake cannot be selected through settings, HTTP, or CLI.

No LangChain, LlamaIndex, agent tool loop, SQL generation, function calling, live search,
conversation memory, streaming, or provider retry is added.

## 9. M4-D08 — constrained draft and deterministic answer

**Proposed decision:** the LLM never returns or rewrites a `StructuredResult`. A strict
`generated-answer-draft-v1` contains only:

```text
draft_schema_version
context_sha256
ordered atomic literature claims
selected approved limitation codes
```

Each literature claim contains:

```text
claim_id = C1..Cn, continuous in response order
claim_text = one non-empty English sentence, maximum 1,000 characters
citation_ids = 1..4 unique current-response D identifiers
evidence_spans = one exact non-empty contiguous quote per cited chunk, maximum 500 characters each
```

There are at most 16 claims. The provider cannot provide structured counts, coordinates, status,
release metadata, citation metadata, or final answer layout as separate authoritative fields.

The final answer is built deterministically by application code:

1. a structured section rendered directly from the unchanged `StructuredResult`, when present;
2. a literature section containing only validated claims and response-local `[D#]` markers;
3. a limitations section containing required upstream limitations plus validated approved
   generated limitations;
4. a citation section rendered directly from `RetrievedChunks` with document key, title,
   identifier, section, locator, and chunk checksum.

Literature-only answers omit the structured section. Hybrid answers embed the original typed
`QuerySuccess`/`StructuredResult` as data as well as the deterministic text. Generated prose is
not persisted in M4.

## 10. M4-D09 — mechanical validators and semantic-review boundary

**Proposed decision:** validation is all-or-nothing and includes at least:

- round-trip equality of the final and original structured plan/result;
- exact context hash and provider/prompt identity;
- continuous unique claim IDs and current-response citation IDs;
- at least one citation for every literature claim;
- exact evidence-span membership in each cited chunk;
- matching document key, section, locator, checksum, and corpus release;
- no citation ID copied from another response;
- no new assembly, locus, lineage, dataset/corpus release, DOI, PMID, or PMCID token absent from
  the `ContextPack`;
- no forbidden inference or unsupported-topic phrase in generated claims;
- required upstream limitations remain present;
- English/ASCII and all size/count limits.

The validator reports `validation_scope = "mechanical"`. Citation existence and an exact evidence
span do not prove scientific entailment. Draft A therefore does not claim automatic semantic
support verification. Activation requires a checksum-bound human benchmark that labels every
claim `supported`, `partially_supported`, or `unsupported`; any `unsupported` factual claim blocks
activation. `partially_supported` claims are not accepted unless the claim is narrowed and the
reviewed output is regenerated under a new checksum.

## 11. M4-D10 — response and error contracts

**Proposed decision:** `POST /v0/query` returns one strict discriminated union:

- `structured-route-answer-v1`: route, original request, unchanged M2 `QuerySuccess`, canonical
  structured text, and execution flags;
- `literature-answer-v1`: exact corpus provenance, `RetrievedChunks`, context/provider/prompt
  identities, validated claims, citations, deterministic text, hashes, validation scope, and
  execution flags;
- `hybrid-answer-v1`: exact dataset and corpus provenance, unchanged M2 `QuerySuccess`,
  `RetrievedChunks`, anchor diagnostics, context/provider/prompt identities, validated claims,
  citations, deterministic text, hashes, validation scope, and execution flags;
- `rag-error-v1`: stable code/message, requested exact keys when syntactically valid, optional
  sanitized upstream code, and execution flags.

Execution flags are separate booleans for `structured_retrieval_executed`,
`literature_retrieval_executed`, and `generation_executed`; they describe actual calls, not the
requested route.

M4 error codes are:

```text
request_schema_invalid
unsupported_request
route_request_mismatch
structured_refused
literature_refused
hybrid_binding_unavailable
anchor_integrity_error
anchor_limit_exceeded
insufficient_evidence
context_integrity_error
context_too_large
llm_provider_unavailable
generation_failed
generated_draft_invalid
answer_validation_failed
internal_error
```

Upstream M2/M3 stable error codes are exposed only as `upstream_code` inside
`structured_refused`/`literature_refused`; internal exception details are never exposed. Schema,
unsupported, mismatch, unpublished/unbound, and validation refusals map to deterministic 4xx
statuses; unavailable configured dependencies map to 503; unexpected faults map to sanitized 500.
CLI uses stdout/exit 0 for success and canonical stderr/nonzero exits for errors.

## 12. M4-D11 — API, CLI, compatibility, and composition

**Proposed decision:** add:

- `POST /v0/query` for the routed contract;
- `eve-relation-rag rag query --question ... [--release-key ...]
  [--corpus-release-key ...] [--limit ...] [--cursor ...] [--literature-top-k ...]`.

Existing `/health`, `/v0/structured/plan`, `/v0/structured/query`, structured CLI commands, and
M3 operator/developer literature commands remain byte-contract compatible. M4 HTTP and CLI call
the same application service and produce canonical-equivalent JSON.

Composition is lazy by route. A structured or unsupported request cannot fail because embedding
or LLM configuration is absent. A literature request cannot construct the structured repository.
FastAPI validation and CLI option failures use the M4 envelope only for the M4 endpoint/command;
existing adapters retain their current envelopes.

No Alembic migration is proposed for Draft A. No production data, release, corpus, anchor,
receipt, answer, prompt, or provider row is written by the M4 online path.

## 13. M4-D12 — test and benchmark contract

**Proposed decision:** tests are written or synchronized before implementation and include:

1. strict schema, canonical hash, JSON round-trip, immutability, ordering, and tamper tests;
2. table-driven four-route grammar and zero-side-effect unsupported tests;
3. exact binding, gate ordering, route call matrix, and execution-flag tests;
4. all six structured result variants, filters, target extraction, exact corpus anchor resolution,
   unmatched anchors, canonical ordering, and the 64-target refusal;
5. ContextPack allowlist, cross-release/query rejection, checksum tamper, and byte-limit tests;
6. deterministic fake provider valid/malformed/extra/timeout/error/oversize/identity cases;
7. citation, evidence-span, identifier, structured equality, forbidden inference, and all-or-none
   validator tests;
8. API/CLI canonical parity and existing M2/M3 adapter regressions;
9. PostgreSQL synthetic published-corpus integration with an anchor-rich test fixture;
10. a real smoke test proving the Zhao release plus the real published M3 corpus fails before
    structured facts, literature retrieval, or LLM invocation.

The frozen M4 benchmark contains at least:

- 5 structured route cases;
- 5 literature route cases;
- 10 hybrid cases across the M2 result variants and anchor modes;
- 5 unsupported/adversarial cases.

The exact fixture manifest and checksum are committed before implementation results are accepted.
CI uses only deterministic local fakes and no provider credential or external network. The M4
mechanical gates are all hard 100% requirements:

```text
structured numbers and identifiers unchanged = 100%
document-derived claims with valid current citations = 100%
exact evidence-span presence = 100%
invented record identifiers = 0
unsupported-request refusal = 100%
route call-order and zero-side-effect invariants = 100%
```

The existing complete M2 benchmark and M3 deterministic retrieval benchmark must not regress.
The M3 13-question retrieval set is not reused blindly as generation gold because it includes
questions outside M4 answer scope.

## 14. M4-D13 — implementation stages and PR exit gate

**Proposed decision:** after explicit approval, implementation proceeds on the current M4 branch:

```text
M4.0  tests, schemas, router, binding manifest, and deterministic fakes
M4.1  route orchestration and trusted corpus-scoped anchor resolution
M4.2  ContextPack, provider protocol, composer, and deterministic rendering
M4.3  mechanical validators and all-or-none error handling
M4.4  public API/CLI and lazy production composition
M4.5  PostgreSQL integration, benchmark, documentation, and PR
```

The local M4 pull-request exit gate is **FULFILLED**. Final evidence from the same working tree:

- [x] The full pre-existing plus M4 PostgreSQL suite passed: `682 passed, 1 warning`.
- [x] The frozen M2, M3, and M4 benchmark selection passed: `72 passed`; the M4 mechanical
  benchmark retained every hard 100% invariant in Section 13.
- [x] Ruff passed for the full repository.
- [x] Strict mypy passed for `78 source files`.
- [x] `uv lock --check` passed with `92 packages`.
- [x] Alembic reported exactly one head, `0010_m3_lock_hardening`; `alembic check` reported no
  drift against the current database.
- [x] A separate temporary empty PostgreSQL database upgraded from `0001_empty_baseline` through
  `0010_m3_lock_hardening`; its final `alembic check` reported no drift, and the temporary
  database was deleted.
- [x] `git diff --check` passed.
- [x] `docs/data_semantics.md`, `docs/development_status.md`, and the README distinguish
  engineering-mechanism fulfillment from real activation.
- [x] Synthetic providers, capabilities, bindings, and structured-target anchors remain
  tests-owned and are not selectable through production settings, HTTP, or CLI.
- [x] The PR payload records that real hybrid activation remains blocked: Zhao remains
  candidate-only; no real binding manifest, structured-target anchor package, provider/egress
  approval, or checksum-bound human semantic benchmark is approved.
- [x] M4 added no Alembic revision, schema change, production-data mutation, or generated-answer
  persistence path; production still composes no LLM.

These results authorize the requested M4 pull request. They do not authorize real provider use,
real Zhao hybrid success, or any schema/data mutation, and they do not assert a remote-CI result.

## 15. Explicit exclusions

Draft A excludes:

- publishing or changing the Zhao structured candidate;
- approving a real dataset/corpus binding or modifying the published corpus/anchor manifest;
- selecting or calling a production/paid LLM;
- authorizing document or structured data egress;
- persistence of prompts, provider outputs, claims, or generated answers;
- streaming, sessions, chat memory, personalization, or multilingual output;
- arbitrary SQL or LLM-generated SQL;
- live search, autonomous downloads, arbitrary PDF/OCR, or external tools;
- reranking, learned fusion, GraphRAG, agents, or tool-calling loops;
- biological prevalence/absence, host comparison, infection inference, co-divergence, independent
  integration inference, phylogenetic placement, or new EVE detection;
- M5 Streamlit/demo/release packaging.

## 16. Approved decisions

Approval of Draft A includes all proposed decisions above, especially:

1. M4 engineering completion with real Zhao hybrid intentionally fail-closed;
2. the exact deterministic route grammar and `/v0/query` contract;
3. a checksum-pinned local binding manifest with no real pair approved in M4;
4. no production provider or data egress, with CI-only deterministic fake generation;
5. the 131,072-byte context, 8-chunk, 16-claim, 64-anchor, and 32,768-byte output limits;
6. mechanical validators plus a separately required human semantic-support gate;
7. no persistence, streaming, memory, retries, new database tables, or production data writes.

Any later material change to these decisions requires an explicit amendment before the affected
implementation proceeds.
