# Scientific question redesign for the RAG-value benchmark

## Outcome

The question workflow now has two explicitly different resources:

| Resource | Purpose | Admission to trusted benchmark |
|---|---|---|
| System-regression questions | Parser, route, SQL compiler, exact identifiers, filters, pagination, release isolation, and refusal regression | Never automatically; these are software fixtures |
| Scientific question templates | Natural questions that EVE researchers may ask across structured, literature, Hybrid, and unsupported families | Only after entity binding, instantiation, human review, Gold annotation, and approval |

The old 64 route-oriented questions are preserved byte-for-byte in
`benchmark/system_regression/rag_value_route_questions_v1.jsonl`. The new 64 scientific templates
live in `benchmark/rag_value_ablation/scientific_questions_template.jsonl`. They are not the same
question resource and must not be merged during execution or reporting.

This redesign did not run a model, retriever, database query, embedding provider, or LLM. It did
not create Gold, Oracle evidence, human labels, results, or approvals.

## Why the old questions are regression-oriented

Questions such as `Count distinct included loci in this release.`, `Show assembly GCA_...`, and
`<structured sentence>. and explain the literature evidence` deliberately mirror the current
controlled-English and router grammar. They are useful because a precise parser fixture should be
mechanical and stable. They are unsuitable as the primary scientific benchmark because a
researcher normally starts from a host, viral lineage, host–virus relationship, assembly, locus,
method, or interpretation limit rather than from an API command.

The frozen regression artifact has:

- 64 rows;
- SHA-256 `9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34`;
- all rows marked `pending`;
- no approval or Gold;
- its original synthetic fixture identifiers retained exactly.

The all-`a` locus identifier remains only in that historical regression fixture. It is prohibited
from the scientific templates.

## Authoring contract and trust transition

`ScientificQuestionTemplate` is intentionally separate from `EvaluationQuestion`.
`EvaluationQuestion` keeps its existing approval rule: only an `approved` record with human
approval and complete family-matched Gold can enter the trusted benchmark. The authoring-only
contract cannot represent approval, Oracle evidence, results, or scores, and fixes
`review_status` to `pending` and `gold` to `null`.

The required transition is:

```text
ScientificQuestionTemplate (pending, placeholders)
  + ScientificEntityBindingsTemplate (pending, empty)
  -> human selects release-scoped entities
  -> deterministic text instantiation with no remaining placeholders
  -> parser/readiness and capacity checks
  -> independent scientific wording review
  -> separate human Gold and Oracle annotation
  -> approved EvaluationQuestion
```

No step may infer labels from a model, current retriever, lexical overlap, or parser acceptance.
Parser acceptance is a software readiness signal only. A placeholder-based template can never be
admitted directly to a trusted question manifest.

## Family versus scientific task

`family` controls which experiment condition and scoring contract applies:

- `structured`: exact facts and sets from a DatasetRelease;
- `literature`: document and passage evidence from a CorpusRelease;
- `hybrid`: structured facts plus literature evidence and interpretation limits;
- `unsupported`: appropriate refusal and prohibited downstream behavior.

`scientific_task` describes the research objective. It is the primary organization of this set:

| Scientific task | Structured | Literature | Hybrid | Unsupported | Total |
|---|---:|---:|---:|---:|---:|
| `host_eve_profile` | 4 | 4 | 4 | 0 | 12 |
| `viral_lineage_distribution` | 4 | 4 | 4 | 0 | 12 |
| `host_virus_relationship` | 4 | 4 | 4 | 0 | 12 |
| `assembly_locus_evidence` | 4 | 4 | 4 | 0 | 12 |
| `unsupported_scientific_or_operational_boundary` | 0 | 0 | 0 | 16 | 16 |
| **Total** | **16** | **16** | **16** | **16** | **64** |

For viral-lineage questions, “distribution” means only the distribution of records inside the
selected DatasetRelease. It does not mean prevalence, modern infection, global biological
distribution, or confirmed host range.

## Entity-binding worksheet

The binding worksheet covers the complete frozen placeholder vocabulary, even though four slots
are reserved and unused in the current 64 templates.

| Slot | Required type | Used in current set |
|---|---|---:|
| `HOST_LINEAGE_A` | source lineage | yes |
| `HOST_SPECIES_A` | source species | yes |
| `HOST_SPECIES_B` | source species | no |
| `VIRAL_LINEAGE_A` | viral lineage | yes |
| `VIRAL_LINEAGE_B` | viral lineage | no |
| `EXTENDED_LINEAGE_A` | extended viral lineage | yes |
| `ASSEMBLY_A` | assembly | yes |
| `ASSEMBLY_B` | assembly | no |
| `LOCUS_A` | locus | yes |
| `LOCUS_B` | locus | no |
| `LOCUS_C` | locus | yes |

Every binding starts with null stable key, display name, release identity, snapshot identity,
lineage role, and descendant policy. Those extra lineage fields are necessary because a name alone
cannot deterministically choose formal, study, or extended lineage semantics. The worksheet is
checksum-bound and pending; it is not an approved binding manifest.

## Capability-status distribution

Natural wording is preserved even when the current system cannot execute it. Nothing was rewritten
into `Show/List/Count` syntax merely to claim support.

| Capability status | Templates | Meaning |
|---|---:|---|
| `requires_natural_structured_planning` | 10 | Matching backend operation exists, but the natural structured entry is absent |
| `requires_natural_literature_routing` | 16 | Literature retrieval accepts question text, but the router requires a mechanical prefix |
| `requires_new_intent` | 2 | `list_viral_lineages` is not a current QueryPlan intent |
| `requires_composite_plan` | 3 | The question needs multiple existing operations and a validated multi-result envelope |
| `requires_natural_hybrid_decomposition` | 16 | One natural question must be split into typed structured and literature needs |
| `future_only` | 1 | The supplied wording is not self-contained under the no-memory benchmark policy |
| `unsupported_by_design` | 16 | The requested inference or operation must be refused |
| `supported_now` | 0 | No natural template is silently treated as executable |

The two natural-entry statuses intentionally extend the suggested status vocabulary. They keep a
missing parser/router entry distinct from a missing typed backend intent. A status records the
primary gap only; `required_capabilities` and the detailed mapping retain secondary gaps such as a
safe methods/limitations context, a composite result, or missing release data.

The detailed per-question analysis is in
[`scientific_question_capability_gap.md`](scientific_question_capability_gap.md).

## Human-readable question set

All wording below is exact. Every item remains pending.

### Host-centred EVE profile

Structured:

- `HOST-S-01` — What included EVE loci are recorded in assemblies assigned to {HOST_SPECIES_A}?
- `HOST-S-02` — Which assemblies assigned to {HOST_LINEAGE_A} contain included EVE loci?
- `HOST-S-03` — Which viral lineages are represented among included EVE loci in {HOST_LINEAGE_A}?
- `HOST-S-04` — What is the EVE profile of {HOST_SPECIES_A} in the selected release?

Literature:

- `HOST-L-01` — How did the source studies identify EVE candidates reported for {HOST_LINEAGE_A}?
- `HOST-L-02` — What evidence did the literature use to support the endogenous origin of EVE records in {HOST_LINEAGE_A}?
- `HOST-L-03` — What limitations do the source studies discuss when interpreting EVE records in {HOST_LINEAGE_A}?
- `HOST-L-04` — How did the source literature classify the viral origins of EVE records reported in {HOST_LINEAGE_A}?

Hybrid:

- `HOST-H-01` — What EVE loci are recorded in {HOST_SPECIES_A}, and what evidence supports their endogenous origin?
- `HOST-H-02` — Which viral lineages are represented among EVE loci in {HOST_LINEAGE_A}, and how were those assignments made?
- `HOST-H-03` — Which assemblies assigned to {HOST_LINEAGE_A} contain EVE loci, and what assembly-related limitations apply to those records?
- `HOST-H-04` — What is the EVE profile of {HOST_SPECIES_A}, and how should those records be interpreted according to the source literature?

### Viral-lineage distribution

Structured:

- `VIRUS-S-01` — In which assembly-source taxa are {VIRAL_LINEAGE_A}-related EVE loci recorded?
- `VIRUS-S-02` — Which assemblies contain included EVE loci with affinity to {VIRAL_LINEAGE_A}?
- `VIRUS-S-03` — How many distinct source taxa, assemblies, and EVE loci are represented for {VIRAL_LINEAGE_A}?
- `VIRUS-S-04` — Which exact EVE loci have supported affinity to {VIRAL_LINEAGE_A}?

Literature:

- `VIRUS-L-01` — What evidence does the literature use to assign reported regions to {VIRAL_LINEAGE_A}?
- `VIRUS-L-02` — How do the source studies distinguish {VIRAL_LINEAGE_A}-related signals from false-positive protein similarities?
- `VIRUS-L-03` — What taxonomic uncertainty or naming limitations apply to {VIRAL_LINEAGE_A} assignments?
- `VIRUS-L-04` — What limitations affect interpretation of the recorded host distribution of {VIRAL_LINEAGE_A}-related EVE records?

Hybrid:

- `VIRUS-H-01` — In which source taxa are {VIRAL_LINEAGE_A}-related EVE loci recorded, and how were those loci assigned to this viral lineage?
- `VIRUS-H-02` — Which assemblies contain {VIRAL_LINEAGE_A}-related EVE loci, and what evidence supports their endogenous status?
- `VIRUS-H-03` — What is the recorded distribution of {VIRAL_LINEAGE_A}-related EVE loci, and what limitations apply to interpreting that distribution?
- `VIRUS-H-04` — Which exact loci have affinity to {EXTENDED_LINEAGE_A}, and what evidence supports the use of this extended-lineage label?

### Host-lineage × viral-lineage relationship

Structured:

- `REL-S-01` — Which {HOST_LINEAGE_A} assemblies contain {VIRAL_LINEAGE_A}-related EVE loci?
- `REL-S-02` — Which EVE loci support the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?
- `REL-S-03` — How many distinct loci and assemblies support the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?
- `REL-S-04` — What are the exact coordinates and assembly-source taxa of the loci supporting this association?

Literature:

- `REL-L-01` — How did the source studies detect candidate {VIRAL_LINEAGE_A}-related regions in {HOST_LINEAGE_A} assemblies?
- `REL-L-02` — What evidence supports the endogenous origin of {VIRAL_LINEAGE_A}-related regions reported from {HOST_LINEAGE_A} assemblies?
- `REL-L-03` — What alternative explanations or uncertainties could affect the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}?
- `REL-L-04` — Why does the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A} not by itself demonstrate modern infection or host–virus co-divergence?

Hybrid:

- `REL-H-01` — Which {HOST_LINEAGE_A} assemblies contain {VIRAL_LINEAGE_A}-related EVE loci, and how were those loci detected?
- `REL-H-02` — How many EVE loci support the recorded {HOST_LINEAGE_A}–{VIRAL_LINEAGE_A} association, and why should this count not be interpreted as the number of independent integration events?
- `REL-H-03` — Which records support the {HOST_LINEAGE_A}–{VIRAL_LINEAGE_A} association, and what evidence supports their viral-lineage assignments?
- `REL-H-04` — Summarize the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, including the exact records, supporting literature, and major interpretation limits.

`REL-S-04` is retained exactly as requested but is marked `future_only`: “this association” is not
self-contained, while the benchmark prohibits conversation memory. It must be revised or explicitly
instantiated during human authoring before approval.

### Assembly and locus evidence

Structured:

- `RECORD-S-01` — What EVE loci are recorded in assembly {ASSEMBLY_A}?
- `RECORD-S-02` — Show the exact genomic location, detection calls, and public assertions for locus {LOCUS_A}.
- `RECORD-S-03` — Which viral-lineage affinities are represented among the EVE loci in assembly {ASSEMBLY_A}?
- `RECORD-S-04` — Which structured evidence items and source locators are linked to locus {LOCUS_A}?

Literature:

- `RECORD-L-01` — How did the source study define the detection criteria used for records such as locus {LOCUS_A}?
- `RECORD-L-02` — What methods were used to evaluate host-genomic flanks around reported EVE loci?
- `RECORD-L-03` — What assembly or contig limitations could affect interpretation of locus {LOCUS_A}?
- `RECORD-L-04` — What evidence did the source literature use to distinguish integrated regions from viral contigs?

Hybrid:

- `RECORD-H-01` — Show locus {LOCUS_A} and explain what evidence supports its endogenous origin.
- `RECORD-H-02` — What EVE profile is recorded for assembly {ASSEMBLY_A}, and how were those records identified?
- `RECORD-H-03` — Why was locus {LOCUS_A} included in this release, and what literature supports its viral-lineage assignment?
- `RECORD-H-04` — What uncertainties remain for locus {LOCUS_C}, considering both its structured assertions and the limitations described in the literature?

### Unsupported scientific or operational boundary

- `UNSUP-01` — Which host lineage has the highest prevalence of {VIRAL_LINEAGE_A}-related EVEs?
- `UNSUP-02` — Which species definitely lacks {VIRAL_LINEAGE_A}-related EVEs?
- `UNSUP-03` — Prove that {VIRAL_LINEAGE_A} infected modern members of {HOST_LINEAGE_A}.
- `UNSUP-04` — Do the recorded EVE loci represent the same number of independent integration events?
- `UNSUP-05` — Prove host–virus co-divergence between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}.
- `UNSUP-06` — Determine the exact date when locus {LOCUS_A} integrated into the host genome.
- `UNSUP-07` — Is locus {LOCUS_A} transcriptionally active?
- `UNSUP-08` — Does locus {LOCUS_A} provide an adaptive function to its host?
- `UNSUP-09` — Which host lineage is most susceptible to infection by {VIRAL_LINEAGE_A}?
- `UNSUP-10` — Which screened-negative species contain no endogenous viral elements?
- `UNSUP-11` — Run HMMER or BLAST on this new sequence and identify previously unknown EVEs.
- `UNSUP-12` — Build a phylogenetic tree and place locus {LOCUS_A} within it.
- `UNSUP-13` — Search the live web for additional evidence that is not present in the approved corpus.
- `UNSUP-14` — Estimate the global natural distribution of {VIRAL_LINEAGE_A}-related EVEs from this pilot release.
- `UNSUP-15` — Execute an arbitrary SQL query across all database tables.
- `UNSUP-16` — Determine with certainty the ancestral host species in which locus {LOCUS_A} first originated.

## Stop condition

This change stops at pending authoring templates and capability analysis. The next step is not
benchmark execution. Entity selection, question instantiation, wording approval, Gold, Oracle
evidence, and any implementation of missing intents require separate explicit work and review.
