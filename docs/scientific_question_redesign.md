# Scientific question redesign for the RAG-value benchmark

## Outcome

The first benchmark version now asks only for associations represented by the available data:

```text
Assembly-source taxon
  -> EVE locus / reported viral region
  -> viral-lineage affinity
  -> evidence and source
```

The 48 answerable templates do not require records to be classified as `Transferred gene`
or `Integrated virus`. The remaining 16 templates test explicit scientific and operational
refusal boundaries. All 64 records remain pending authoring templates without Gold or approval.

## Source-label boundary

`HCVR`, `VR Type`, and `Viral Major Taxon` remain source-native annotations. In particular:

- `VR Type = Integration` does not imply `Integrated virus`;
- `VR Type = Viral contig` does not imply `Transferred gene`; and
- `HCVR` does not imply either relation class.

The v1 association schema has no `relation_class` field. The two requested relation classes
may be added only in a later, separately reviewed contract supported by explicit assertions.

## Association output contract

| Family | Required association output | Evidence boundary |
| --- | --- | --- |
| Structured | `exact_association_set` | Exact DatasetRelease and source-record keys |
| Literature | `source_reported_association_set` | Permitted documents and evidence-group keys |
| Hybrid | Both sets plus `cross_source_association_set` | Human-reviewed alignment; source values remain separate |

Every exact tuple carries an assembly-source taxon binding, assembly accession, EVE-locus key,
role- and snapshot-qualified viral-lineage affinity, release identity, and source-record keys.
Every literature tuple carries source-reported taxon text, a named viral region, source-reported
viral-lineage-affinity text, document keys, and evidence-group keys. Missing normalization is kept
explicit and is never filled from lexical similarity or the other truth domain.

## Template inventory

The canonical JSONL has SHA-256 `c6896954dc84e105a858e9ea0aabd88b9cedba4be1217911598c74a317df545c`.
Family counts are 16 structured, 16 literature, 16 Hybrid, and 16 unsupported.

## Entity-binding worksheet

The worksheet contains 10 empty pending slots:

| Slot | Required entity type |
| --- | --- |
| `ASSEMBLY_A` | `assembly` |
| `ASSEMBLY_B` | `assembly` |
| `ASSEMBLY_SOURCE_TAXON_A` | `assembly_source_taxon` |
| `EVE_LOCUS_A` | `eve_locus` |
| `EVE_LOCUS_B` | `eve_locus` |
| `EVE_LOCUS_C` | `eve_locus` |
| `REPORTED_REGION_A` | `reported_viral_region` |
| `SOURCE_TAXON_LINEAGE_A` | `source_lineage` |
| `VIRAL_LINEAGE_A` | `viral_lineage` |
| `VIRAL_LINEAGE_B` | `viral_lineage` |

## Human-readable question set

### Assembly-source taxon association

- `HOST-S-01` — Which EVE loci are recorded for assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, grouped by viral-lineage affinity and evidence source in the selected release?
- `HOST-S-02` — For each represented assembly-source taxon within {SOURCE_TAXON_LINEAGE_A}, which assemblies and EVE loci occur, grouped by viral-lineage affinity and evidence source?
- `HOST-S-03` — Which viral-lineage affinities occur across EVE loci from assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, and which source-record evidence identifies each association?
- `HOST-S-04` — Which exact association tuples link assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, their assemblies and EVE loci, viral-lineage affinities, and evidence sources?
- `HOST-L-01` — Which source-reported taxa within {SOURCE_TAXON_LINEAGE_A} are linked to reported viral regions, grouped by viral-lineage affinity and literature evidence source?
- `HOST-L-02` — For source-reported taxa within {SOURCE_TAXON_LINEAGE_A}, which assemblies or reported viral regions are named, grouped by viral-lineage affinity and evidence source?
- `HOST-L-03` — Which viral-lineage affinities does the permitted literature report for viral regions from taxa within {SOURCE_TAXON_LINEAGE_A}, and which evidence groups support each report?
- `HOST-L-04` — Which source-reported association tuples link taxa within {SOURCE_TAXON_LINEAGE_A}, named viral regions, viral-lineage affinities, and literature evidence sources?
- `HOST-H-01` — Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE-locus associations that align with reported viral regions in the permitted literature, grouped by viral-lineage affinity and evidence source?
- `HOST-H-02` — For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A}, which assemblies, EVE loci, and reported viral regions align across sources, grouped by viral-lineage affinity and evidence source?
- `HOST-H-03` — Which viral-lineage affinities are shared or source-specific across EVE loci and reported viral regions for taxa within {SOURCE_TAXON_LINEAGE_A}, with evidence provenance retained?
- `HOST-H-04` — Which taxon, EVE-locus or reported-viral-region, viral-lineage-affinity, and evidence-source tuples are structured-only, literature-only, or present in both within {SOURCE_TAXON_LINEAGE_A}?

### Viral-lineage affinity association

- `VIRUS-S-01` — Which represented assembly-source taxa have EVE loci assigned an affinity to {VIRAL_LINEAGE_A}, and what evidence source supports each association?
- `VIRUS-S-02` — Which assemblies and EVE loci are associated with viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by assembly-source taxon and evidence source?
- `VIRUS-S-03` — For each represented assembly-source taxon associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which EVE loci and source-record evidence are recorded?
- `VIRUS-S-04` — Which exact assembly-source-taxon, EVE-locus, viral-lineage-affinity {VIRAL_LINEAGE_A}, and evidence-source tuples occur in the selected release?
- `VIRUS-L-01` — Which source-reported taxa are linked to viral regions with affinity to {VIRAL_LINEAGE_A}, and which literature evidence source supports each report?
- `VIRUS-L-02` — Which named viral regions does the permitted literature associate with viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by source-reported taxon and evidence source?
- `VIRUS-L-03` — For source-reported taxa associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies or viral regions and evidence groups are named?
- `VIRUS-L-04` — Which source-reported taxon, viral-region, viral-lineage-affinity {VIRAL_LINEAGE_A}, and evidence-source tuples occur in the permitted literature?
- `VIRUS-H-01` — Which assembly-source taxa have EVE loci with affinity to {VIRAL_LINEAGE_A} that align to reported viral regions, with each source's evidence retained?
- `VIRUS-H-02` — Which EVE loci and reported viral regions associated with viral-lineage affinity {VIRAL_LINEAGE_A} are structured-only, literature-only, or present in both, grouped by taxon and evidence source?
- `VIRUS-H-03` — For assembly-source taxa associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies, EVE loci, reported viral regions, and evidence sources align?
- `VIRUS-H-04` — Which exact-release EVE loci assigned affinity to {VIRAL_LINEAGE_A} have human-reviewed matches to literature-reported viral regions, and what evidence source supports each side?

### Taxon × viral-lineage affinity association

- `REL-S-01` — Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE loci with affinity to {VIRAL_LINEAGE_A}, and what source-record evidence supports each association?
- `REL-S-02` — For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies, EVE loci, and evidence sources are recorded?
- `REL-S-03` — Which exact EVE loci define recorded associations between {SOURCE_TAXON_LINEAGE_A} and viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by assembly-source taxon and evidence source?
- `REL-S-04` — Which assembly-source taxa and EVE loci within {SOURCE_TAXON_LINEAGE_A} have affinity to {VIRAL_LINEAGE_A}, and which have affinity to {VIRAL_LINEAGE_B}, with evidence sources retained?
- `REL-L-01` — Which taxa within {SOURCE_TAXON_LINEAGE_A} does the permitted literature link to viral regions with affinity to {VIRAL_LINEAGE_A}, and what evidence source supports each report?
- `REL-L-02` — For source-reported taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which assemblies or viral regions and evidence groups are named?
- `REL-L-03` — Which named viral regions does the permitted literature associate with {SOURCE_TAXON_LINEAGE_A} and viral-lineage affinity {VIRAL_LINEAGE_A}, grouped by taxon and evidence source?
- `REL-L-04` — Which source-reported taxa and viral regions within {SOURCE_TAXON_LINEAGE_A} have affinity to {VIRAL_LINEAGE_A}, and which have affinity to {VIRAL_LINEAGE_B}, with evidence sources retained?
- `REL-H-01` — Which assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} have EVE loci with affinity to {VIRAL_LINEAGE_A} that align to reported viral regions, with evidence from both sources retained?
- `REL-H-02` — For assembly-source taxa within {SOURCE_TAXON_LINEAGE_A} associated with viral-lineage affinity {VIRAL_LINEAGE_A}, which EVE loci and reported viral regions align across evidence sources?
- `REL-H-03` — Which EVE loci and reported viral regions link {SOURCE_TAXON_LINEAGE_A} to viral-lineage affinity {VIRAL_LINEAGE_A}, with taxon identity and evidence source preserved?
- `REL-H-04` — Which taxon, EVE-locus or reported-viral-region, viral-lineage-affinity, and evidence-source tuples occur for {VIRAL_LINEAGE_A} within {SOURCE_TAXON_LINEAGE_A}, and which occur for {VIRAL_LINEAGE_B}?

### Assembly and locus/region association

- `RECORD-S-01` — Which EVE loci in assembly {ASSEMBLY_A} are recorded, grouped by viral-lineage affinity and evidence source?
- `RECORD-S-02` — Which assembly-source taxon, assembly, EVE-locus identity, viral-lineage affinity, and evidence source are recorded for {EVE_LOCUS_A}?
- `RECORD-S-03` — Which EVE loci in assembly {ASSEMBLY_A} have affinity to {VIRAL_LINEAGE_A}, and what source-record evidence supports each association?
- `RECORD-S-04` — Which assembly-source taxa, assemblies, viral-lineage affinities, and evidence sources are recorded for EVE loci {EVE_LOCUS_A}, {EVE_LOCUS_B}, and {EVE_LOCUS_C}?
- `RECORD-L-01` — Which viral regions in assembly {ASSEMBLY_A} does the permitted literature report, grouped by viral-lineage affinity and evidence source?
- `RECORD-L-02` — Which source-reported taxa and viral-lineage affinities does the literature associate with viral regions in assembly {ASSEMBLY_A}, with evidence sources retained?
- `RECORD-L-03` — Which taxon and viral-lineage affinity does the permitted literature report for viral region {REPORTED_REGION_A}, and which evidence source supports it?
- `RECORD-L-04` — Which viral regions in assembly {ASSEMBLY_A} are associated with viral-lineage affinity {VIRAL_LINEAGE_A}, and what literature evidence source supports each report?
- `RECORD-H-01` — Which taxon, EVE-locus, viral-lineage-affinity, and evidence-source association for {EVE_LOCUS_A} aligns with a literature-reported viral region?
- `RECORD-H-02` — Which EVE loci in assembly {ASSEMBLY_A} align with literature-reported viral regions, grouped by viral-lineage affinity and evidence source?
- `RECORD-H-03` — Which EVE-locus and reported-viral-region associations in assembly {ASSEMBLY_A} are structured-only, literature-only, or present in both, with viral-lineage affinity and evidence source retained?
- `RECORD-H-04` — Which assembly-source taxa, EVE loci, viral-lineage affinities, and evidence sources are associated with assembly {ASSEMBLY_A}, and which are associated with assembly {ASSEMBLY_B}, with reported viral regions retained separately?

### Unsupported boundaries

- `UNSUP-01` — Which source taxonomic unit has the highest prevalence of {VIRAL_LINEAGE_A}-related records?
- `UNSUP-02` — Which taxon definitely has no association with {VIRAL_LINEAGE_A}?
- `UNSUP-03` — Which modern host species are currently infected by {VIRAL_LINEAGE_A} because an EVE association is recorded?
- `UNSUP-04` — Which exact independent integration event is represented by each recorded EVE locus?
- `UNSUP-05` — Which pairs of source taxa and viral lineages have co-diverged because matching EVE associations are recorded?
- `UNSUP-06` — Classify every record as either Transferred gene or Integrated virus even though the v1 association contract has no relation-class dimension.
- `UNSUP-07` — Treat every Integration source label as Integrated virus and every Viral contig source label as Transferred gene, then list the resulting associations.
- `UNSUP-08` — Treat every HCVR source label as Transferred gene or Integrated virus, then list the resulting associations.
- `UNSUP-09` — Merge study-defined, formal, and extended viral-lineage roles into one lineage and report one combined association set.
- `UNSUP-10` — Assign EVE locus {EVE_LOCUS_A} to {VIRAL_LINEAGE_A} from name similarity alone.
- `UNSUP-11` — Because {ASSEMBLY_SOURCE_TAXON_A} has an association with {VIRAL_LINEAGE_A}, report the same association for every taxon within {SOURCE_TAXON_LINEAGE_A}.
- `UNSUP-12` — Merge associations from unapproved or unversioned releases and corpora into the selected release.
- `UNSUP-13` — Treat the first page or a truncated result as the complete assembly-source-taxon, EVE-locus, viral-lineage-affinity, and evidence set.
- `UNSUP-14` — Search the live web for additional taxon-virus associations outside the approved corpus.
- `UNSUP-15` — Run BLAST or HMMER on a new sequence and add the inferred association to the selected release.
- `UNSUP-16` — Execute an arbitrary SQL query across all database tables to construct a new taxon-virus association.

## Trust transition

A pending template can become an executable evaluation question only after entity binding,
complete association projection, provenance verification, independent scientific wording review,
human Gold and Oracle annotation, and explicit approval. Parser acceptance alone is not evidence.
