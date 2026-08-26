# EVE Relation V0 — Milestone 1 data semantics

> Contract: approved Draft B
>
> Status: verified staging; not a public EVE release
>
> Frozen pilot source: Zhao et al. v4 Data S1

## Scientific definition

An endogenous viral element (EVE) is a continuous virus-like gene fragment embedded in a
eukaryotic host genome and flanked on both sides by host genomic sequence. Milestone 1 stages
source-reported candidates; it does not yet claim that this definition has been demonstrated for
any staged row.

## Frozen pilot scope

The importer selects every VR reported for the ten approved bivalve assemblies in the Zhao et al.
v4 Data S1 scope. The verified result is:

| Outcome | Count | Meaning |
| --- | ---: | --- |
| Source-reported VR calls | 39,495 | Every selected native VR occurrence is retained. |
| `source_high` | 71 | The source workbook's HCVR value is exactly `Yes`. |
| `source_low` | 39,424 | Every other explicit source HCVR value. |
| `Integration` exact placements | 38,968 | Assembly, contig, length, and 0-based half-open interval passed validation. |
| `Viral contig` quarantine outcomes | 527 | Retained for audit, but not promoted to an exact integration placement. |
| Assemblies / selected contigs | 10 / 12,233 | All resolved exactly against the frozen NCBI reports. |

`source_high` and `source_low` are source-assessment labels. They are not validation verdicts,
proof of endogenization, inclusion decisions, or release membership.

## Object separation

Milestone 1 keeps these concepts separate:

```text
frozen source artifact
  -> immutable physical SourceRecord
  -> method-specific DetectionCall for the native VR occurrence
  -> coordinate-free locus identity
  -> optional exact placement
  -> independent left and right flank assessments
  -> explicit inclusion decision
  -> public release membership
```

Moving to a later object is never automatic. A source label or a valid coordinate cannot create
public membership by itself.

## Source, call, locus, and coordinate identity

- Each of the 39,495 selected rows has an immutable physical `SourceRecord` key derived from the
  frozen artifact, source snapshot, worksheet, and Excel row; this identity is independent of any
  import/detection method.
- Each selected native VR occurrence has a separate deterministic `DetectionCall` key derived
  from the frozen artifact/snapshot/worksheet, exact assembly and contig accession.versions,
  native VR token, and method/run identity.
- A source row and a detection call are therefore not the same identity; replay preserves the
  source row while a different approved method/run may create a distinct call.
- The locus identity binds the source snapshot, assembly accession/version, contig
  accession/version, native VR token, and identity policy.
- Coordinates are deliberately excluded from the locus key, so correcting a placement does not
  silently create a new biological identity.
- Placements use 0-based, half-open intervals and remain separate evidence attached to a locus.
- A missing or invalid placement can therefore be quarantined without losing the auditable source
  call or its coordinate-free identity.

## Authority and provenance

The study-defined `Orthopolintovirales` selection is a source claim, not a formal ICTV assignment.
Assembly and contig resolution is bound to the frozen NCBI Datasets v2 reports and exact artifact
checksums recorded in the Milestone 1 manifest. Public release additionally requires a complete,
versioned NCBI Taxonomy snapshot including merged and deleted taxon history, plus an explicit ICTV
release/snapshot binding for viral lineage assertions.

## Public-membership gates

A locus can enter a public release only when the release validator can verify all required
evidence and provenance, including:

1. one exact placement on the frozen assembly/contig authority;
2. independently supported left and right host-genomic flanks under the required policy;
3. an explicit `include` decision tied to that placement;
4. complete frozen NCBI taxonomy history and an ICTV release/snapshot binding; and
5. a clean, checksum-bound, conflict-free validation result.

The current pilot has no flank assessments, no inclusion decisions, and no public locus
memberships. Consequently, the 38,968 `Integration` placements are staged candidates—not a
published EVE catalogue—and the 527 `Viral contig` rows remain quarantined. Migration
`0005_m1_fail_closed_publication` additionally rejects database status promotion to `validated`
or `published` until a trusted, immutable validation-receipt workflow is implemented.
