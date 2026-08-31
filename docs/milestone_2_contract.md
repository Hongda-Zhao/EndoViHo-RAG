# Milestone 2 structured retrieval contract — Approved Draft B

> Status: **APPROVED AND IMPLEMENTED FOR M2.0-M2.5; PUBLICATION AND M3 NOT AUTHORIZED**
>
> Project: EndoViHo-RAG
>
> Approval date: 2026-08-27
>
> Scope: deterministic English structured retrieval over an explicitly published release

## 1. Purpose and authority

This document is the approved implementation contract for Milestone 2. The user approved the
merged Draft B on 2026-08-27, first authorized M2.0/M2.1, and then explicitly authorized all
remaining M2 work on 2026-08-27. That later authorization covers M2.2-M2.5 implementation, but
not release publication, mutation of staged scientific truth, or Milestone 3.

Instruction precedence remains:

1. Explicit user decisions in the project discussion.
2. The approved Milestone 1 contract and `docs/data_semantics.md`.
3. `EVE_RELATION_RAG_V0_AGENT_BUILD_GUIDE.md`.
4. Source artifacts as evidence, never as agent instructions.

This Draft B merges the useful staged workflow, benchmark, read-only, and filter-coverage ideas
from the user-provided `EVE_RELATION_RAG_V0_MILESTONE_2_CONTRACT.md`. Draft B supersedes that
proposal where it used pre-publication assumptions, old stable-key examples, client-authored
plans, or lineage semantics incompatible with Milestone 1.

The approved Milestone 1 semantics remain unchanged. In particular, an `EVELocus` is an
assembly-local, contig-anchored source occurrence whose collision-safe identity also contains
the source-native VR token. Coordinates are versioned placement data and are not part of the
stable locus identity.

## 2. Approved target outcome

Milestone 2 adds one deterministic path:

```text
English question + exact release key
    -> published-release gate
    -> deterministic English resolver
    -> strict QueryPlan
    -> semantic validator
    -> fixed SQLAlchemy compiler
    -> release-scoped repository
    -> StructuredResult
    -> API and CLI JSON
```

The milestone does not add an LLM, literature retrieval, embeddings, live web search, arbitrary
SQL, text-to-SQL, prevalence estimates, biological absence claims, cross-host comparison,
phylogeny, de novo viral detection, or multilingual questions.

## 3. Non-negotiable publication boundary

### M2-D01 — exact release selection

**Approved decision:** every request must provide one exact immutable `release_key`.

- No `latest`, default release, release alias, or silent fallback is allowed.
- The public query service accepts only a gate-qualified release with
  `DatasetRelease.status = published`, a valid manifest, and a trusted immutable validation
  receipt covering the release dependency graph.
- `candidate`, `validated`, `deprecated`, `rejected`, and missing releases are not queryable in
  Draft B.
- A later contract may permit an exact deprecated release for reproducibility, but Draft B does
  not.
- There is no `allow_unpublished`, preview flag, environment-variable bypass, or administrator
  query mode in the public service.

The present pilot release, `release:endoviho-rag:v0:20260826:001`, is a candidate. Therefore a
real request against it must return `release_not_published`, set
`fact_retrieval_executed = false`, and execute no public fact query. It must not return candidate
counts, candidate loci, quarantine state, or entity suggestions.

### M2-D02 — public fact universe

**Approved decision:** the public locus universe is defined only by explicit release
membership.

Let `M(plan, release)` be the distinct, unpaginated set of matched public loci. It must begin at:

```text
DatasetRelease(status = published)
    -> ReleaseLocusMembership
    -> EVELocus
    -> membership-selected exact EVELocusPlacement
```

Filters then reduce this universe. They never replace its membership root.

- Public assertions and evidence must be reached through `ReleaseAssertionMembership`.
- A bare `EVELocus`, `ScientificAssertion`, `ReleaseAssemblyMembership`, source record, or
  quarantine row is never sufficient for public output.
- An assembly in a release allowlist is publicly represented only when at least one public locus
  in `M` belongs to it.
- Public locus detail may include associated detection calls as source provenance, but it must
  not include call `raw_result`, source-record raw payloads, database numeric IDs, or unpublished
  assertions.
- Within a published release, any syntactically valid locus key absent from the
  membership-scoped lookup returns `entity_not_in_release`. The production resolver must not
  inspect candidate or quarantine tables to determine why it is absent. A candidate release is
  rejected earlier as `release_not_published`.

## 4. Public request contract

### M2-D03 — question-first request

**Approved decision:** public callers submit an English question, not SQL and not a
client-authored QueryPlan.

The following examples are illustrative contracts. `<published_release_key>` is a placeholder,
not a claim that a second pilot release exists and not a valid literal request value.

```json
{
  "request_schema_version": "structured-query-request-v1",
  "release_key": "<published_release_key>",
  "question": "List all loci in this release.",
  "page": {
    "limit": 50,
    "cursor": null
  }
}
```

Rules:

- `release_key` and `question` are required non-empty strings.
- English is the only accepted query language in V0.
- The resolver recognizes the approved controlled-English grammar; it does not use a statistical
  language detector. Text outside that grammar returns `unsupported_question`.
- `page` is optional at the request boundary. The planner adds the default page only for a list
  intent.
- A caller-supplied page is rejected for detail and aggregate intents.
- Unknown fields are rejected.
- The request has no fields for SQL, table, column, operator, join, grouping, projection, sort,
  release status, or permission bypass.
- `question` is one line of `1..2000` Unicode characters with no control characters. Release and
  stable keys are `1..255` characters with no whitespace or control characters. A cursor is
  `1..4096` unpadded base64url characters (`A-Z`, `a-z`, `0-9`, `_`, or `-`). These are
  transport bounds, not fuzzy matching rules.

The implemented API surface is:

```text
POST /v0/structured/plan
POST /v0/structured/query
```

`/plan` returns the validated interpretation without executing public facts. Its successful
response has non-null `query_plan`, `planning_audit`, and `resolved_entities`, a null
`structured_result`, and `fact_retrieval_executed = false`.
`/query` runs the same planner and returns both the validated QueryPlan and StructuredResult.
Draft B does not expose a public endpoint that executes an arbitrary client-authored plan.

## 5. QueryPlan schema

### M2-D04 — strict canonical plan

**Approved decision:** the internal planner emits this small, immutable, discriminated
schema. All Pydantic plan models use strict types, `extra = "forbid"`, and `frozen = true`.

```json
{
  "plan_version": "endoviho-query-plan-v0.1",
  "route": "structured",
  "release_key": "<published_release_key>",
  "intent": "list_loci",
  "original_question": "List loci in assembly GCA_029931535.1.",
  "scope": {
    "scope_type": "filtered",
    "filters": [
      {
        "filter_type": "assembly",
        "assembly_key": "assembly:ncbi:GCA_029931535.1"
      }
    ]
  },
  "page": {
    "limit": 50,
    "cursor": null
  }
}
```

`route` is the literal `structured`. There is no literature parameter in Milestone 2. The schema
is a discriminated union of six plan models keyed by `intent`:

```text
assembly_detail and locus_detail
    metric_key is absent
    page is absent

list_loci, list_assemblies, and list_source_taxa
    metric_key is absent
    page is required and canonical

aggregate
    metric_key is required
    page is absent
```

Forbidden fields are absent, not nullable placeholders. This makes an incompatible metric or
page impossible to pass merely by setting it to `null`.

The scope is one of:

```text
EntireReleaseScope
    scope_type = entire_release
    explicitly_requested = true
    filters are forbidden

FilteredScope
    scope_type = filtered
    filters contains 1..3 entries
    each filter_type appears at most once
    every filter is combined with AND
```

The planner may emit only these filters:

```text
AssemblyFilter
    filter_type = assembly
    assembly_key = exact resolved stable key

LocusFilter
    filter_type = locus
    locus_key = locus:eve:v1:sha256:<64 lowercase hex characters>

SourceLineageFilter
    filter_type = source_lineage
    snapshot_key = exact release-pinned snapshot key
    term_key = exact resolved term key
    role = assembly_source_taxonomy
    include_descendants = explicit boolean

ViralLineageFilter
    filter_type = viral_lineage
    snapshot_key = exact release-pinned snapshot key
    term_key = exact resolved term key
    role = formal_viral_taxonomy | study_viral_lineage | extended_viral_lineage
    include_descendants = explicit boolean
```

The compiler receives stable keys only. It never receives an unresolved accession, canonical
name, alias, or free-form condition as a database constraint.

Filters for `source_confidence`, HCVR, VR Type, sequence coordinates, arbitrary fields, and
arbitrary sorting are deferred. Source confidence may appear as explicitly public source
provenance; it is not a quality threshold or public inclusion filter.

### Full-release scope

The planner may emit `entire_release` only when the question explicitly requests the complete
release, for example, “List all loci in this release.” Missing or unresolved conditions never
mean “query everything.”

### Condition preservation

Planning keeps a typed audit sidecar that is not compiler input:

```text
PlanningAudit
    extracted_conditions[]
        condition_id
        source_text
        source_start
        source_end
        condition_kind
        mapped_target
    mapped_condition_ids[]
    unresolved_condition_ids[]
    unconsumed_semantic_spans[]
```

Every extracted scientific condition must map to exactly one approved intent, filter, metric,
or explicit scope. If the extracted and mapped sets differ, the request returns
`condition_unmapped` and does not call the structured repository.

Each extracted condition also records `source_start`, `source_end`, and one unique
`mapped_target`. The audit records `unconsumed_semantic_spans`. Every entity mention, negation,
logical operator, comparator/range, metric, scope, and pagination phrase must be consumed exactly
once. Unsupported negation, exclusion, range, or OR semantics return `unsupported_question`;
multiple mentions are resolved individually and the resolver must never keep the first while
dismissing the rest.

### Canonical plan hash

`plan_sha256` is SHA-256 over canonical UTF-8 JSON with sorted object keys and `page.cursor`
replaced by `null`. Before hashing, filters are sorted in the fixed order `assembly`, `locus`,
`source_lineage`, `viral_lineage`. The hash includes the exact original question and page limit.
Therefore a cursor is valid only when the caller repeats the same request semantics and page
size.

## 6. Intent contract

### M2-D05 — first supported intents

**Approved decision:** Milestone 2 supports six intents.

| Intent | Required scope | Metric | Pagination | Result kind |
|---|---|---|---|---|
| `assembly_detail` | exactly one assembly filter | forbidden | forbidden | `assembly_detail` |
| `locus_detail` | exactly one locus filter | forbidden | forbidden | `locus_detail` |
| `list_loci` | assembly/source-lineage/viral-lineage filters, or explicit entire release | forbidden | required | `locus_page` |
| `list_assemblies` | source-lineage/viral-lineage filters, or explicit entire release | forbidden | required | `assembly_page` |
| `list_source_taxa` | viral-lineage filter, or explicit entire release | forbidden | required | `source_taxon_page` |
| `aggregate` | assembly/source-lineage/viral-lineage filters, or explicit entire release | required | forbidden | `aggregate` |

`explain_method` and `explain_evidence` are deferred to the literature/hybrid milestones. A
Milestone 2 detail result can return typed provenance, but it does not generate a narrative
explanation.

Examples of deterministic controlled English include:

```text
Show assembly GCA_029931535.1.
Show locus locus:eve:v1:sha256:<64 hex>.
List all loci in this release.
List loci in assembly GCA_029931535.1.
List loci assigned exactly to source lineage <name or stable key>.
List loci assigned to source lineage <name or stable key> including descendants.
List loci with study viral lineage Orthopolintovirales exactly.
List loci with extended viral lineage asfa-like including descendants.
List source taxa represented in this release.
Count distinct included loci in assembly GCA_029931535.1.
```

The source-lineage descendants example is parseable, but the present source snapshots make it
return `lineage_closure_incomplete`. The extended example additionally requires a release-bound
`extended_viral_lineage` snapshot, the requested term, and a complete closure attestation; the
current real candidate fails closed because that role and its evidence-backed assertions are not
yet present. The English resolver is deterministic and rule-based in Milestone 2. It is not an LLM
and is not expected to interpret arbitrary prose. Unsupported wording fails closed with a stable
error and may include safe suggestions.

## 7. Entity resolution and lineage semantics

### M2-D06 — resolver priority

**Approved decision:** resolve the release first, then resolve entities only in namespaces
pinned to that published release.

```text
1. exact assembly accession.version
2. exact locus key
3. exact snapshot-qualified lineage term key
4. exact normalized canonical English name
5. exact normalized curated English alias
6. suggestion only
```

An exact stable lineage reference uses one of these controlled-English forms so the role,
snapshot, and term remain separate and unambiguous:

```text
source lineage term <TERM_KEY> in snapshot <SNAPSHOT_KEY>
formal viral lineage term <TERM_KEY> in snapshot <SNAPSHOT_KEY>
study viral lineage term <TERM_KEY> in snapshot <SNAPSHOT_KEY>
extended viral lineage term <TERM_KEY> in snapshot <SNAPSHOT_KEY>
```

`TERM_KEY` and `SNAPSHOT_KEY` are stable no-whitespace tokens under the request bounds. A
canonical name or alias mention must likewise say `source lineage`, `formal viral lineage`,
`study viral lineage`, or `extended viral lineage`; an omitted or conflicting role returns
`lineage_role_ambiguous`.

Rules:

- Assembly accessions require the version. The resolver never adds a version and never replaces
  a GCA accession with a paired GCF accession, or vice versa.
- Identifier matching is exact and case-sensitive according to its authority grammar.
- Name normalization is limited to Unicode NFKC, trimming, collapsing repeated whitespace, and
  Unicode casefolding. Punctuation, diacritics, authority qualifiers, and meaningful tokens are
  preserved.
- Curated alias collisions are legal in the database. A collision returns `entity_ambiguous`;
  it never chooses the first row.
- Formal viral taxonomy and study-defined viral lineage are separate namespaces. A formal term
  cannot be silently substituted for the Zhao et al. study-defined `Orthopolintovirales`, even
  when display names coincide.
- Extended viral lineage is a separate, release-curated `study_defined` namespace for broad
  evidence-backed affinity groups such as `asfa-like`. It is not an ICTV rank or membership
  claim. A formal `Asfarviridae` query remains strict, while an extended query can cover formal
  and informal descendants only when the release contains explicit extended assertions for
  those loci.
- Fuzzy or prefix matches may return at most five suggestions from the public universe of a
  published release. Assembly suggestions must be represented by a public locus; locus
  suggestions come from `ReleaseLocusMembership`; viral suggestions come from
  `ReleaseAssertionMembership`; source-lineage suggestions come from source assignments of
  assemblies represented by public loci. Suggestions must not come from a bare assembly
  allowlist, candidate locus, candidate assertion, or quarantine row. A suggestion never
  executes a fact query.
- An unknown or ambiguous entity cannot become an entire-release query.
- Resolver priority is applied to every mention independently. Resolving one mention never
  permits another mention or qualifier to be discarded.

Each successful resolution is returned as a typed object containing the original input, entity
kind, match mode, stable key, canonical name when applicable, snapshot key, authority namespace,
snapshot version, scheme kind, and role.

### M2-D07 — exact term versus descendants

**Approved decision:** `include_descendants` must be explicit in the interpreted question and
in each lineage filter.

- “exactly” maps to `false`.
- “including descendants” maps to `true`.
- If neither meaning is clear, the resolver returns `lineage_scope_ambiguous`.
- Descendant lookup is legal only through the closure table belonging to the exact release-pinned
  snapshot.
- With `include_descendants = false`, the direct assignment or public assertion term must equal
  the selected term. With `true`, closure direction is
  `ancestor_term = selected term -> descendant_term = direct assigned/asserted term`, including
  the `depth = 0` self-row.
- A release must provide independently validated evidence that the relevant closure is complete.
  Self-rows alone do not demonstrate completeness.
- The current staging host snapshot contains assembly-report leaf assignments with self-closure,
  the study viral snapshot is self-only, and no real extended snapshot is bound. Therefore
  `include_descendants = true` must currently return `lineage_closure_incomplete` (or fail earlier
  because the role is absent) and execute no public fact query.

No Draft B migration invents a closure-completeness flag. A future trusted validation receipt or
approved schema extension must establish that capability before descendant execution is enabled.

## 8. Metric contract

### M2-D08 — exact integer metrics

**Approved decision:** use five integer metrics. The fifth, `distinct_contig_count`, is an
explicit Draft B addition to the four candidates in the build guide so that contigs and
source-occurrence loci cannot be confused.

All metrics operate on the same distinct, filtered, unpaginated public locus universe `M`.

| Metric key | Exact definition | Unit |
|---|---|---|
| `distinct_included_locus_count` | `COUNT(DISTINCT M.locus_id)` | `loci` |
| `distinct_contig_count` | `COUNT(DISTINCT (M.assembly_id, M.sequence_id))` | `contigs` |
| `distinct_assembly_count` | `COUNT(DISTINCT M.assembly_id)` | `assemblies` |
| `distinct_source_taxon_count` | `COUNT(DISTINCT (assignment.snapshot_id, assignment.term_id))` for assemblies represented in `M` | `source_taxa` |
| `detection_call_count` | `COUNT(DISTINCT DetectionCall.id)` where `DetectionCall.locus_id` belongs to `M` | `source_calls` |

Public `deduplication_key` labels are, respectively:
`release_key+locus_key`, `assembly_accession_version+sequence_accession_version`,
`assembly_accession_version`, `snapshot_key+term_key`, and `release_key+call_key`. Database
numeric IDs may implement the SQL distinct operation but are never returned as public identity.

Additional rules:

- `distinct_assembly_count` is not the count of the release assembly allowlist.
- `distinct_source_taxon_count` counts direct canonical assembly-source assignments, not organism
  strings, closure ancestors, or inferred historical hosts.
- Every represented assembly must have exactly one direct `AssemblyTaxonAssignment` across all
  assignment policy keys in the one pinned `assembly_source_taxonomy` snapshot. Missing or
  multiple rows return `result_integrity_error`; the repository must not select a policy or term
  silently. Selecting a future canonical policy would require that exact policy key in a newly
  approved contract and release capability.
- `detection_call_count` is a source-call count, not a locus count and not a release-membership
  count.
- Joins to assertions, evidence, aliases, or closure rows must not multiply any metric.
- Candidate source-record counts, quarantine counts, prevalence, ratios, percentages, absence,
  and frequency are not Milestone 2 public metrics.

An aggregate payload is an integer and records its definition:

```json
{
  "kind": "aggregate",
  "metric_key": "distinct_included_locus_count",
  "value": 17,
  "unit": "loci",
  "deduplication_key": "release_key+locus_key"
}
```

## 9. Compiler and repository contract

### M2-D09 — capability-gated fixed compilation

**Approved decision:** production code follows this authority path:

```text
PublishedReleaseGate
    -> QueryableRelease capability
    -> ResolverRepository
    -> ValidatedQueryPlan
    -> StructuredQueryCompiler
    -> StructuredRepository
```

Only `PublishedReleaseGate` may create a production `QueryableRelease`. Public services must not
pass a bare numeric `release_id` directly to the structured repository.

A production `QueryableRelease` capability contains, at minimum:

```text
internal release identity
dataset_key and exact release_key
status = published
schema_version and published_at
manifest_sha256
validation_receipt_key and validation_receipt_sha256
exact source dependency role bindings
exact lineage snapshot role bindings
complete_lineage_closure_roles attested by the receipt
```

The gate independently verifies every field and the trusted immutable receipt before producing
the capability. A missing or invalid receipt, manifest, dependency binding, or required role
returns `release_dependencies_incomplete`. A descendant query additionally requires its exact
lineage role in `complete_lineage_closure_roles`.

The production `QueryableRelease` constructor/factory is package-private and gate-owned. Tests
use a tests-only protocol double that production modules cannot import. Neither HTTP nor CLI can
deserialize or accept either capability type.

The compiler:

- dispatches through a closed `(intent, metric_key)` whitelist;
- uses SQLAlchemy expression objects and bound values only;
- compiles every filter to one fixed constraint;
- uses fixed projections and sort columns selected by server enums;
- constructs the distinct matched locus universe first, preferably with `EXISTS`, before count,
  page, or hydration;
- obtains a public placement through the placement referenced by
  `ReleaseLocusMembership`, not an arbitrary placement row;
- applies a source-lineage filter through the pinned `AssemblyTaxonAssignment` and, when approved,
  same-snapshot `LineageClosure`;
- applies a viral-lineage filter through `ReleaseAssertionMembership` and its exact
  `ScientificAssertion(assertion_type = viral_major_taxon)` lineage reference;
- rejects a filter without one fixed compiler mapping as `compiler_constraint_unmapped`.

The production package must not expose an arbitrary SQL repository method. It must not use
user-derived raw SQL, SQLAlchemy `text()` fragments, dynamic identifiers, dynamic operators,
free-form `order_by`, or user-selected columns. Values that resemble SQL remain ordinary bound
values and normally resolve to no entity.

Count and page retrieval run in the same read-only, repeatable-read transaction so the total and
items observe the same release state. Repeatable read guarantees consistency within one request;
it does not prove that the release dependency graph is immutable. That proof belongs to the
trusted receipt and dependency digest. Because the receipt workflow does not yet exist, the
current production success path remains unavailable.

No schema migration is approved by Draft B. An index may be proposed separately only after an
`EXPLAIN`-based need is demonstrated and its Alembic migration is reviewed.

## 10. Pagination contract

### M2-D10 — fixed forward keyset pagination

**Approved decision:** only list intents use pagination.

- Default `limit`: `50`.
- Allowed `limit`: strict integer `1..100`.
- Direction: forward only.
- Offset pagination: forbidden.
- Client-selected sort: deferred.

Fixed order:

| Intent | Canonical order |
|---|---|
| `list_loci` | `locus_key ASC` |
| `list_assemblies` | `assembly_accession_version ASC, assembly_key ASC` |
| `list_source_taxa` | `snapshot_key ASC, term_key ASC` |

The cursor is an opaque, versioned, base64url token authenticated with HMAC-SHA-256. Its signed
payload binds:

```text
cursor_version
release_key
release_manifest_sha256
plan_sha256
intent
canonical_sort_key
last_sort_value(s)
```

The HMAC key is a runtime secret, never committed to Git. Invalid signatures, malformed tokens,
or reuse across a release, question, filter, intent, sort, or limit return a cursor error and do
not execute a public fact query.

`total_count` is the distinct count after all filters and before cursor/limit. It must be equal on
every page. `returned_count` describes only the current page. The last page has
`next_cursor = null`. Concatenating all pages must produce exactly the canonical unpaged set,
without duplicate or missing items.

## 11. StructuredResult contract

### M2-D11 — typed result envelope

**Approved decision:** the API keeps the validated plan separate from the typed result.

```json
{
  "response_schema_version": "structured-query-response-v1",
  "response_kind": "query_success",
  "query_plan": {},
  "planning_audit": {},
  "resolved_entities": [],
  "structured_result": {
    "result_schema_version": "structured-result-v1",
    "plan_sha256": "<canonical plan SHA-256>",
    "release": {
      "dataset_key": "dataset:endoviho-rag",
      "release_key": "<published_release_key>",
      "schema_version": "<frozen release schema version>",
      "status": "published",
      "manifest_sha256": "<64 lowercase hex characters>",
      "published_at": "<RFC 3339 timestamp>"
    },
    "data": {},
    "warnings": [],
    "limitations": []
  },
  "error": null,
  "fact_retrieval_executed": true
}
```

This response is a discriminated union:

```text
PlanSuccess (`response_kind = plan_success`)
    query_plan, planning_audit, and resolved_entities are non-null
    structured_result = null
    error = null
    fact_retrieval_executed = false

QuerySuccess (`response_kind = query_success`)
    query_plan, planning_audit, resolved_entities, and structured_result are non-null
    error = null
    fact_retrieval_executed = true

ErrorResponse (`response_kind = error`)
    structured_result = null
    error is non-null
    fact_retrieval_executed accurately reflects whether a public fact query ran
```

A success requires a non-empty audit in which every extracted condition is mapped and every
semantic span is consumed. Its `resolved_entities` must exactly represent the plan's filtered
entity keys, snapshots, and roles; an explicit entire-release plan has no resolved entities.

No volatile `generated_at` timestamp is included in the scientific result. The same immutable
release and canonical plan should yield semantically stable JSON.

`data` is a discriminated union with one `kind`:

```text
assembly_detail
locus_detail
locus_page
assembly_page
source_taxon_page
aggregate
```

### Common public projections

```text
LineageRef
    term_key
    canonical_name
    rank
    snapshot_key
    authority_namespace
    snapshot_version
    scheme_kind
    role

ExactPlacement
    sequence_key
    sequence_accession_version
    start0
    end0
    strand
    coordinate_system = 0-based-half-open
    precision = exact

LocusSummary
    locus_key
    assembly_key
    assembly_accession_version
    source_organism_name
    source_taxon: LineageRef
    placement: ExactPlacement
    viral_lineages: LineageRef[]

AssemblySummary
    assembly_key
    assembly_accession_version
    source_organism_name
    source_taxon: LineageRef
    included_locus_count

SourceTaxonSummary
    lineage: LineageRef
    represented_assembly_count
    included_locus_count
```

`LocusSummary.viral_lineages` is derived only through that locus's
`ReleaseAssertionMembership -> ScientificAssertion(assertion_type = viral_major_taxon)` rows and
is deduplicated by `(snapshot_key, term_key, role)`. All summary counts are calculated from the
current request's filtered, unpaginated `M(plan, release)`, never from the whole release unless
the plan itself has explicit entire-release scope.

The position is exact public placement data, not part of `EVELocus` identity. A public member
cannot expose an approximate placement because release membership requires exact placement.

`LocusDetail` extends `LocusSummary` with typed source provenance and public assertion data:

```text
CallDetail
    call_key
    source_method_key
    process_run_key
    source_record_key
    source artifact key/checksum
    worksheet and row locator

PublicAssertionDetail
    assertion_key
    assertion_type
    predicate_key
    asserted_value
    optional source_label
    optional source_confidence = source_high | source_low
    optional lineage: LineageRef
    method_definition_key and version
    process_run_key
    supporting_evidence: EvidenceDetail

EvidenceDetail
    evidence_key
    evidence_type
    evidence_sha256
    source_locator
    summary
    artifact_key
    artifact_sha256
    source_uri
    verified_license_key
```

Every `PublicAssertionDetail` must be selected by `ReleaseAssertionMembership`, and its evidence
must be the single membership-selected edge with `relation = supports`.
`EvidenceDetail.artifact_sha256` is `SourceArtifact.verified_sha256`. `source_high` and
`source_low` are labeled as versioned source assessments; neither is described as independent
validation or public quality.

The six exact `data` variants are:

```text
assembly_detail
    assembly: AssemblySummary

locus_detail
    locus: LocusSummary
    calls: CallDetail[]
    public_assertions: PublicAssertionDetail[]

locus_page
    items: LocusSummary[]
    page: PageInfo

assembly_page
    items: AssemblySummary[]
    page: PageInfo

source_taxon_page
    items: SourceTaxonSummary[]
    page: PageInfo

aggregate
    metric_key
    value
    unit
    deduplication_key
```

List results contain:

```text
kind
items[]
page.limit
page.returned_count
page.total_count
page.next_cursor
page.sort_key
page.sort_direction = asc
```

Stable limitation codes include:

```text
assembly_source_taxon_is_not_ancient_host
assembly_local_locus_is_not_independent_integration_event
zero_matches_do_not_establish_biological_absence
source_confidence_is_not_release_validation
coordinates_are_zero_based_half_open
detection_calls_are_not_loci
```

They are required deterministically:

- any result containing a source taxon, or the source-taxon metric, includes
  `assembly_source_taxon_is_not_ancient_host`;
- any result containing a locus, or a locus/contig aggregate, includes
  `assembly_local_locus_is_not_independent_integration_event`;
- any result containing coordinates includes the coordinate limitation; any result containing
  source confidence includes the confidence limitation; any result containing detection calls
  or the detection-call metric includes the call limitation;
- every valid zero-match list or aggregate includes
  `zero_matches_do_not_establish_biological_absence`.

`warnings` and `limitations` are both sorted arrays of `{ "code": ..., "message": ... }`
objects, never a mixture of strings and objects. For stable JSON, resolved entities are sorted by
entity kind and stable key; viral lineages by snapshot key, term key, and role; calls by
`call_key`; assertions by `assertion_key`; and any nested evidence by `evidence_key`.

A valid list or aggregate query may return zero with HTTP 200 and
`fact_retrieval_executed = true`. It must also return
`zero_matches_do_not_establish_biological_absence`. A missing public detail entity is an error,
not a successful empty detail.

## 12. Error contract

### M2-D12 — stable fail-closed envelope

**Approved decision:** every refusal uses one stable envelope.

```json
{
  "response_schema_version": "structured-query-response-v1",
  "response_kind": "error",
  "query_plan": null,
  "planning_audit": null,
  "resolved_entities": [],
  "structured_result": null,
  "error": {
    "code": "release_not_published",
    "message": "The requested release is not published.",
    "field_errors": [],
    "suggestions": []
  },
  "fact_retrieval_executed": false
}
```

Release lookup, request validation, and resolver metadata lookup do not count as public scientific
fact retrieval. All schema, release, resolver, semantic, capability, and cursor refusals must set
the flag to `false`. If the structured repository has already executed a public fact query when
an internal failure occurs, the flag must accurately be `true`, while result data remains absent.

Approved codes:

```text
request_schema_invalid
query_plan_version_unsupported
unsupported_question
intent_unsupported
unsupported_capability
condition_unmapped
full_release_scope_not_explicit
intent_filter_incompatible
filter_unsupported
metric_required
metric_unsupported
pagination_not_allowed
limit_invalid

release_required
release_key_invalid
release_alias_forbidden
release_not_found
release_not_published
release_dependencies_incomplete
release_manifest_invalid

assembly_accession_version_required
entity_unresolved
entity_ambiguous
entity_not_in_release
lineage_snapshot_mismatch
lineage_role_ambiguous
lineage_scope_ambiguous
lineage_closure_incomplete

cursor_invalid
cursor_plan_mismatch
compiler_constraint_unmapped
result_integrity_error
structured_query_failed
```

Recommended HTTP mapping:

| HTTP | Cases |
|---:|---|
| `200` | successful plan or fact query, including a valid empty list/aggregate |
| `400` | malformed or context-mismatched cursor |
| `404` | release/entity unresolved, or entity not in public membership |
| `409` | ambiguous entity, non-published release, incomplete release dependency |
| `422` | request/plan schema, language, intent, metric, filter, scope, pagination, or capability refusal |
| `500` | server-generated plan version mismatch, unmapped compiler constraint, result integrity, or internal structured-query failure |

Because public callers cannot submit a QueryPlan, `query_plan_version_unsupported` and
`compiler_constraint_unmapped` indicate server contract defects and always map to HTTP 500.
FastAPI's default validation response must be converted to this project envelope. Error messages
are concise English and do not expose SQL, stack traces, numeric database IDs, secrets, candidate
state, or quarantine details.

## 13. CLI contract

**Approved decision:** the CLI and API call the same planner, validator, gate, compiler, and
repository services.

Approved implemented commands:

```text
eve-relation-rag structured plan \
    --release-key KEY \
    --question "English question"

eve-relation-rag structured query \
    --release-key KEY \
    --question "English question" \
    [--limit 50] \
    [--cursor TOKEN] \
    [--format json|table]
```

- JSON is the default and canonical format.
- Table output is a presentation only and must not change values.
- Successful JSON goes to stdout; errors go to stderr.
- Exit `0`: success, including a valid empty result or plan-only response.
- Exit `2`: missing CLI arguments, request/schema/cursor/language/unsupported/semantic error.
- Exit `3`: entity unresolved, ambiguous, or not in release.
- Exit `4`: `release_not_found`, `release_not_published`, or dependency-incomplete.
- Exit `5`: database, result-integrity, or internal error.

Typer was added as a locked dependency during the authorized M2.5 API/CLI subphase. It remains a
transport adapter over the same application service used by FastAPI.

## 14. Verification and acceptance

The implementation is accepted only when tests demonstrate all of the following.

### Schema, planning, and resolver

- QueryPlan golden JSON and strict rejection of every extra or wrong-typed field.
- Every supported intent/filter/metric compatibility combination, plus every forbidden
  combination.
- A curated controlled-English gold set resolves intent and slots with exact equality.
- Exact identifiers, canonical names, aliases, collisions, incomplete accessions, and
  formal/study/extended namespace collisions, plus unsupported wording, are covered.
- Extracted condition IDs and mapped condition IDs are equal before compilation; source spans are
  complete and `unconsumed_semantic_spans` is empty.
- Unsupported negation, exclusion, OR, ranges, duplicate filter types, and additional entity
  mentions are rejected rather than partially interpreted.
- Unknown, versionless, ambiguous, or condition-losing questions produce zero public repository
  calls.

### Fact set and metric exactness

- Every intent/filter/metric has a manually specified gold fixture and exact result-set equality.
- Duplicate assertions, multiple evidence edges, aliases, and lineage joins do not change a
  distinct set or metric.
- All five metric values equal independent fixture counts.
- Missing or multiple effective source-taxon assignments produce `result_integrity_error` rather
  than a selected or undercounted term.
- Every public locus and assertion is demonstrably rooted in its corresponding release
  membership.
- Assembly lists/counts include only assemblies represented by public loci, not the allowlist.
- Candidate-only loci, assertions, evidence, and quarantine data never leak.
- Formal, study-defined, and extended viral lineages never cross-resolve.
- Incomplete descendant closure fails before fact retrieval.

### Pagination

- Concatenated pages exactly equal the canonical unpaged sorted set with no duplicate or gap.
- `total_count` is identical on every page and independent of page position.
- Invalid, tampered, cross-plan, cross-release, or cross-limit cursors fail closed.
- Aggregate values do not change with transport pagination.

### SQL and service boundary

- Every compiler path uses a fixed SQLAlchemy statement shape and bound values.
- Unknown fields such as `sql`, `operator`, `order_by`, and selected columns fail before
  compilation.
- SQL-injection-shaped entity text is only a bound resolver value and never changes query scope.
- Repository spies prove that release and semantic refusals execute no public fact query.
- The production release gate rejects a missing/invalid receipt or dependency digest and is the
  only production constructor of `QueryableRelease`.
- Production imports and public request schemas cannot construct, import, or deserialize the
  tests-only release capability.
- Count and page queries use one read-only repeatable-read transaction.
- API and CLI return semantically identical canonical JSON for the same request.

### Project checks

```sh
uv run pytest
uv run ruff check .
uv run mypy src
uv run alembic check
```

PostgreSQL integration tests and GitHub Actions must remain green. Performance indexes are not an
acceptance substitute for result correctness.

## 15. Testing under the current `0005` publication gate

Migration `0005_m1_fail_closed_publication` deliberately prevents candidate promotion until a
trusted validation-receipt workflow exists. Milestone 2 must not drop or disable its trigger,
change `session_replication_role`, hand-edit the pilot status, or fabricate a published release.

The approved test split is:

1. In the fully migrated PostgreSQL database, test `PublishedReleaseGate` against the real
   candidate. It must return `release_not_published`, and a repository spy must show zero public
   fact calls.
2. Build fully constrained synthetic **candidate** membership fixtures for repository tests. A
   factory located only under `tests/` may create a `TestsOnlyQueryableRelease` protocol double
   and directly test compiler/repository exact sets, metrics, hydration, and pagination.
3. Test successful service/API composition with a fake published-release gate and the verified
   repository projections. The fake gate must not enter the production package. Synthetic tests
   must not emit or snapshot an object presented as a real `PublishedReleaseRef`, and public HTTP
   or CLI inputs must be unable to accept a test capability.
4. Add a real published-release end-to-end success test only after an approved immutable
   validation-receipt/publication workflow exists.

This strategy tests Milestone 2 mechanics without weakening Milestone 1's scientific publication
boundary. Until item 4 is possible, project status must explicitly say that real published-release
end-to-end activation remains publication-gated; it must not claim that the current pilot is a
public EVE dataset.

## 16. Approved tools and fixed parameters

| Item | Approved Draft B value |
|---|---|
| Runtime | CPython 3.12 |
| Truth store | PostgreSQL 16 |
| ORM/compiler | SQLAlchemy 2.x expression API; no arbitrary SQL |
| Schema validation | Pydantic v2 strict frozen models |
| API | FastAPI |
| CLI | Typer, implemented in the M2.5 API/CLI subphase |
| Tests | pytest with PostgreSQL integration tests |
| Static checks | Ruff and mypy strict mode |
| Query language | deterministic controlled English only |
| Release selection | exact `release_key`; published status plus trusted validation receipt |
| Public locus root | `ReleaseLocusMembership` |
| Public assertion root | `ReleaseAssertionMembership` |
| Filter combination | AND only |
| List page default / maximum | 50 / 100 |
| Pagination | forward keyset only |
| Cursor authentication | HMAC-SHA-256 with runtime secret |
| Transaction | read-only, repeatable read |
| Coordinates | 0-based half-open; exact placement only in public results |
| QueryPlan hash | SHA-256 of canonical JSON with null cursor |
| LLM / embeddings / literature | not used in Milestone 2 |

Exact dependency versions and hashes will remain frozen in `uv.lock` after implementation.

## 17. Approved decisions and current authorization

The user approved all M2-D01 through M2-D12 decisions in this merged Draft B, including:

1. exact release keys, published status plus trusted receipt, and rejection of `deprecated`;
2. public locus/assertion membership roots and no candidate preview;
3. question-first `/plan` and `/query`, with no arbitrary plan execution endpoint;
4. strict QueryPlan unions, AND-only filters, explicit entire-release scope, and condition audit;
5. six intents, with both explain intents deferred;
6. separate formal/study/extended viral namespaces and fail-closed descendant completeness;
7. five exact metrics, including `distinct_contig_count`;
8. capability-gated fixed SQLAlchemy compilation and no default M2 migration;
9. HMAC-authenticated forward keyset pagination with fixed ordering and page bounds;
10. typed results, stable limitations/errors, API/CLI status mappings, and tests-only capability
    isolation without modifying or bypassing migration `0005`.

Implementation authority now covers M2.0 through M2.5. It still does not authorize release
publication, a bypass or alteration of migration `0005`, mutation of the Milestone 1 truth layer,
or Milestone 3.

## 18. Approved implementation sequence

### M2.0 — contract synchronization

Inspect the existing Milestone 1 schema and write:

```text
docs/milestone_2_schema_mapping.md
```

The mapping records exact existing tables, keys, relationships, release gates, and known missing
capabilities. It adds no scientific semantics and no database migration.

### M2.1 — schema-only foundation

Implement only:

```text
strict Pydantic StructuredPlan discriminated union
strict StructuredResult and ErrorResponse discriminated unions
PlanningAudit and resolved-entity schema objects
canonical QueryPlan JSON serialization
plan_sha256 with cursor normalized to null
schema-level validation and canonicalization tests
```

M2.1 must not import database sessions or execute queries. It must not implement parser,
resolver, compiler, repository, API routes, CLI commands, renderer, or release capability.

### M2.2 — parser and resolver (implemented)

Deterministic controlled-English parser, mention extraction, exact resolver, curated aliases,
suggestions, and condition coverage.

### M2.3 — validator and structured retrieval (implemented)

Published-release capability gate, semantic validator, fixed SQLAlchemy compiler, repositories,
metrics, public membership projections, and read-only execution.

### M2.4 — pagination and deterministic presentation (implemented)

HMAC keyset cursor, total-before-page behavior, serializers, and optional deterministic English
or table renderer derived only from StructuredResult.

### M2.5 — API, CLI, benchmark, and documentation (implemented)

FastAPI routes, Typer commands, README examples, complete structured benchmark, and final status
update. Completion never automatically authorizes Milestone 3.

## 19. Benchmark target for full Milestone 2

M2.5 will contain at least 30 controlled-English gold questions:

| Category | Minimum |
|---|---:|
| assembly detail | 4 |
| locus detail | 4 |
| source-lineage queries | 4 |
| viral-lineage queries | 4 |
| combined source × virus | 5 |
| aggregate | 4 |
| invalid, ambiguous, or unsupported | 5 |

Each case freezes its request, status, intent, resolved entities, canonical plan, exact result
keys, exact numbers, limitations, provenance, and applied constraints. Expected and actual sets
must be exactly equal; subset acceptance is forbidden. The 31-case catalog is the
controlled-English planning and contract oracle; it is not, by itself, evidence that every case
executed through SQL. A separate PostgreSQL production-fact matrix executes every intent, filter
class, combined source-and-virus filtering, and all five metrics through the fixed compiler and
membership-rooted repository. Both layers are required for M2.5 acceptance.

## 20. Full Milestone 2 exit conditions

- `docs/milestone_2_schema_mapping.md` matches the current ORM and Alembic head.
- All six plan variants have exact accepted and rejected schema tests.
- Non-aggregate plans cannot contain `metric_key`.
- Detail and aggregate plans cannot contain `page`; list plans require canonical page data.
- Every filter is strict, role-qualified where applicable, and deterministically ordered.
- Canonical serialization is stable across equivalent filter input order.
- `plan_sha256` ignores only the cursor and changes for every other execution-relevant field.
- Plan, query, and error response variants are mutually exclusive and strict.
- Success audits are non-empty and complete, and resolved entities exactly match plan filters.
- A query success binds its canonical plan hash, exact release key, intent/result kind, aggregate
  metric, list limit, and detail identity to the returned result.
- Result projections use exact M1 public key and coordinate grammars without querying data.
- Parser, release-scoped resolver, gate, semantic validator, fixed compiler, membership-rooted
  repository, signed cursor, deterministic renderer, API, CLI, and benchmark are present.
- No M2 database migration was needed, and migration `0005` remains the publication boundary.
- `uv run pytest`, `uv run ruff check .`, `uv run mypy src`, and `uv run alembic check` pass.
- `docs/development_status.md` records full M2 completion and the continued publication gate.
