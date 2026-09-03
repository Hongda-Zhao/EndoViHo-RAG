# Scientific question capability-gap analysis

## Scope and result

This is a static, read-only audit of the current router, controlled-English parser, QueryPlan
union, structured compiler/repository/results, literature invocation, Hybrid orchestration, and
ContextPack. No model, retriever, database, release, corpus, embedding, or provider was executed.

None of the 48 answerable natural-language templates is executable end-to-end exactly as written.
This does not mean that all backend operations are absent:

- 10 structured templates need only a natural structured-planning entry before reaching an
  existing single QueryPlan operation;
- 3 structured templates require deterministic composition of multiple existing operations;
- 2 structured templates require a new `list_viral_lineages` intent;
- 1 structured template has an existing backend projection but is not self-contained;
- all 16 literature templates can use the existing retrieval request, but all need a natural
  literature routing boundary, two also need a typed safe methods/limitations context, and one of
  those two additionally needs an explicit Unicode input decision;
- all 16 Hybrid templates require natural typed decomposition, and some additionally require a new
  intent, a composite plan, or new public evidence projection;
- all 16 unsupported templates must remain refused by design.

The current local data state is a separate blocker. `docs/data_semantics.md` states that the pilot
has no public locus memberships, flank assessments, or inclusion decisions and that there is no
public EVE release. The repository also does not provide an approved real CorpusRelease, entity
bindings, Gold, or Oracle evidence. Therefore no trusted scientific result can be produced even
where a backend query shape already exists.

## Status and dependency legend

Statuses:

| Code | Template status | Meaning |
|---|---|---|
| NS | `requires_natural_structured_planning` | Backend operation exists; natural structured wording is not parsed |
| NL | `requires_natural_literature_routing` | Retriever accepts text; router rejects the natural form |
| NI | `requires_new_intent` | Required typed structured result is absent |
| CP | `requires_composite_plan` | Multiple operations/results must be coordinated and validated |
| NH | `requires_natural_hybrid_decomposition` | Natural question must become typed structured and literature needs |
| FO | `future_only` | A prior authoring or data condition prevents execution |
| UB | `unsupported_by_design` | Request must be refused before prohibited downstream work |

Data and readiness dependencies:

| Code | Dependency |
|---|---|
| B | Human-approved placeholder binding with stable key, display name, release, role, snapshot, and scope |
| R | Exact published, gate-authorized DatasetRelease and manifest |
| T | Release-bound source taxonomy assignments and any required complete lineage closure |
| V | Role-qualified viral-lineage assertions, snapshots, and any required closure |
| M | Public locus membership in the selected DatasetRelease |
| D | Public detail projection: placement, calls, assertions, and membership-selected evidence |
| F | Approved flank assessments and inclusion-decision provenance when the question needs an endogeneity or inclusion rationale |
| C | Approved CorpusRelease, relevant document/chunk bytes, and later human Gold |
| H | Approved DatasetRelease–CorpusRelease binding and curated structured anchors |
| X | Exhaustive cursor traversal, deduplication, and capacity preflight |

Every set-valued structured question needs `X`: a single page is limited to at most 100 records and
cannot be scored as the complete set until pagination reaches the end. Hybrid retrieval also has a
64-anchor cap and an eight-chunk ContextPack cap; broad entity bindings must be preflighted and must
fail closed rather than truncate silently.

## Current parser and orchestration boundaries

The structured router recognizes only questions beginning with `Show`, `List`, or `Count`. The
controlled-English parser has six intents:

- `assembly_detail`;
- `locus_detail`;
- `list_loci`;
- `list_assemblies`;
- `list_source_taxa`;
- `aggregate`.

It can already combine one source-lineage filter and one role-qualified viral-lineage filter with
AND, and the compiler applies both over release membership. It cannot emit
`list_viral_lineages`, a profile bundle, or more than one aggregate result per QueryPlan.

The literature router recognizes only three fixed `Explain the literature ...` prefixes. The
retrieval contract itself accepts a general question string, so these 16 questions do not need a
new retrieval algorithm merely to enter the literature route.

The Hybrid router recognizes only one controlled structured sentence followed by one fixed
literature suffix. `RagQueryApplication` executes one structured request, produces one
`QuerySuccess`, resolves anchors once, and builds a ContextPack containing one QueryPlan and one
StructuredResult. The production ContextPack cannot directly carry these natural Hybrid templates,
even when a single structured operation would suffice: its validator requires the original Hybrid
question to equal the structured question followed by one fixed mechanical suffix. It also uses the
printable-ASCII routed-question contract. Preserve ContextPack unchanged as provenance for existing
mechanical production routes; natural scientific execution needs the experiment-specific
`EvaluationEvidencePack` already specified by the RAG-value design. Composite profiles additionally
need an immutable multi-plan/multi-result envelope. Unvalidated text concatenation is not acceptable.

The current routed request contract also permits printable ASCII only. The exact requested wording
uses an en dash in `REL-L-04`, `REL-H-02`, `REL-H-03`, and `UNSUP-05`, so those records
currently fail request validation before route selection. The templates preserve the requested
scientific wording and record this as a future input-contract decision.

## Required future capability shapes

These are capability requirements, not implemented intents or approved questions:

- `list_viral_lineages`: return a release-pinned, role-qualified viral-lineage set under an exact
  structured scope, with explicit exact-versus-descendant semantics and exhaustive pagination.
- `host_eve_profile`: compose matching loci, assemblies, viral lineages, and exact aggregate counts
  for one bound host species or lineage.
- `viral_lineage_distribution`: compose matching source taxa, assemblies, loci, and exact aggregate
  counts for one role-qualified viral lineage. “Distribution” remains limited to records in the
  selected release.
- `host_virus_relationship`: apply one source-lineage filter and one viral-lineage filter together,
  then expose matching loci, assemblies, public assertions, and exact counts without treating the
  relationship as modern infection or co-divergence.
- natural Hybrid decomposition: map one unchanged researcher question to typed structured needs and
  typed literature-evidence needs, without requiring a mechanical suffix and without falling back
  after a structured refusal.

The named profile capabilities may be deterministic compositions rather than new single SQL
queries. Either design still needs strict result typing, checksum binding, capacity checks, and a
validated multi-result envelope.

## Structured templates: every-question mapping

All structured rows require B and R. All are rejected by the natural entry as written.

| ID | Status | Existing or required typed operations | Parser/semantic issue | Additional data |
|---|---|---|---|---|
| HOST-S-01 | NS | Existing `list_loci(source_lineage=species, exact)` | Natural `What` form is outside grammar | T/M/X |
| HOST-S-02 | NS | Existing `list_assemblies(source_lineage, descendants)` | Natural `Which` form is outside grammar | T/M/X |
| HOST-S-03 | NI | New `list_viral_lineages(source_lineage)`; exhaustive loci-plus-dedup is only a composite alternative | No result intent for viral-lineage set | T/V/M/X |
| HOST-S-04 | CP | `host_eve_profile`: loci, assemblies, viral lineages, and counts | Natural profile intent and multi-result envelope absent | T/V/M/X |
| VIRUS-S-01 | NS | Existing `list_source_taxa(viral_lineage)` | Natural `In which` form is outside grammar | T/V/M/X |
| VIRUS-S-02 | NS | Existing `list_assemblies(viral_lineage)` | Natural `Which` form is outside grammar | T/V/M/X |
| VIRUS-S-03 | CP | Three existing aggregates: distinct source taxa, assemblies, and loci | One QueryPlan carries exactly one metric | T/V/M |
| VIRUS-S-04 | NS | Existing `list_loci(viral_lineage)` | Natural `Which` form is outside grammar; “affinity” needs role/scope binding | T/V/M/X |
| REL-S-01 | NS | Existing `list_assemblies(source_lineage AND viral_lineage)` | Natural relationship form is outside grammar | T/V/M/X |
| REL-S-02 | NS | Existing `list_loci(source_lineage AND viral_lineage)` | Natural relationship form is outside grammar | T/V/M/X |
| REL-S-03 | CP | Two existing aggregates: distinct loci and assemblies under both filters | One QueryPlan carries exactly one metric | T/V/M |
| REL-S-04 | FO | Existing filtered `list_loci` projection contains locus, coordinate, and source taxon | “this association” is not self-contained and no conversation memory is allowed | T/V/M/D/X |
| RECORD-S-01 | NS | Existing `list_loci(assembly)` | Natural `What` form is outside grammar | M/X |
| RECORD-S-02 | NS | Existing `locus_detail` contains placement, calls, and public assertions | Extra natural detail phrase does not match `Show locus <key>` | M/D |
| RECORD-S-03 | NI | New `list_viral_lineages(assembly)`; exhaustive loci-plus-dedup is only a composite alternative | No result intent for viral-lineage set | V/M/X |
| RECORD-S-04 | NS | Existing `locus_detail.public_assertions[].supporting_evidence` | Natural `Which` form is outside grammar | M/D; Gold universe must be limited to membership-selected assertion evidence |

`RECORD-S-04` is answerable from the current public result only if “linked evidence” means the
supporting evidence selected by public assertion membership. A broader set of every EvidenceItem
would require another intent and an explicitly approved public projection.

## Literature templates: every-question mapping

All 16 rows have status NL and require B/C except `RECORD-L-02` and `RECORD-L-04`, which have no
entity slot and require C. The existing router rejects every natural `How`, `What`, or `Why` form.

| ID | Required literature evidence | Additional limitation or risk |
|---|---|---|
| HOST-L-01 | Candidate-identification methods | Current blanket policy interprets `identify EVE candidates` as prohibited new-EVE detection |
| HOST-L-02 | Endogeneity evidence | A source-high label alone must not be treated as proof of endogeneity |
| HOST-L-03 | Interpretation limitations | Human Gold must specify required limitation concepts and passages |
| HOST-L-04 | Viral-origin classification methods | Gold must distinguish formal, study, and extended lineage roles |
| VIRUS-L-01 | Viral-lineage assignment evidence | Viral role and exact/descendant semantics must come from binding |
| VIRUS-L-02 | False-positive protein-similarity controls | Method claims require exact supporting passages |
| VIRUS-L-03 | Taxonomic and naming uncertainty | Snapshot/version and role semantics are material |
| VIRUS-L-04 | Recorded-distribution limitations | Must not imply prevalence, global distribution, or host range |
| REL-L-01 | Detection methods for candidate regions | Association does not make method evidence locus-specific by itself |
| REL-L-02 | Endogeneity evidence | Literature-level evidence may not prove every individual locus |
| REL-L-03 | Alternative explanations and uncertainty | Human reviewers must define required limitations and forbidden claims |
| REL-L-04 | Why association does not prove modern infection or co-divergence | Blanket infection/co-divergence rules reject this legitimate limitation question; Unicode input also fails |
| RECORD-L-01 | Detection criteria | Internal locus hash may not occur in document text; no structured anchor is allowed in S2/S3 |
| RECORD-L-02 | Host-genomic flank evaluation methods | Current release has no flank assessments, although literature may discuss methods |
| RECORD-L-03 | Assembly/contig limitations | Internal locus hash may not be discoverable in a literature-only condition |
| RECORD-L-04 | Integrated-region versus viral-contig evidence | Must distinguish general study method from locus-specific proof |

For `RECORD-L-01` and `RECORD-L-03`, using a structured locus-to-document map would change a
literature-only condition into Hybrid retrieval. The approved corpus must either contain a
retrievable display identifier, or the expected retrieval difficulty must be retained and reported.

## Hybrid templates: every-question mapping

Every Hybrid row has primary status NH and requires B/R/C/H. Secondary structured gaps are shown
explicitly; primary status does not imply those gaps are solved.

| ID | Structured need plus literature need | Secondary gap and data |
|---|---|---|
| HOST-H-01 | `list_loci(species)` + endogeneity evidence | T/M/F/X; per-locus flank evidence is not currently public |
| HOST-H-02 | `list_viral_lineages(host)` + assignment methods | New intent; T/V/M/X |
| HOST-H-03 | `list_assemblies(host)` + assembly limitations | T/M/X |
| HOST-H-04 | `host_eve_profile` + interpretation evidence | Composite/multi-result envelope; T/V/M/X |
| VIRUS-H-01 | `list_source_taxa(viral lineage)` + assignment evidence | T/V/M/X |
| VIRUS-H-02 | `list_assemblies(viral lineage)` + endogeneity evidence | T/V/M/F/X; evidence applicability must be human-reviewed |
| VIRUS-H-03 | `viral_lineage_distribution` + limitations | Composite/multi-result envelope; T/V/M/X |
| VIRUS-H-04 | `list_loci(extended lineage)` + extended-label evidence | V/M/X; binding must select extended role |
| REL-H-01 | `list_assemblies(source AND viral)` + detection methods | T/V/M/X; blanket EVE-detection rule rejects this legitimate methods context |
| REL-H-02 | `aggregate(loci, source AND viral)` + independent-event literature limitation | T/V/M; blanket independent-event rule rejects this legitimate limitation context; Unicode fails; future Gold needs required documents and evidence groups as well as the limitation |
| REL-H-03 | exhaustive `list_loci(source AND viral)` plus `locus_detail` per locus, or a new bulk assertion projection, + assignment evidence | T/V/M/D/X; `list_loci` alone exposes lineage summaries, not the required public-assertion set; a multi-result envelope is needed; Unicode input fails |
| REL-H-04 | minimum `list_loci(source AND viral)` + literature and limits; full `host_virus_relationship` profile if the reviewed output requires assemblies, assertions, or counts | T/V/M/X; add a composite plan for the full profile |
| RECORD-H-01 | `locus_detail` + endogeneity evidence | M/D/F; current public result has no flank-assessment detail |
| RECORD-H-02 | `assembly_eve_profile` + identification methods | Composite plus `list_viral_lineages`; M/V/X |
| RECORD-H-03 | `locus_detail` + inclusion rationale + lineage evidence | New public inclusion/flank provenance; M/D/F |
| RECORD-H-04 | `locus_detail` + literature limitations | M/D |

Natural Hybrid decomposition must produce typed structured needs and typed literature evidence needs.
It must not ask users to append `. and explain the literature`, and it must not silently fall back
to literature-only retrieval after structured refusal.

## Unsupported templates: every-question mapping

The boundary names below are capability-analysis suggestions, not Gold labels or approvals. Every
row remains pending, and human review must later define the exact refusal category, required
explanation, forbidden claims, and prohibited stages.

| ID | Boundary being tested | Current deterministic protection | Future hardening |
|---|---|---|---|
| UNSUP-01 | Prevalence not established | Explicit `prevalence` scope-policy match | Preserve refusal before retrieval |
| UNSUP-02 | Biological absence not established | Rejected only as unknown natural syntax; `lacks` is not an explicit match | Add explicit absence boundary test |
| UNSUP-03 | Modern infection not established | Explicit infection match | Preserve refusal before retrieval |
| UNSUP-04 | Independent events not established | Explicit independent-integration-events match | Preserve refusal before retrieval |
| UNSUP-05 | Co-divergence not established | Unicode request validation blocks first; scope policy would match normalized ASCII wording | Define Unicode policy and preserve co-divergence refusal |
| UNSUP-06 | Exact integration date unavailable | Rejected only as unknown natural syntax | Add explicit dating boundary test |
| UNSUP-07 | Transcription not present in release | Rejected only as unknown natural syntax | Add explicit expression/activity boundary test |
| UNSUP-08 | Adaptive function unsupported | Rejected only as unknown natural syntax | Add explicit function boundary test |
| UNSUP-09 | Susceptibility/modern infection unsupported | Explicit infection match | Preserve refusal before retrieval |
| UNSUP-10 | Screened-negative does not establish absence | Explicit screened-negative and no-EVE matches | Preserve refusal before retrieval |
| UNSUP-11 | External HMMER/BLAST/new discovery | Explicit HMMER, BLAST, and new-EVE matches | Preserve refusal before any tool call |
| UNSUP-12 | External phylogenetic analysis | Explicit phylogenetic match | Preserve refusal before any tool call |
| UNSUP-13 | Live web outside approved corpus | Rejected only as unknown natural syntax; the current regex does not match `search the live web` word order | Add this exact live-web variant to the explicit boundary tests |
| UNSUP-14 | Global biological distribution from pilot release | Rejected only as unknown natural syntax | Add global-distribution/scope boundary test |
| UNSUP-15 | Arbitrary SQL | Explicit arbitrary-SQL match | Preserve fixed compiler boundary |
| UNSUP-16 | Certain ancestral host identity | Rejected only as unknown natural syntax | Add ancestral-host-certainty boundary test |

The explicit gaps for `UNSUP-02`, `UNSUP-06`, `UNSUP-07`, `UNSUP-08`, `UNSUP-13`, `UNSUP-14`,
and `UNSUP-16` matter before any broader natural-language router is introduced. Today they are
safe because unknown syntax fails closed; a future natural router must not accidentally accept
them.

## Four current policy conflicts

These answerable methods or limitation questions contain words that the current blanket scope
policy treats as forbidden:

- `HOST-L-01`: `identify EVE candidates`;
- `REL-H-01`: EVE loci that were `detected`;
- `REL-L-04`: explains why modern infection/co-divergence is not demonstrated;
- `REL-H-02`: explains why locus count is not an independent-event count.

A future solution needs a typed `method_explanation` or `interpretation_limitation` context with
paired safety tests. Simply deleting forbidden patterns would weaken the refusal boundary.

## Release/data blockers

Before any real scientific question can become trusted, all of the following are required:

1. an exact published DatasetRelease with public locus memberships and a trusted receipt;
2. an exact approved CorpusRelease and a release–corpus binding;
3. human-selected entity bindings with stable key, display name, release manifest, lineage role,
   snapshot, and exact/descendant policy;
4. capacity validation for complete structured sets, anchor counts, and context size;
5. fully instantiated, self-contained question text with no placeholders;
6. independent human wording review;
7. separately authored human Gold and manually approved Oracle evidence;
8. the existing trusted question-manifest admission gate.

Current public structured results also lack flank-assessment details and inclusion-decision
rationale. `source_high`, an Integration label, coordinates, or a viral-lineage assertion must not
be promoted into proof of endogeneity.

## Proposed future implementation order

This analysis recommends the following order; none of it is implemented here:

1. add strict typed classification for natural structured and literature questions;
2. add paired tests for safe methods/limitations contexts and every unsupported boundary;
3. decide and test the Unicode question-input policy;
4. implement `list_viral_lineages` with release-pinned role and closure semantics;
5. define deterministic `host_eve_profile`, `viral_lineage_distribution`, and
   `host_virus_relationship` compositions over the exact operation shapes above;
6. implement exhaustive-pagination coordination and deterministic composite plans;
7. define an immutable multi-plan/multi-result structured envelope without changing production
   ContextPack behavior;
8. add an approved public projection for inclusion and flank provenance if the release supplies it;
9. implement natural Hybrid decomposition into typed structured and literature needs;
10. only then bind approved entities and run readiness checks against approved local artifacts;
11. execute later benchmark phases only after their separate approvals and required human labels.

Production defaults, parser, QueryPlan union, SQL compiler, database schema, releases, corpora,
providers, embeddings, S0–S6 definitions, and ContextPack were not changed by this redesign.
