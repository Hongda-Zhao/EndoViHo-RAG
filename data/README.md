# Frozen data manifests, audits, and public mini release

This directory contains small, versioned metadata, audit records, and one 11-locus public mini
release. Source workbooks, NCBI reports, genome assemblies, and other large or license-controlled
artifacts are not committed.

## Structured pilot metadata

- `manifests/milestone1_zhao_v4_data_s1.json` is the typed source manifest: approved scope,
  checksums, licenses/usage bases, acquisition commands, and expected counts.
- `audits/milestone1_data_s1_import_audit.json` is the deterministic import audit: observed counts,
  distinct/duplicate checks, issue counts, key digests, and release-readiness state.
- `releases/endoviho-mini-v1/` is the first real portable DatasetRelease: 11 explicitly included
  loci on three current Unionidae assemblies with complete flank and authority bindings.

## Canonical workbook

The frozen source is the official bioRxiv DC6/media-6 file `649669_file12.xlsx`, physical worksheet
`S3`, byte size `83,851,778`, SHA-256
`79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800`. Its conservative recorded
license key is `CC-BY-NC-ND-4.0`. A locally renamed, metadata-edited workbook is semantically
equivalent but is not the canonical release artifact.

## Frozen NCBI resolution snapshot

Resolution uses NCBI Datasets v2 CLI `18.36.0` under source snapshot
`authority:ncbi-datasets-v2:18.36.0:20260826:pilot-resolution`:

| Report | SHA-256 | Bytes | JSONL records |
| --- | --- | ---: | ---: |
| Assembly data | `adcbef683cbc1ad592464e6a7ec64bd3d5612b91e4d44fb531d5d4cfdf4d81d4` | 39,377 | 10 |
| Sequence | `c96695fc44481f4b08c6bd4e56a439efb9baaf9332c8337d048fe5dab345e425` | 59,941,556 | 220,512 |

The manifest also records the CLI binary checksum, exact commands, and
`NCBI-MOLECULAR-DATA-USAGE-POLICY` usage basis. All 10 assemblies and all 12,233 selected contigs
resolved exactly, with no length mismatch.

## Frozen audit result

The historical import audit records 39,495 source calls: 71 `source_high`, 39,424 `source_low`,
38,968 normalized `Integration` placements, and 527 quarantined `Viral contig` outcomes. That
audit does not create public membership by itself. Its recorded readiness state remains an
immutable description of the 2026-08-26 staging event.

## Public mini DatasetRelease

`release:endoviho-rag:mini:v1:20260903:001` publishes only 11 explicitly listed loci. Each member
has one exact placement, complete unambiguous 20 kb sequence on each side, an explicit `include`
decision, NCBI taxonomy with merged/deleted history, and ICTV MSL41 v1 plus corrected VMR
`MSL41.v1.20260729` bindings. The source label `Orthopolintovirales` is preserved and linked by the
approved rename evidence to formal `Amphintovirales`.

This portable release does not classify any locus as `Transferred gene` or `Integrated virus`,
does not make the other 39,484 source calls public, and has not been activated in a live
PostgreSQL deployment. See
[`releases/endoviho-mini-v1/README.md`](releases/endoviho-mini-v1/README.md).

## Canonical full staging command

After placing the checksum-matching workbook and NCBI JSONL reports at the default `.artifacts/`
paths recorded by `scripts/stage_milestone1.py`, run from the repository root:

```sh
. scripts/local-dev-env.sh
docker compose up -d db
uv run alembic upgrade head
uv run python scripts/stage_milestone1.py
```

The command verifies the frozen bytes and manifest before atomically staging a candidate release;
it never creates public release membership or promotes release status.
