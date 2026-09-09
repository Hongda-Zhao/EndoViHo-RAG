# EndoViHo real mini DatasetRelease v1

Release key: `release:endoviho-rag:mini:v1:20260903:001`

This is the first public, version-controlled structured data release in this repository. It is a
deliberately small validation set, not a catalogue of all Zhao et al. source calls.

## Scope

- 3 current NCBI assemblies from one host family, `Unionidae`;
- 11 exact assembly-local loci;
- study viral-lineage label `Orthopolintovirales`;
- frozen rename mapping to ICTV MSL41 v1 order `Amphintovirales`;
- 11 explicit `include` decisions; and
- no `Transferred gene` or `Integrated virus` classification.

| Assembly | Assembly-source taxon | NCBI TaxId | Public loci |
| --- | --- | ---: | ---: |
| `GCA_016617855.1` | *Megalonaias nervosa* | 52375 | 6 |
| `GCA_016746295.1` | *Potamilus streckersoni* | 2493646 | 3 |
| `GCA_028554795.2` | *Sinohyriopsis cumingii* | 165450 | 2 |

Every locus has an exact versioned contig accession, a 0-based half-open interval, the physical
`S3` source row, source annotations, frozen NCBI report locators, two independently recorded 20 kb
adjacent sequence assessments with zero ambiguous bases, and the exact inclusion-decision digest.

## Files

- `dataset_release.json` is the self-checksummed release manifest.
- `loci.jsonl` is the checksummed public-membership ledger, one self-checksummed locus per line.

The four top-level scientific dimensions of every locus row are:

```text
assembly_source_taxon
eve_locus_or_reported_viral_region
viral_lineage_affinity
evidence_and_source
```

`HCVR`, `VR Type`, and `Viral Major Taxon` are preserved under `source_annotations`. They do not
create a relation-class assignment.

## Frozen authority versions

- NCBI Taxonomy taxdump `2026-08-29T05:29:15Z`, including merged and deleted TaxId history;
- ICTV Master Species List `MSL41 v1`; and
- corrected ICTV VMR revision `MSL41.v1.20260729`.

Exact source URLs, artifact SHA-256 values, snapshot identities, and licenses are in
`dataset_release.json`.

## Rebuild and verify

With the checksum-matching source workbook and frozen authority/candidate artifacts present under
`.artifacts/`, rebuild the release from the repository root:

```sh
uv run python scripts/build_mini_dataset_release.py
```

Verification of the tracked public package needs no private/source artifact:

```sh
uv run python scripts/build_mini_dataset_release.py --verify-only
```

`release_status = published` describes this portable version-controlled data package.
`database_activation_status = not_performed` is intentionally separate: loading it into a live
PostgreSQL deployment remains an operational action and is not implied by these files.

## Source and limitations

The source calls are from Zhao et al., *Lineage-specific accumulation of endogenous dsDNA viral
elements across eukaryotes*, bioRxiv v4, DOI `10.1101/2025.04.19.649669`. See
[`../../manifests/milestone1_zhao_v4_data_s1.json`](../../manifests/milestone1_zhao_v4_data_s1.json)
and the repository `DATA_LICENSE` for provenance and terms.

Flank support here means that exact adjacent assembly sequence was available and inspectable under
the frozen policy. It does not independently establish germline transmission, fixation, or either
of the relation classes excluded above.
