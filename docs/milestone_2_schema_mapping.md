# Milestone 2 schema mapping — M2.0

> Status: **implemented documentation for approved M2.0; no query code or schema change**
>
> Project: EndoViHo-RAG
>
> Mapping date: 2026-08-27
>
> Database head inspected: `0005_m1_fail_closed_publication`

## 1. Purpose and authority

This document maps the approved Milestone 2 contract onto the Milestone 1 truth schema that
already exists. It does not add a table, field, release rule, scientific interpretation, or
public membership. The normative order is:

1. explicit user decisions;
2. `docs/milestone_1_contract.md` and `docs/data_semantics.md`;
3. `docs/milestone_2_contract.md`;
4. this implementation mapping;
5. ORM and migration details as implementation evidence.

The inspected implementation sources are:

- `src/eve_relation_rag/db/models.py`;
- Alembic revisions `0001_empty_baseline` through `0005_m1_fail_closed_publication`;
- `src/eve_relation_rag/releases/validator.py` for the present non-persistent validation report;
- the three contracts/status documents listed above and `docs/development_status.md`.

The ORM currently defines 32 domain tables. Alembic head is
`0005_m1_fail_closed_publication`. No Milestone 2 migration is authorized by Draft B.

## 2. Current activation state

The existing pilot is a staging truth layer, not a public EVE release:

| Current fact | Value | M2 consequence |
|---|---:|---|
| Release | `release:endoviho-rag:v0:20260826:001` | Exact key exists, but status is `candidate`. |
| Source records / detection calls | 39,495 / 39,495 | Candidate/source provenance only. |
| Coordinate-free loci | 39,495 | A bare locus is not public. |
| Exact candidate placements | 38,968 | Exact coordinates do not create membership. |
| Terminal quarantines | 527 | Audit-only; never a public-query root. |
| Flank assessments | 0 | Public membership gate is incomplete. |
| Inclusion decisions | 0 | Public membership gate is incomplete. |
| `release_locus_membership` rows | 0 | Public locus universe is empty/unavailable. |
| `release_assertion_membership` rows | 0 | No assertion is public. |
| Published releases | 0 | Production M2 success path is unavailable. |
| Trusted immutable validation receipts | 0 / no table | `QueryableRelease` cannot be issued. |

A real request for the current release must stop at the future `PublishedReleaseGate` with
`release_not_published`, `fact_retrieval_executed = false`, no entity suggestions, and no public
fact repository call. It must not turn the unavailable public scope into a successful zero.

## 3. Release identity, dependency pins, and receipt gate

### 3.1 Existing release rows

| M2 concept | Existing table/columns | Existing guarantee | Public-gate interpretation |
|---|---|---|---|
| Dataset identity | `dataset.dataset_key` | Globally unique, append-only identity. | Returned as the stable dataset identity. |
| Exact release | `dataset_release.release_key` | Globally unique; release belongs to one `dataset_id`. | Exact match only; no `latest` or alias. |
| Release schema | `dataset_release.schema_version` | Required string. | Must be copied into `QueryableRelease` and `StructuredResult`. |
| Release state | `dataset_release.status` | Enum-like check: `candidate`, `validated`, `published`, `deprecated`, `rejected`. | Draft B accepts only `published`. |
| Manifest digest | `dataset_release.manifest_sha256` | Optional 64-hex value; required by a database check for `published`/`deprecated`. | Gate must require and independently verify the digest. |
| Publication time | `dataset_release.published_at` | Required by a database check for `published`/`deprecated`. | Returned only after the release passes the gate. |
| Supersession | `dataset_release.supersedes_release_id` | Must refer to the same dataset and cannot refer to itself. | Does not authorize following an alias or silently moving to another release. |

`0005_m1_fail_closed_publication` replaces the lifecycle trigger so that:

- every inserted release starts as `candidate`;
- transitions to `validated` or `published` are rejected because no trusted receipt workflow
  exists;
- `published` content would be immutable, with only a status-only transition to `deprecated`;
- `deprecated` and `rejected` releases cannot be reopened in place.

The status column and manifest digest are necessary but not sufficient for an M2 production
capability.

### 3.2 Existing dependency bindings

| Dependency kind | Existing binding | Cardinality/role semantics |
|---|---|---|
| Frozen input snapshots | `release_source_snapshot(release_id, source_snapshot_id, role)` | At most one source snapshot per release role. The role vocabulary is not constrained by a database enum. |
| Lineage snapshots | `release_lineage_snapshot(release_id, snapshot_id, role, domain, scheme_kind)` | At most one row per release role; roles and domain/scheme combinations are database constrained. A receipt must require every role needed by a queryable release. |
| Method definitions | `release_method_definition(release_id, method_definition_id, role)` | Pins exact versioned methods; more than one method may share a role. |
| Assembly scope | `release_assembly_membership(release_id, assembly_id, membership_role)` | Pilot allowlist/scope only; it is not public EVE membership. |

The referenced objects carry stable keys, versions, source artifacts, checksums, and license or
provenance metadata. A future receipt must attest the exact dependency graph rather than trusting
that rows merely share a `release_id`.

### 3.3 Missing receipt mapping

There is no `validation_receipt` ORM model, database table, artifact binding, receipt checksum,
signature, dependency-graph digest, or closure-completeness attestation in the current schema.
`ReleaseValidationReport` in `src/eve_relation_rag/releases/validator.py` is an immutable in-memory
DTO result; it is not a stored, signed, checksummed, release-bound receipt and cannot authorize a
status transition or a `QueryableRelease`.

Consequently, these approved `QueryableRelease` fields have no current production source:

```text
validation_receipt_key
validation_receipt_sha256
receipt-attested dependency graph
receipt-attested complete_lineage_closure_roles
```

No M2.0/M2.1 code may invent these values. A separately approved receipt workflow and migration
must exist before the production gate can return success.

## 4. Public membership graph

### 4.1 Public locus membership is the sole locus root

The approved public path maps exactly to:

```text
DatasetRelease(status = published, trusted receipt verified)
  -> ReleaseLocusMembership(release_id, locus_id)
       -> EVELocus(release_id, id)
       -> EVELocusPlacement selected by membership.placement_id
       -> InclusionDecision selected by membership.inclusion_decision_id
       -> left FlankAssessment selected by membership.left_flank_assessment_id
       -> right FlankAssessment selected by membership.right_flank_assessment_id
```

`ReleaseLocusMembership` encodes the public gates with composite foreign keys and checks:

- the selected placement belongs to the same release and locus and has `precision = exact`;
- the selected inclusion decision belongs to that locus and placement and has
  `decision_code = include`;
- the left and right assessments belong to that locus and placement, have their correct sides,
  and both have `verdict = supported`;
- the two flank assessment rows are distinct.

The membership table does not carry both `assessment_policy_key` values and therefore cannot by
itself prove that the two supported flanks used the same approved policy. The current
`ReleaseValidationReport` logic checks that equality, but a future trusted receipt must persist
and attest the result before publication.

`M(plan, release)` is the distinct, filtered, unpaginated set rooted in this table. Neither
`eve_locus`, `release_assembly_membership`, `inclusion_decision`, nor an exact placement may
replace the membership root.

### 4.2 Public assertion membership is the sole assertion root

The approved public assertion path maps exactly to:

```text
ReleaseLocusMembership
  -> ReleaseAssertionMembership(release_id, assertion_id)
       -> ScientificAssertion for the same release, locus, and ProcessRun
       -> ProcessRun(execution_status = succeeded)
       -> AssertionEvidence for the selected evidence with relation = supports
       -> EvidenceItem for the same release
```

One `ReleaseAssertionMembership` row selects one supporting evidence item. Other bare assertion
or evidence rows, including `contradicts` and `context` edges, are not automatically public. A
future broader evidence projection requires a separately approved result contract.

### 4.3 Existing database protection

Revision `0003_m1_assertion_evidence` makes global identity/provenance tables append-only and
makes all release-scoped tables immutable once their release is `published` or `deprecated`.
`QuarantineIssue` has a separate join-aware immutability trigger. These triggers are migration
semantics and are not visible from SQLAlchemy column metadata alone.

## 5. Assembly, sequence, locus, and placement mapping

| M1 object | Stable/public fields relevant to M2 | Identity and relationship | M2 use |
|---|---|---|---|
| `GenomeAssembly` | `assembly_key`, `namespace`, `accession_version`, `source_organism_name` | Exact `GCA/GCF_accession.version`; source organism name is display metadata, not a taxon identity or ancient-host claim. | Resolve exact accession only after release gate; expose only if represented by `M`. |
| `AssemblySequence` | `sequence_key`, `namespace`, `accession_version`, `sequence_length` | Exact versioned contig within one exact assembly. | Hydrate the public placement and contig identity. |
| `ReleaseAssemblyMembership` | release and assembly IDs, `membership_role` | Defines the release allowlist/scope. | Required parent scope for loci/taxon assignments, but never proof that an assembly has a public locus. |
| `EVELocus` | `locus_key`, assembly/sequence links, `native_vr_token`, `identity_policy_key` | Coordinate-free source occurrence. Stable-key grammar is `locus:eve:v1:sha256:<64 lowercase hex>`. | Public only through `ReleaseLocusMembership`. |
| `EVELocusPlacement` | `placement_key`, `start0`, `end0`, `strand`, `precision`, `coordinate_system`, provenance hash/locator | At most one placement per release/locus; coordinates are separate from locus identity. | Public output uses only the exact placement selected by membership. |

Important cardinality rules:

- one contig may contain multiple distinct loci because `native_vr_token` is part of the source
  occurrence identity;
- two loci may share the same exact interval; revision `0004_m1_shared_intervals` deliberately
  changed the interval index to non-unique;
- a locus count therefore cannot be inferred from distinct contigs or distinct intervals;
- `EVELocusPlacement` has a bounds trigger requiring the sequence to belong to the same assembly
  and `end0 <= sequence_length`;
- canonical coordinates are 0-based half-open. A 1-based closed display is derived as
  `start1 = start0 + 1`, `end1 = end0` and must be labeled as derived.

## 6. Lineage mapping

### 6.1 Snapshot and role objects

| M1 object | Existing semantics | M2 mapping |
|---|---|---|
| `LineageSnapshot` | Stable `snapshot_key`; `domain = host|viral`; `scheme_kind = formal_taxonomy|study_defined`; authority namespace, version, source artifact, SHA-256. Host study-defined snapshots are forbidden. | Supplies snapshot-qualified resolver namespace and `LineageRef` metadata. |
| `ReleaseLineageSnapshot` | Pins at most one snapshot per release role when that role is present. | The only source of a lineage role accepted by a query capability; the future gate validates every role required by a query. |
| `LineageTerm` | `term_key` is unique only within one snapshot; also has canonical name, rank, authority local ID, and locator. | Resolve and return as `(snapshot_key, term_key, role)`, never as a bare display name. |
| `LineageAlias` | Alias rows are scoped to snapshot/term; collisions across terms remain legal; locale defaults to `en`. | Exact English alias resolution may return multiple candidates and must fail ambiguous. |
| `LineageClosure` | Same-snapshot `(ancestor, descendant, depth)` closure; depth 0 is the self-row. | Used only when the receipt attests that the exact role has complete closure. |

The role constraints are exact:

| Role | Domain | Scheme kind | Scientific meaning |
|---|---|---|---|
| `assembly_source_taxonomy` | `host` | `formal_taxonomy` | Taxon assigned to the source assembly; not a modern or ancient infection claim. |
| `formal_viral_taxonomy` | `viral` | `formal_taxonomy` | Formal viral taxonomy in its exact frozen authority snapshot. |
| `study_viral_lineage` | `viral` | `study_defined` | A versioned source/study label; it must not masquerade as formal taxonomy. |

The staged Zhao label `Orthopolintovirales` is a `study_viral_lineage` source assertion. A query
for formal ICTV taxonomy cannot silently resolve to it even if the canonical names coincide.

### 6.2 Assembly-source assignments

`AssemblyTaxonAssignment` binds an allowlisted assembly to a term in the release-pinned
`assembly_source_taxonomy` snapshot and records an assignment policy and source locator.

The schema permits more than one assignment across different policy keys. Draft B therefore
requires every assembly represented by `M` to have exactly one direct assignment across all
policy keys in the one pinned host snapshot. Missing or multiple rows are
`result_integrity_error`; M2 must not choose a policy silently. `source_organism_name` is not a
substitute for the term.

### 6.3 Exact and descendant semantics

```text
include_descendants = false
    direct assignment/public assertion term_id = selected term_id

include_descendants = true
    LineageClosure.snapshot_id = selected snapshot
    ancestor_term_id = selected term_id
    descendant_term_id = direct assignment/public assertion term_id
    depth >= 0, including the selected term itself
```

Table presence does not establish closure completeness. The current host snapshot contains
assembly-report leaf terms and self-rows only; the study viral snapshot is also self-only. The
complete NCBI Taxonomy dump with merged/deleted TaxId history and the required formal ICTV
snapshot are not loaded. There is no closure-completeness column or receipt attestation.

The real current pilot is only a candidate, so its requests fail first with
`release_not_published` and do not enter lineage resolution. For a future gate-qualified release
whose receipt does not attest complete closure for the requested role,
`include_descendants = true` must fail with `lineage_closure_incomplete` before public fact
retrieval. In particular, the source workbook's `Bivalvia` selection label must not be used as a
substitute for a formal NCBI ancestor term.

## 7. Calls, assertions, methods, and evidence

### 7.1 Source row and method-specific call

```text
SourceSnapshot -> SourceArtifact -> SourceRecord
                                      -> DetectionCall -> optional EVELocus
                                             -> ProcessRun -> MethodDefinition
```

- `SourceRecord` is the immutable physical workbook row. Its identity is independent of method;
  its raw payload is not a public M2 projection.
- `DetectionCall` is method/run-specific and release-scoped. `0005` permits multiple calls for
  one source record when their `process_run_id` differs, and binds `ImportLedger` call/locus links
  back to the same physical source record.
- A call is not a locus or a release membership. Public locus detail may expose safe call
  provenance only after anchoring the call's `locus_id` in `M`; `raw_result` is forbidden.
- `MethodDefinition` is versioned and checksummed. `definition_artifact_id` is nullable at head;
  method identity does not require fabricating an artifact association.
- `ProcessRun` pins a release method role and records its execution status. `DetectionCall` and
  public assertion membership independently require a `succeeded` status.

### 7.2 Source assessment and scientific assertion

`SourceAssessment` stores the Zhao HCVR label and the source-relative mapping
`Yes -> source_high`, every other explicit label -> `source_low`. It has no direct path to public
membership and is never a quality threshold or default filter.

`ScientificAssertion` supports exactly three typed assertion kinds at head:

| Assertion type | Typed fields | Public interpretation |
|---|---|---|
| `hcvr` | Required `SourceAssessment`, source label, and source confidence. | Versioned source assessment only. |
| `viral_major_taxon` | Required lineage snapshot/term and role `formal_viral_taxonomy` or `study_viral_lineage`. | Formal and study-defined namespaces remain distinct. |
| `vr_type` | No source-assessment or lineage columns. | Versioned source `VR Type` assertion, not independent flank evidence. |

A bare `ScientificAssertion` is not public. `PublicAssertionDetail` begins at
`ReleaseAssertionMembership`, which also selects one `supports` evidence edge.

### 7.3 Evidence projection

`EvidenceItem` is release-scoped and must refer to a release-pinned source snapshot and an
artifact within that snapshot. It supplies `evidence_key`, type, source locator, SHA-256, summary,
and artifact provenance.

`AssertionEvidence` relations are `supports`, `contradicts`, or `context`. Only the exact
`supports` edge selected by `ReleaseAssertionMembership` is mapped to the approved M2.1
`supporting_evidence` projection. Existence of an evidence row alone never proves public
eligibility.

## 8. Mapping to approved M2 query objects

### 8.1 Production gate and capability

| M2 object | Existing source | Required mapping rule |
|---|---|---|
| `PublishedReleaseGate` | `Dataset`, `DatasetRelease`, dependency-binding tables, and future receipt store | Reject before entity/fact lookup unless exact release, `published`, manifest, dependencies, and receipt all verify. |
| `QueryableRelease` | No persistent table; future package-private capability | Contains internal release ID plus stable release metadata, verified dependency role bindings, receipt key/hash, and receipt-attested complete closure roles. Never accepted from HTTP/CLI. |
| `ResolvedEntity` | Membership-scoped assembly/locus rows; lineage rows in the exact capability-pinned snapshot and role | Returns stable keys and snapshot metadata, never numeric database IDs. Exact lineage resolution may select a non-represented closure ancestor; only suggestions are restricted to the represented public universe. |

### 8.2 Filter-to-schema mapping

All filters reduce the same membership-rooted `M` and combine with `AND`:

| Approved filter | Existing path | Exact condition |
|---|---|---|
| Assembly | `M -> EVELocus -> GenomeAssembly` | Exact resolved `assembly_key`. |
| Locus | `M -> EVELocus` | Exact `locus_key`; only legal for `locus_detail`. |
| Source lineage exact | `M.assembly_id -> AssemblyTaxonAssignment -> LineageTerm` | Direct assignment term equals selected term in the capability-pinned host snapshot/role. |
| Source lineage descendants | Same plus `LineageClosure` | Selected term is ancestor of the direct assigned term; closure role must be receipt-attested complete. |
| Viral lineage exact | `M -> ReleaseAssertionMembership -> ScientificAssertion -> LineageTerm` | Public assertion type is `viral_major_taxon`; role/snapshot/term all equal the filter. |
| Viral lineage descendants | Same plus `LineageClosure` | Selected term is ancestor of the public asserted term; exact viral role must be receipt-attested complete. |

### 8.3 Result projection mapping

| Approved M2 projection | Existing source path | Exclusions/integrity rules |
|---|---|---|
| `ReleaseRef` | `Dataset -> DatasetRelease` after gate | Stable keys, schema version, manifest SHA, published time only; no bare capability/internal ID. |
| `LineageRef` | `ReleaseLineageSnapshot -> LineageSnapshot -> LineageTerm` | Always include snapshot key, authority, version, scheme kind, and role. |
| `ExactPlacement` | `ReleaseLocusMembership.placement_id -> EVELocusPlacement -> AssemblySequence` | Exact/member-selected placement only. |
| `LocusSummary` | `M -> EVELocus -> GenomeAssembly/AssemblySequence`, direct source assignment, member viral assertions | Viral lineages are deduplicated by snapshot/term/role; no candidate assertions. |
| `AssemblySummary` | Assemblies represented in filtered `M` | Count matched loci in unpaginated `M`, not the release allowlist. |
| `SourceTaxonSummary` | Direct assignments of assemblies represented in filtered `M` | Count represented assemblies/loci in `M`; do not infer an ancient host or collapse ranks. |
| `CallDetail` | `M.locus_id -> DetectionCall -> SourceRecord/SourceArtifact/ProcessRun` | Safe stable provenance only; no `raw_result`, raw payload, or numeric IDs. |
| `PublicAssertionDetail` | `M -> ReleaseAssertionMembership -> ScientificAssertion/ProcessRun/MethodDefinition` | No bare assertions; source confidence remains source-relative. |
| `EvidenceDetail` | Membership-selected supports edge -> `EvidenceItem -> SourceArtifact` | Exactly one selected supporting item per public assertion; use verified artifact SHA/license. |

Nested arrays require deterministic ordering: resolved entities by kind/key, viral lineages by
snapshot/term/role, calls by `call_key`, public assertions by `assertion_key`, and evidence by
`evidence_key`.

## 9. Metric-to-schema mapping

All metrics use the same filtered, distinct, unpaginated `M`; pagination and hydration happen
after the metric universe is fixed.

| Metric | Existing columns/path | Exact distinct unit | Public deduplication label |
|---|---|---|---|
| `distinct_included_locus_count` | `M.release_id, M.locus_id` | One member locus within the exact release. | `release_key+locus_key` |
| `distinct_contig_count` | `M.assembly_id, M.sequence_id` | One exact contig within one exact assembly. | `assembly_accession_version+sequence_accession_version` |
| `distinct_assembly_count` | `M.assembly_id` | One assembly represented by at least one matched public locus. | `assembly_accession_version` |
| `distinct_source_taxon_count` | Direct `AssemblyTaxonAssignment.snapshot_id, term_id` for assemblies in `M` | One direct taxon term in the pinned source-taxonomy snapshot. | `snapshot_key+term_key` |
| `detection_call_count` | `DetectionCall.id` with `DetectionCall.locus_id` in `M` | One method-specific source call associated with a matched public locus. | `release_key+call_key` |

Database numeric IDs may implement `COUNT(DISTINCT ...)` but never become public identity.
Assertions, evidence, aliases, and closure joins must use `EXISTS` or an equivalent deduplicated
shape so they cannot multiply counts.

The following are not public M2 metrics: source-record count, candidate-locus count, quarantine
count, release allowlist assembly count, prevalence, percentage, ratio, frequency, screened
negative, biological absence, or integration-event count.

## 10. Candidate and audit objects excluded from the public root

| Existing object | Purpose | Public M2 rule |
|---|---|---|
| `ReleaseAssemblyMembership` | Release allowlist/pilot scope. | Never enough to list/count an assembly. |
| bare `EVELocus` / placement | Candidate identity and optional coordinate evidence. | Never enough to return a locus or coordinate. |
| `SourceRecord.raw_payload` | Exact imported source row. | Never returned. |
| `DetectionCall.raw_result` | Source/method result payload. | Never returned. |
| `ImportLedger` | One terminal import outcome per source row/run. | Audit-only; `normalized_candidate` is not public inclusion. |
| `QuarantineIssue` | Structured issue for a quarantine ledger row. | Audit-only; issue status does not create membership or biological absence. |
| `SourceAssessment` | Source-relative HCVR label/confidence. | May appear only through a public member assertion; not a public filter in Draft B. |
| bare `ScientificAssertion` / `EvidenceItem` / `AssertionEvidence` | Versioned source claims and evidence graph. | Public only through `ReleaseAssertionMembership` and its selected supports edge. |
| `FlankAssessment` / `InclusionDecision` | Inputs to membership eligibility. | Enforced through membership FKs; not standalone public-query roots. |

## 11. Migration-level invariants relevant to M2

| Revision | Relevant final behavior |
|---|---|
| `0001_empty_baseline` | Empty Milestone 0 baseline. |
| `0002_milestone_1_truth_layer` | Creates the first truth schema, exact placement bounds trigger, quarantine-issue completeness triggers, public locus membership gates, and initial release immutability. |
| `0003_m1_assertion_evidence` | Adds versioned methods/runs, evidence, scientific assertions, assertion membership, global append-only guards, and complete published-release scoped immutability coverage. |
| `0004_m1_shared_intervals` | Makes the exact-interval index non-unique so different source-occurrence loci may share an interval. |
| `0005_m1_fail_closed_publication` | Binds ledger call/locus outcomes to the same source record, permits method-specific calls per process run, makes method artifact linkage nullable, rejects unreceipted legacy public states, and disables release promotion pending a trusted receipt workflow. |

M2 repository correctness depends on these trigger-level invariants as well as ORM foreign keys.
No query implementation may disable triggers, change `session_replication_role`, mutate the pilot
status, or manufacture a published release.

## 12. Current gaps

The following are real missing capabilities, not values that M2.0/M2.1 may infer:

1. no trusted immutable validation-receipt object or workflow;
2. no persistent binding from a receipt to the exact manifest/dependency graph;
3. no published DatasetRelease and therefore no production `QueryableRelease` success path;
4. no independent left/right flank rows, inclusion decisions, or public locus/assertion rows for
   the pilot;
5. no complete frozen NCBI Taxonomy dump with merged/deleted history;
6. no required formal ICTV MSL41/VMR snapshot binding for a public release;
7. no closure-completeness field or receipt attestation; current closures are self-only;
8. no scientific query endpoint, resolver, planner, compiler, repository, or M2 CLI;
9. no demonstrated need for additional query indexes; Draft B authorizes no migration.

These gaps do not authorize weakening the publication boundary. M2.1 may define strict schemas
and tests, and later authorized mechanics may use tests-only constrained fixtures, but production
fact retrieval remains closed.

## 13. Preconditions before M2.2

### 13.1 Required to begin M2.2 implementation

M2.2 is not currently authorized. Before starting it, all of the following are required:

1. a new explicit user instruction authorizing M2.2;
2. this M2.0 mapping reviewed against the then-current ORM and Alembic head, with no unexplained
   schema drift;
3. M2.1 strict QueryPlan, PlanningAudit, response/error, projection, pagination, and metric schemas
   completed with golden serialization/hash tests;
4. full existing pytest, Ruff, mypy, and Alembic checks green;
5. tests-only fixtures and capability doubles remain under `tests/` and cannot be imported or
   deserialized by production HTTP/CLI code;
6. the production release gate continues to reject the real candidate before entity resolution
   or suggestions.

M2.2 parser/resolver mechanics may later be tested against synthetic membership-scoped data only
after authorization. They must implement exact role-qualified lineage resolution, candidate-safe
suggestion universes, complete condition coverage, and current fail-closed descendant behavior.

### 13.2 Required for production success, but not for schema-only M2.1

The following are separate publication prerequisites and must not be faked merely to test M2:

1. an approved persistent, immutable, checksummed validation-receipt schema and workflow;
2. receipt verification of the exact release manifest and dependency graph;
3. complete frozen NCBI and required ICTV authority packages bound to the release;
4. receipt-attested closure completeness for every role allowed to execute descendant queries;
5. independently supported left/right flanks and explicit inclusion decisions;
6. populated public locus and assertion memberships;
7. a database-authorized transition to `published` followed by immutable end-to-end verification;
8. an approved read-only database role and production service boundary.

Until these exist, a real public query success would be a contract violation even if repository
unit tests pass.

## 14. M2.0 completion statement

This mapping identifies every existing Milestone 1 object needed by the approved M2 query
contract and separates it from missing publication authority. It authorizes no database write,
schema migration, query implementation, or release transition. M2.0 is complete when this file is
reviewed and the only workspace change attributable to M2.0 is this documentation artifact.
