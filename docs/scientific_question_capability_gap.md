# Scientific question capability-gap analysis

## Scope and result

This is a static audit of the 64 pending association templates against the current router,
controlled-English parser, QueryPlan union, structured result graph, literature path, Hybrid
orchestration, and production `ContextPack`. No model, retriever, database, release, corpus,
embedding provider, or LLM was executed.

The 48 answerable templates all have status `requires_relation_contract`. Their common target is a
typed association tuple:

```text
source taxonomic unit
  -> represented source species
  -> assembly
  -> locus or source-reported region
  -> Transferred gene | Integrated virus
  -> viral lineage (role + snapshot + exact/descendant scope)
```

The current repository has useful partial projections, but it has no approved `Transferred gene`
or `Integrated virus` relation contract. Therefore none of the 48 may be marked `supported_now`,
even where `list_loci`, `list_assemblies`, `list_source_taxa`, or `locus_detail` can supply the other
fields. The other 16 questions are `unsupported_by_design`.

All 64 records remain `pending`, with no Gold, Oracle evidence, result, or approval.

## Three truth domains

The family output boundary is strict:

| Family | Association output | Additional reviewed fields |
|---|---|---|
| Structured | `exact_association_set` plus applicable `exact_*` projections | `required_limitations`, `forbidden_claims` |
| Literature | `source_reported_association_set`; never an `exact_*` structured set | `required_documents`, `required_evidence_groups`, `required_limitations`, `forbidden_claims` |
| Hybrid | both source-specific sets plus `cross_source_association_set`; applicable `exact_*` fields describe only its structured side | document/evidence and safety fields |

A literature-only question may use only what the permitted CorpusRelease explicitly reports. It
must not access a DatasetRelease to restrict species or assemblies, resolve an internal locus key,
or import a structured relation label. Hybrid may align the two sets, but it must preserve
structured-only, literature-only, both, ambiguous, and unmatched outcomes rather than forcing a
match.

## Non-negotiable semantic boundaries

### Relation class

The current source assertions are `hcvr`, `viral_major_taxon`, and `vr_type`. They do not define the
requested relation ontology:

- `VR Type = Integration` must not be mapped to `Integrated virus`;
- `VR Type = Viral contig` must not be mapped to `Transferred gene`; and
- `HCVR`, `source_high`, and `source_low` must not be mapped to either class.

Phase 1 needs a strict, versioned relation contract and separately approved assertions or mapping
policy. The contract must record provenance, distinguish structured assertions from source-reported
literature wording, permit explicit unknown/unmapped values, and reject silent coercion.

### Source taxonomy

`LocusSummary.source_taxon` and `AssemblySummary.source_taxon` are explicitly
`assembly_source_taxonomy`. The structured questions therefore say “represented source species” or
“assembly-source taxonomic units.” This is the set represented by public membership in the exact
selected release, not every biological descendant of a taxonomic unit and not an ancient or modern
host claim.

Literature questions retain the host wording used by the permitted sources. Those source-reported
associations do not assert DatasetRelease membership. Hybrid keeps literature wording separate
from the release-bound assembly-source projection.

### Viral lineage

Every viral lineage must retain its role (`study_viral_lineage`, `formal_viral_taxonomy`, or an
approved `extended_viral_lineage`), snapshot and authority, and exact-versus-descendant semantics.
Names from different roles must not be merged. `LocusSummary.viral_lineages` already carries
role-qualified `LineageRef` values, so a future exhaustive relation projection should reuse those
values instead of inventing a separate unqualified lineage list.

### Current candidate diversity

The currently inspected candidate cohort contains only the source label `Integration` and the
study-defined viral lineage `Orthopolintovirales`. It therefore has no relation-class or viral-
lineage diversity with which to measure the requested discrimination. It is also candidate-only,
not a public benchmark release. A trusted run needs an approved release and corpus whose human-
reviewed questions actually exercise both relation classes and multiple role-qualified viral
lineages; otherwise the affected metrics are ineligible, not zero.

## Existing seams and missing capabilities

The controlled-English planner currently emits exactly six intents: `assembly_detail`,
`locus_detail`, `list_loci`, `list_assemblies`, `list_source_taxa`, and one `aggregate`. Existing
contracts can be reused as follows:

- `LocusSummary` supplies release-bound source species, assembly, locus, placement, and
  role-qualified viral lineages;
- `LocusDetailData.public_assertions` can expose the source `vr_type`, but only as a source
  assertion and never as either requested relation class;
- `list_loci`, `list_assemblies`, and `list_source_taxa` provide useful filtered, paginated
  projections; and
- `QuerySuccess` and `StructuredResult` remain immutable release-bound truth objects for S4/S5.

The production router accepts the mechanical structured grammar, three fixed literature prefixes,
and one controlled structured sentence plus a fixed Hybrid suffix. The exact 48 natural answerable
templates are therefore not executable unchanged through the current route. This is recorded as a
secondary natural planning/routing/decomposition gap; it does not justify changing their wording
or broadening the production router.

The minimum missing experiment capabilities are:

1. `relation_contract` and `relation_class_assertion`;
2. a canonical `association_projection` over the complete tuple;
3. `source_taxonomy_projection` with release-represented-species semantics;
4. complete cursor traversal and deterministic deduplication for set-valued relations;
5. natural structured planning into existing operations or a validated composite plan;
6. literature association extraction and entity normalization that preserve source wording;
7. natural literature routing without hidden structured dependencies;
8. natural Hybrid decomposition and immutable multi-result packaging; and
9. cross-source alignment with explicit provenance and unmatched outcomes.

The current production `ContextPack` must remain unchanged. It is useful as provenance for the
existing mechanical S3/S5 path, but it accepts only current literature/Hybrid shapes, one
`QueryPlan` and one `StructuredResult`, and the fixed Hybrid suffix. The association templates need
the experiment-only evidence envelope and, for composite structured needs, an immutable multi-plan/
multi-result envelope. This is an adapter around existing contracts, not a parallel production RAG
architecture.

Every complete set also needs exhaustive pagination. A first page, the 64-anchor ceiling, or the
eight-chunk production context limit must never be presented as a complete association universe.

## Structured templates: every-question mapping

All rows require approved entity bindings, an approved DatasetRelease, the relation contract,
release-represented source scope, role/snapshot/scope preservation, and natural planning. Existing
operations below are reusable seams, not proof that the complete question is currently supported.

| ID | Requested exact projection | Reusable structured seam | Remaining question-specific gap |
|---|---|---|---|
| HOST-S-01 | represented source species by class and viral lineage | exhaustive `list_loci(source_lineage)` exposes each direct source taxon | species-level association projection and class/lineage grouping |
| HOST-S-02 | species -> assemblies by class and viral lineage | `list_assemblies`; filtered loci | class/lineage columns and represented-species join |
| HOST-S-03 | species -> viral lineages by class | exhaustive `list_loci` exposes `viral_lineages` | relation-class column and deterministic grouping/deduplication |
| HOST-S-04 | complete species/assembly/locus/class/lineage tuples | `list_loci` plus `list_assemblies` | composite result envelope and complete association projection |
| VIRUS-S-01 | assembly-source taxonomic units by class | `list_source_taxa(viral_lineage)` | relation-class split and exact role/scope binding |
| VIRUS-S-02 | represented source species by class | `list_source_taxa` and filtered loci | species projection and relation-class split |
| VIRUS-S-03 | species -> assemblies by class | `list_assemblies(viral_lineage)` | represented-species join and relation-class split |
| VIRUS-S-04 | species/assembly/loci by class | exhaustive `list_loci(viral_lineage)` | relation-class projection and complete pagination |
| REL-S-01 | source species for one source-lineage x viral-lineage scope by class | exhaustive `list_loci(source_lineage AND viral_lineage)` can derive represented species | typed combined association projection |
| REL-S-02 | species -> assemblies within the combined scope by class | `list_assemblies(source_lineage AND viral_lineage)` | relation-class split and species-preserving output |
| REL-S-03 | species/assembly/loci within the combined scope by class | `list_loci(source_lineage AND viral_lineage)` | relation-class projection and complete pagination |
| REL-S-04 | full tuples compared across two viral lineages | one filtered locus/assembly plan per lineage | validated multi-plan comparison envelope |
| RECORD-S-01 | assembly loci by class and lineage | `list_loci(assembly)` | relation-class projection and exhaustive grouping |
| RECORD-S-02 | one locus's source species/assembly/class/lineage | `locus_detail` plus immutable `LocusSummary` | approved relation-class assertion distinct from source `vr_type` |
| RECORD-S-03 | assembly loci for one role-qualified viral lineage by class | `list_loci(assembly AND viral_lineage)` | relation-class split |
| RECORD-S-04 | three locus association tuples | one `locus_detail` per locus | validated multi-result envelope and relation-class assertions |

## Literature templates: every-question mapping

All rows require an approved CorpusRelease, natural literature routing, manually approved document
and evidence groups, source-reported relation-class annotation, entity normalization, limitations,
and forbidden claims. No row may emit an `exact_*` structured projection or depend on release
membership.

| ID | Requested source-reported association | Main retrieval/annotation requirement |
|---|---|---|
| HOST-L-01 | host species by class and viral lineage within the named lineage | explicit source statements; normalize without converting to assembly-source truth |
| HOST-L-02 | host species -> named assemblies by class and lineage | assembly discoverability plus source passage provenance |
| HOST-L-03 | host species -> viral lineages by class | role-qualified lineage normalization without cross-role merging |
| HOST-L-04 | complete reported host/assembly-or-region/class/lineage tuples | preserve missing fields and named regions exactly as reported |
| VIRUS-L-01 | reported host taxonomic units for one lineage by class | source host wording and exact lineage role/scope binding |
| VIRUS-L-02 | reported host species for one lineage by class | species normalization inside the corpus only |
| VIRUS-L-03 | reported host species -> assemblies for one lineage by class | assembly identifiers must be present or manually normalized from the source |
| VIRUS-L-04 | named loci or regions for one lineage by class | named regions remain literature entities, not structured locus keys |
| REL-L-01 | species for one host-lineage x viral-lineage relation by class | both lineage bindings plus source-statement evidence |
| REL-L-02 | species -> assemblies for the paired lineage scope by class | assembly discoverability and explicit association passages |
| REL-L-03 | named loci/regions for the paired lineage scope by class | no inferred structured-locus equivalence |
| REL-L-04 | compare reported tuples across two viral lineages | two role-qualified lineage scopes and source-specific tuple comparison |
| RECORD-L-01 | named regions in one assembly by class and lineage | assembly and region discoverability; no internal locus injection |
| RECORD-L-02 | host species and lineages for named regions in one assembly by class | assembly-bound literature search; no prebound structured locus |
| RECORD-L-03 | named regions for one literature host species by class/assembly/lineage | source-host normalization and explicit reported associations |
| RECORD-L-04 | regions in one assembly for one lineage by class | assembly/lineage discoverability and source passage provenance |

## Hybrid templates: every-question mapping

All rows require an approved DatasetRelease/CorpusRelease pair, the structured relation contract,
natural decomposition, curated anchors where available, and deterministic cross-source alignment.
The structured set remains immutable; a literature label never overwrites it.

| ID | Structured seam | Cross-source requirement beyond the common contract |
|---|---|---|
| HOST-H-01 | source-taxon/filtered-locus projection | align represented source species by class and lineage with source-reported host species |
| HOST-H-02 | `list_assemblies(source_lineage)` | align species/assembly/class/lineage tuples without requiring literature release membership |
| HOST-H-03 | exhaustive filtered `list_loci` | align role-qualified lineage sets per represented species and class |
| HOST-H-04 | composite loci plus assemblies | classify complete tuples as structured-only, literature-only, both, or unmatched |
| VIRUS-H-01 | `list_source_taxa(viral_lineage)` | align assembly-source taxa with literature host wording for the bound lineage |
| VIRUS-H-02 | source-taxon/filtered-locus projection | align represented source species while preserving source-specific identities |
| VIRUS-H-03 | `list_assemblies(viral_lineage)` | align species/assembly/class tuples and keep absent literature identifiers explicit |
| VIRUS-H-04 | exhaustive `list_loci(viral_lineage)` | match only manually/curated-identifiable regions; retain unmatched loci |
| REL-H-01 | combined source-lineage/viral-lineage projection | align represented species with literature-reported species by class |
| REL-H-02 | `list_assemblies` under both lineage filters | align species/assembly/class relations without a broad fallback |
| REL-H-03 | exhaustive `list_loci` under both filters | align named literature regions only through approved identities/anchors |
| REL-H-04 | separate composite plans for two viral lineages | compare full cross-source tuples without treating cross-source occurrence as exact identity |
| RECORD-H-01 | `locus_detail` | approved locus-to-literature anchor and field-wise source-preserving alignment |
| RECORD-H-02 | exhaustive `list_loci(assembly)` | complete locus/class/lineage alignment with capacity preflight |
| RECORD-H-03 | exhaustive `list_loci(assembly)` | retain structured-only, literature-only, both, ambiguous, and unmatched tuples |
| RECORD-H-04 | one complete assembly projection per assembly | validated multi-result comparison plus cross-source presence state |

## Unsupported templates: every-question mapping

Boundary labels below are analysis suggestions, not Gold labels or approvals. Human review must
later define the exact refusal category, required explanation, forbidden claims, and prohibited
downstream stages. A future natural router must preserve refusal before any prohibited action.

| ID | Boundary being tested | Required behavior |
|---|---|---|
| UNSUP-01 | prevalence is not established by record counts | refuse prevalence ranking; no broad query |
| UNSUP-02 | absence from a release/corpus is not biological absence | refuse definite-absence claim |
| UNSUP-03 | an EVE association does not establish modern infection | refuse infection inference |
| UNSUP-04 | loci are not independently established integration events | refuse one-locus/one-event conversion |
| UNSUP-05 | matching records do not establish host-virus co-divergence | refuse co-divergence inference |
| UNSUP-06 | requested relation classes are unapproved | refuse classification until an approved relation contract exists |
| UNSUP-07 | unsafe `Integration`/`Viral contig` mapping | refuse both mappings and return no derived association set |
| UNSUP-08 | unsafe `HCVR` mapping | refuse confidence-to-relation conversion |
| UNSUP-09 | viral-lineage role conflation | refuse merging study, formal, and extended roles |
| UNSUP-10 | name similarity is not a lineage assertion | refuse lexical lineage assignment |
| UNSUP-11 | one represented species cannot be extrapolated to every descendant | refuse biological descendant expansion |
| UNSUP-12 | unapproved/unversioned release and corpus mixing | refuse cross-release merge |
| UNSUP-13 | a first/truncated page is not a complete set | refuse completeness claim; do not silently truncate |
| UNSUP-14 | live web lies outside the approved corpus | refuse before network or retrieval construction |
| UNSUP-15 | BLAST/HMMER and release mutation are outside scope | refuse before tool execution and publication/mutation |
| UNSUP-16 | arbitrary SQL bypasses the fixed QueryPlan/compiler boundary | refuse before database execution |

The current fail-closed router already rejects many of these strings as unknown syntax, and has
explicit patterns for several biological/operational hazards. Unknown-syntax rejection is not a
durable semantic classifier. Phase 1 should add explicit boundary tests before any broader natural
router is admitted.

## Data and approval blockers

Before any answerable template can enter a trusted run, all of the following are required:

1. an exact approved DatasetRelease for structured/Hybrid questions and an exact approved
   CorpusRelease for literature/Hybrid questions;
2. a versioned `Transferred gene`/`Integrated virus` contract plus independently reviewed class
   assertions or mapping policy;
3. enough approved category and viral-lineage diversity to make the intended comparison eligible;
4. human-selected bindings with stable keys, display names, release/snapshot identity, lineage
   role, and exact/descendant scope;
5. complete pagination, anchor, and context-capacity preflight;
6. deterministic instantiation with no placeholders;
7. independent wording review;
8. human-authored family-specific Gold, including required limitations and forbidden claims;
9. separately manually approved Oracle evidence; and
10. the existing approved-only trusted question-manifest admission gate.

## Proposed Phase 1 additions

This analysis proposes experiment-only contracts and tests, not production activation:

1. add the relation ontology/assertion contract with explicit unknown/unmapped outcomes;
2. add the canonical association tuple and family-separated Gold sets;
3. add a release-represented source-species projection using existing immutable structured values;
4. add exhaustive pagination and deterministic association deduplication;
5. add typed natural structured and literature routing adapters;
6. add literature association extraction/normalization annotations without structured leakage;
7. add an immutable composite structured envelope where multiple existing plans are required;
8. add cross-source identity/alignment records with explicit unmatched and ambiguous states;
9. add natural Hybrid decomposition into structured and literature needs; and
10. add paired positive/negative tests for all 16 refusal boundaries.

Production defaults, parser, QueryPlan union, SQL compiler, database schema, releases, corpora,
providers, embeddings, S0-S6 definitions, and `ContextPack` remain unchanged.
