# Scientific question redesign for the RAG-value benchmark

## Outcome

The scientific benchmark now asks only for release- or source-bounded association inventories. Its
48 answerable templates do not ask how a record was found, why a classification was made, what
evidence proves endogeneity, or which interpretive limitation applies. Each answerable row targets
the same normalized relationship:

```text
assembly-source taxonomic unit or source-reported host taxonomic unit
  -> represented source species or source-reported host species
  -> assembly
  -> locus or source-reported region
  -> relation class
  -> role-qualified viral lineage with exact/descendant scope
```

The question workflow retains two explicitly different resources:

| Resource | Purpose | Admission to trusted benchmark |
|---|---|---|
| System-regression questions | Parser, route, SQL compiler, identifiers, filters, pagination, release isolation, and refusal regression | Never automatically; these are software fixtures |
| Scientific question templates | Natural association questions across structured, literature, Hybrid, and unsupported families | Only after entity binding, instantiation, human review, Gold annotation, and approval |

The old 64 route-oriented questions remain byte-for-byte in
`benchmark/system_regression/rag_value_route_questions_v1.jsonl`, with SHA-256
`9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34`. The 64
association-oriented templates live in
`benchmark/rag_value_ablation/scientific_questions_template.jsonl`, with SHA-256
`4ba8ad0291e57ed6eb6bbdad67cebf1c612f5b7b4bdb65fb8fbd53832c273227`. The resources
must not be merged during execution or reporting.

This redesign did not run a model, retriever, database query, embedding provider, or LLM. It did
not create Gold, Oracle evidence, human labels, results, or approvals.

## Relation-class boundary

`Transferred gene` and `Integrated virus` are requested authoring vocabulary, not currently
approved repository truth. The current repository has no approved mapping that derives either
class. In particular:

- source `VR Type = Integration` is not automatically `Integrated virus`;
- source `VR Type = Viral contig` is not automatically `Transferred gene`; and
- `HCVR`, `source_high`, or `source_low` does not establish either relation class.

No template or future loader may manufacture this mapping. A future trusted benchmark needs an
explicit, versioned, independently reviewed relation-class assertion or mapping policy. Literature-
reported class labels must remain source-reported values and must never overwrite a structured
value.

The currently inspected candidate cohort cannot test class or viral-lineage discrimination: its
selected records have only the source label `Integration` and the study-defined lineage
`Orthopolintovirales`. This candidate state is not a public release, and its lack of category and
lineage diversity is an activation blocker rather than a negative biological result.

## Association output contract

The three answerable families intentionally retain different truth domains:

| Family | Required association output | Meaning |
|---|---|---|
| Structured | `exact_association_set` | Exact release-bound tuples from approved structured truth |
| Literature | `source_reported_association_set` | Tuples explicitly reported by permitted documents, with required documents and evidence groups |
| Hybrid | `exact_association_set`, `source_reported_association_set`, and `cross_source_association_set` | Both source-specific sets plus a deterministic structured-only/literature-only/both alignment |

An association tuple must preserve the applicable assembly-source taxon or source-reported host
taxon, represented source species or source-reported host species, assembly, locus or named source
region, relation class, viral-lineage role, lineage snapshot, and exact-versus-descendant scope.
Null or source-absent fields remain explicit; they are not filled by name similarity or by copying
from the other source.

“Species within a host lineage” means only source species represented in the exact selected
release or explicitly reported by the permitted literature. It does not mean every biological
descendant of that lineage, and absence from the returned set does not establish biological
absence.

Viral-lineage bindings must include a role (`study_viral_lineage`, `formal_viral_taxonomy`, or
`extended_viral_lineage` where approved), an exact snapshot, and an exact-versus-descendant policy.
Names from different roles are never silently merged.

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
  -> human selects release-scoped entities and lineage role/scope
  -> approved relation-class assertion or mapping policy is supplied
  -> deterministic text instantiation with no remaining placeholders
  -> parser/readiness, diversity, pagination, and capacity checks
  -> independent scientific wording review
  -> separate human Gold and Oracle annotation
  -> approved EvaluationQuestion
```

No step may infer labels from a model, current retriever, lexical overlap, parser acceptance,
`Integration`, `Viral contig`, or `HCVR`. Parser acceptance is a software-readiness signal only. A
placeholder template can never be admitted directly to a trusted question manifest.

## Family versus scientific task

`family` controls the evidence and scoring contract. `scientific_task` describes which association
projection the question requests:

| Scientific task | Structured | Literature | Hybrid | Unsupported | Total |
|---|---:|---:|---:|---:|---:|
| `source_taxon_association` | 4 | 4 | 4 | 0 | 12 |
| `viral_lineage_association` | 4 | 4 | 4 | 0 | 12 |
| `source_viral_lineage_association` | 4 | 4 | 4 | 0 | 12 |
| `assembly_locus_association` | 4 | 4 | 4 | 0 | 12 |
| `unsupported_scientific_or_operational_boundary` | 0 | 0 | 0 | 16 | 16 |
| **Total** | **16** | **16** | **16** | **16** | **64** |

## Entity-binding worksheet

The empty binding worksheet retains the complete frozen slot vocabulary:

| Slot | Required type | Used in current set |
|---|---|---:|
| `HOST_LINEAGE_A` | source lineage | yes |
| `HOST_SPECIES_A` | source species | yes |
| `HOST_SPECIES_B` | source species | no |
| `VIRAL_LINEAGE_A` | viral lineage | yes |
| `VIRAL_LINEAGE_B` | viral lineage | yes |
| `EXTENDED_LINEAGE_A` | extended viral lineage | no |
| `ASSEMBLY_A` | assembly | yes |
| `ASSEMBLY_B` | assembly | yes |
| `LOCUS_A` | locus | yes |
| `LOCUS_B` | locus | yes |
| `LOCUS_C` | locus | yes |

Every binding starts with null stable key, display name, release identity, snapshot identity,
lineage role, and descendant policy. The worksheet is checksum-bound and pending; it is not an
approved binding manifest.

## Capability-status distribution

Natural wording is preserved even when the current system cannot execute it. Nothing is rewritten
into mechanical `Show/List/Count` syntax merely to claim support.

| Capability status | Templates | Meaning |
|---|---:|---|
| `requires_relation_contract` | 48 | The shared authoritative relation-class contract and association projection are absent; family-specific routing/composition gaps remain secondary requirements |
| `unsupported_by_design` | 16 | The requested inference, unsafe mapping, scope expansion, or operation must be refused |
| `supported_now` | 0 | No natural template is silently treated as executable |

Every answerable template additionally requires `association_projection`,
`relation_class_assertion`, `relation_contract`, and
`lineage_role_and_scope_preservation`. The detailed per-question analysis is in
[`scientific_question_capability_gap.md`](scientific_question_capability_gap.md).

## Human-readable question set

All wording below is exact. Every item remains pending.

### Host taxonomy association

Structured:

- `HOST-S-01` — Which represented source species within {HOST_LINEAGE_A} have records classified as Transferred gene, and which have records classified as Integrated virus, grouped by viral lineage in the selected release?
- `HOST-S-02` — For each represented source species within {HOST_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records, grouped by viral lineage?
- `HOST-S-03` — For each represented source species within {HOST_LINEAGE_A}, which viral lineages are recorded for Transferred gene records and which are recorded for Integrated virus records?
- `HOST-S-04` — Which exact association tuples link represented source species within {HOST_LINEAGE_A}, their assemblies and loci, the classes Transferred gene or Integrated virus, and their viral lineages in the selected release?

Literature:

- `HOST-L-01` — Which host species within {HOST_LINEAGE_A} does the permitted literature report with Transferred gene records, and which does it report with Integrated virus records, grouped by viral lineage?
- `HOST-L-02` — For host species within {HOST_LINEAGE_A}, which assemblies does the permitted literature associate with Transferred gene records and which with Integrated virus records, grouped by viral lineage?
- `HOST-L-03` — For each host species within {HOST_LINEAGE_A}, which viral lineages does the permitted literature associate with Transferred gene records and which with Integrated virus records?
- `HOST-L-04` — Which literature-reported association tuples link host species within {HOST_LINEAGE_A}, their named assemblies or regions, the classes Transferred gene or Integrated virus, and viral lineages?

Hybrid:

- `HOST-H-01` — Which represented source species within {HOST_LINEAGE_A} have Transferred gene associations in both the selected release and the permitted literature, and which have Integrated virus associations in both, grouped by viral lineage?
- `HOST-H-02` — For represented source species within {HOST_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both, grouped by viral lineage?
- `HOST-H-03` — For each represented source species within {HOST_LINEAGE_A}, which viral lineages have Transferred gene associations in both sources and which have Integrated virus associations in both?
- `HOST-H-04` — Which exact source-species, assembly, locus, relation-class, and viral-lineage association tuples are structured-only, literature-only, or present in both within {HOST_LINEAGE_A}, separating Transferred gene from Integrated virus?

### Viral lineage association

Structured:

- `VIRUS-S-01` — Which release-represented assembly-source taxonomic units have records assigned to {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?
- `VIRUS-S-02` — Which release-represented source species have records assigned to {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?
- `VIRUS-S-03` — For each release-represented source species associated with {VIRAL_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records?
- `VIRUS-S-04` — Which exact loci are assigned to {VIRAL_LINEAGE_A}, grouped by release-represented source species and assembly and separated into Transferred gene and Integrated virus records?

Literature:

- `VIRUS-L-01` — Which host taxonomic units does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?
- `VIRUS-L-02` — Which host species does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?
- `VIRUS-L-03` — For host species associated with {VIRAL_LINEAGE_A}, which assemblies does the permitted literature link to Transferred gene records and which to Integrated virus records?
- `VIRUS-L-04` — Which named loci or source regions does the permitted literature associate with {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?

Hybrid:

- `VIRUS-H-01` — Which release-represented assembly-source taxonomic units are also reported in the permitted literature with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?
- `VIRUS-H-02` — Which release-represented source species are also reported in the permitted literature with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?
- `VIRUS-H-03` — For release-represented source species associated with {VIRAL_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both?
- `VIRUS-H-04` — Which exact release loci assigned to {VIRAL_LINEAGE_A} have matching literature-reported Transferred gene associations, and which have matching Integrated virus associations?

### Host taxon x viral lineage association

Structured:

- `REL-S-01` — Which release-represented source species within {HOST_LINEAGE_A} have Transferred gene associations with {VIRAL_LINEAGE_A}, and which have Integrated virus associations?
- `REL-S-02` — For release-represented source species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies contain Transferred gene records and which contain Integrated virus records?
- `REL-S-03` — Which exact loci define the recorded association between {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, separated by represented source species, assembly, Transferred gene, and Integrated virus?
- `REL-S-04` — Which represented source species, assemblies, loci, Transferred gene records, and Integrated virus records are associated with {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which are associated with {VIRAL_LINEAGE_B}?

Literature:

- `REL-L-01` — Which species within {HOST_LINEAGE_A} does the permitted literature associate with {VIRAL_LINEAGE_A} through Transferred gene records, and which through Integrated virus records?
- `REL-L-02` — For species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies does the permitted literature link to Transferred gene records and which to Integrated virus records?
- `REL-L-03` — Which named loci or source regions does the permitted literature associate with {HOST_LINEAGE_A} and {VIRAL_LINEAGE_A}, separated into Transferred gene and Integrated virus records?
- `REL-L-04` — Which literature-reported host species, assemblies, regions, Transferred gene records, and Integrated virus records are associated with {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which are associated with {VIRAL_LINEAGE_B}?

Hybrid:

- `REL-H-01` — Which release-represented source species within {HOST_LINEAGE_A} are also reported in the permitted literature with Transferred gene associations to {VIRAL_LINEAGE_A}, and which with Integrated virus associations?
- `REL-H-02` — For release-represented source species within {HOST_LINEAGE_A} associated with {VIRAL_LINEAGE_A}, which assemblies have Transferred gene associations in both sources and which have Integrated virus associations in both?
- `REL-H-03` — Which loci link {HOST_LINEAGE_A} to {VIRAL_LINEAGE_A} in structured records and permitted literature, separated by represented source species, assembly, Transferred gene, and Integrated virus?
- `REL-H-04` — Which represented source species, assemblies, loci, Transferred gene records, and Integrated virus records occur across the structured and literature sources for {VIRAL_LINEAGE_A} within {HOST_LINEAGE_A}, and which occur for {VIRAL_LINEAGE_B}?

### Assembly and locus association

Structured:

- `RECORD-S-01` — Which loci in assembly {ASSEMBLY_A} are classified as Transferred gene and which are classified as Integrated virus, grouped by viral lineage?
- `RECORD-S-02` — Which represented source species, assembly, locus identity, and viral lineage are recorded for locus {LOCUS_A}, including whether its relation class is Transferred gene or Integrated virus?
- `RECORD-S-03` — Which loci in assembly {ASSEMBLY_A} are assigned to {VIRAL_LINEAGE_A} as Transferred gene records and which as Integrated virus records?
- `RECORD-S-04` — Which represented source species, assemblies, relation classes, and viral lineages are recorded for {LOCUS_A}, {LOCUS_B}, and {LOCUS_C}, separating Transferred gene from Integrated virus?

Literature:

- `RECORD-L-01` — Which regions in assembly {ASSEMBLY_A} does the permitted literature report as Transferred gene, and which does it report as Integrated virus, grouped by viral lineage?
- `RECORD-L-02` — Which host species and viral lineages does the permitted literature associate with named regions in assembly {ASSEMBLY_A}, separated into Transferred gene and Integrated virus records?
- `RECORD-L-03` — Which named regions in {HOST_SPECIES_A} does the permitted literature report as Transferred gene and which as Integrated virus, grouped by assembly and viral lineage?
- `RECORD-L-04` — Which regions in assembly {ASSEMBLY_A} does the permitted literature associate with {VIRAL_LINEAGE_A} as Transferred gene records and which as Integrated virus records?

Hybrid:

- `RECORD-H-01` — Which source-species, assembly, relation-class, and viral-lineage association for locus {LOCUS_A} is present in both sources, including whether the class is Transferred gene or Integrated virus?
- `RECORD-H-02` — Which locus-level associations in assembly {ASSEMBLY_A} are present in both sources, separated into Transferred gene and Integrated virus records and grouped by viral lineage?
- `RECORD-H-03` — Which Transferred gene and Integrated virus associations in assembly {ASSEMBLY_A} are structured-only, literature-only, or present in both, grouped by locus and viral lineage?
- `RECORD-H-04` — Which represented source species, loci, viral lineages, Transferred gene records, and Integrated virus records are associated with assembly {ASSEMBLY_A}, and which are associated with assembly {ASSEMBLY_B}, with cross-source presence retained?

### Unsupported scientific or operational boundary

- `UNSUP-01` — Which host taxonomic unit has the highest prevalence of {VIRAL_LINEAGE_A}-related records?
- `UNSUP-02` — Which species definitely has no association with {VIRAL_LINEAGE_A}?
- `UNSUP-03` — Which modern host species are currently infected by {VIRAL_LINEAGE_A} because an EVE association is recorded?
- `UNSUP-04` — Which exact independent integration event is represented by each recorded EVE locus?
- `UNSUP-05` — Which pairs of host and viral lineages have co-diverged because matching EVE associations are recorded?
- `UNSUP-06` — Classify every record as either Transferred gene or Integrated virus even though neither relation class has been approved.
- `UNSUP-07` — Treat every Integration source label as Integrated virus and every Viral contig source label as Transferred gene, then list the resulting host associations.
- `UNSUP-08` — Treat every HCVR source label as Transferred gene or Integrated virus, then list the resulting host associations.
- `UNSUP-09` — Merge study-defined, formal, and extended viral-lineage roles into one lineage and report one combined host association set.
- `UNSUP-10` — Assign locus {LOCUS_A} to {VIRAL_LINEAGE_A} from name similarity alone.
- `UNSUP-11` — Because {HOST_SPECIES_A} has an association with {VIRAL_LINEAGE_A}, report the same association for every species within {HOST_LINEAGE_A}.
- `UNSUP-12` — Merge host-virus associations from unapproved or unversioned releases and corpora into the selected release.
- `UNSUP-13` — Treat the first page or a truncated result as the complete host-species, assembly, and locus association set.
- `UNSUP-14` — Search the live web for additional host-virus associations outside the approved corpus.
- `UNSUP-15` — Run BLAST or HMMER on a new sequence and add the inferred host-virus association to the selected release.
- `UNSUP-16` — Execute an arbitrary SQL query across all database tables to construct a new host-virus association.

## Stop condition

All 64 rows are pending authoring templates. Entity selection, category-policy approval, question
instantiation, Gold, Oracle evidence, execution, and results remain absent. Proceed only after the
corresponding human inputs and explicit approval.
