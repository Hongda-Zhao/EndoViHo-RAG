#!/usr/bin/env python3
"""Build the first 11-locus real, portable EndoViHo mini DatasetRelease."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from eve_relation_rag.activation.contracts import (
    AdjudicationCohortManifest,
    AssemblyTaxonAssignmentManifest,
    FlankEvidenceManifest,
    IctvArtifactManifest,
    InclusionDecisionManifest,
    NcbiTaxonomyArtifactManifest,
    PublicLocusMembershipManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
    canonical_model_sha256,
)
from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_SOURCE_SNAPSHOT_KEY,
    ImportedDataS1Record,
    iter_canonical_data_s1_import,
)
from eve_relation_rag.releases.mini_dataset import (
    AssemblySourceTaxon,
    AuthorityArtifactBinding,
    AuthoritySnapshotBinding,
    EveLocusOrReportedViralRegion,
    EvidenceAndSource,
    FlankSideEvidence,
    MiniDatasetReleaseManifest,
    MiniLocusRecord,
    MiniReleaseCounts,
    PublicMembershipFile,
    SourceAnnotations,
    SourcePublicationBinding,
    ViralLineageAffinity,
    compact_json_line,
    load_mini_dataset_release,
)

RELEASE_KEY = "release:endoviho-rag:mini:v1:20260903:001"
PUBLISHED_AT = "2026-09-03T06:21:26Z"
SELECTED_SOURCE_ROWS = frozenset(
    {
        39_724,
        39_725,
        47_423,
        47_424,
        47_425,
        47_452,
        47_456,
        47_464,
        47_469,
        47_471,
        47_472,
    }
)
SELECTED_ASSEMBLIES = frozenset(
    {
        "GCA_016617855.1",
        "GCA_016746295.1",
        "GCA_028554795.2",
    }
)
FULL_FLANK_BP = 20_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_model[ModelT](path: Path, model: type[ModelT]) -> ModelT:
    validator = cast(Any, model)
    return cast(ModelT, validator.model_validate_json(path.read_bytes(), strict=True))


def _jsonl_index(
    path: Path, key_fields: tuple[str, ...]
) -> dict[tuple[object, ...], tuple[int, Mapping[str, object]]]:
    index: dict[tuple[object, ...], tuple[int, Mapping[str, object]]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} line {line_number} is not an object")
            row = cast(dict[str, object], value)
            key = tuple(row.get(field) for field in key_fields)
            if key in index:
                raise ValueError(f"duplicate authority key {key!r} in {path}")
            index[key] = (line_number, row)
    return index


def _load_source_rows(workbook: Path) -> dict[int, ImportedDataS1Record]:
    records: dict[int, ImportedDataS1Record] = {}
    maximum = max(SELECTED_SOURCE_ROWS)
    for record in iter_canonical_data_s1_import(workbook):
        row = record.locator.excel_row
        if row > maximum:
            break
        if row not in SELECTED_SOURCE_ROWS:
            continue
        if not isinstance(record, ImportedDataS1Record):
            raise ValueError(f"selected source row {row} is quarantined")
        records[row] = record
    if set(records) != SELECTED_SOURCE_ROWS:
        missing = sorted(SELECTED_SOURCE_ROWS.difference(records))
        raise ValueError(f"selected source rows are missing: {missing}")
    return records


def _manifest_sha(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("manifest_sha256"), str):
        raise ValueError(f"{path} has no manifest_sha256")
    return cast(str, value["manifest_sha256"])


def _taxon_ancestry_contains(
    tax_id: int, required_term_key: str, ncbi: TaxonomySnapshotManifest
) -> bool:
    terms = {term.term_key: term for term in ncbi.terms}
    current = f"ncbi-taxonomy:taxid:{tax_id}"
    visited: set[str] = set()
    while current not in visited:
        if current == required_term_key:
            return True
        visited.add(current)
        term = terms.get(current)
        if term is None or term.parent_term_key is None:
            return False
        current = term.parent_term_key
    raise ValueError(f"taxonomy cycle encountered for taxid {tax_id}")


def _side(value: Any) -> FlankSideEvidence:
    if (
        value.verdict != "supported"
        or value.inspected_bp != FULL_FLANK_BP
        or value.ambiguous_bp != 0
        or value.ambiguity_fraction != "0.000000"
        or value.longest_ambiguity_run != 0
        or value.boundary_base not in {"A", "C", "G", "T"}
    ):
        raise ValueError("selected locus does not have a complete unambiguous flank")
    return FlankSideEvidence(
        verdict=value.verdict,
        inspected_bp=value.inspected_bp,
        ambiguous_bp=value.ambiguous_bp,
        ambiguity_fraction=value.ambiguity_fraction,
        longest_ambiguity_run=value.longest_ambiguity_run,
        boundary_base=value.boundary_base,
    )


def build_release(root: Path, output: Path) -> MiniDatasetReleaseManifest:
    source_manifest_path = root / "data/manifests/milestone1_zhao_v4_data_s1.json"
    source_audit_path = root / "data/audits/milestone1_data_s1_import_audit.json"
    workbook = root / ".artifacts/milestone1/biorxiv_data_s1_remote.xlsx"
    assembly_report_path = root / ".artifacts/milestone1/ncbi/assembly_data_report.jsonl"
    sequence_report_path = root / ".artifacts/milestone1/ncbi/sequence_report.jsonl"
    flank_root = root / ".artifacts/v0_activation/candidates/flanks-20260829T054654Z"
    structured_root = root / ".artifacts/v0_activation/candidates/structured-20260829T064128Z"
    taxonomy_root = root / ".artifacts/v0_activation/candidates/taxonomy-20260829T064128Z"

    source_manifest = cast(
        dict[str, Any], json.loads(source_manifest_path.read_text(encoding="utf-8"))
    )
    source_audit = cast(
        dict[str, Any], json.loads(source_audit_path.read_text(encoding="utf-8"))
    )
    expected_artifact = source_manifest["artifact"]
    if (
        expected_artifact["sha256"] != DATA_S1_ARTIFACT_SHA256
        or _sha256(workbook) != DATA_S1_ARTIFACT_SHA256
    ):
        raise ValueError("canonical source workbook bytes do not match the source manifest")
    assembly_resolution = source_manifest["assembly_resolution"]
    if _sha256(assembly_report_path) != assembly_resolution["assembly_report"]["sha256"]:
        raise ValueError("NCBI assembly report checksum does not match the source manifest")
    if _sha256(sequence_report_path) != assembly_resolution["sequence_report"]["sha256"]:
        raise ValueError("NCBI sequence report checksum does not match the source manifest")

    cohort = _load_model(
        flank_root / "structured_adjudication_cohort.json", AdjudicationCohortManifest
    )
    flanks = _load_model(flank_root / "source_high_flank_evidence.json", FlankEvidenceManifest)
    decisions = _load_model(
        structured_root / "inclusion_decisions.manifest.json", InclusionDecisionManifest
    )
    public_loci = _load_model(
        structured_root / "public_locus_membership.manifest.json",
        PublicLocusMembershipManifest,
    )
    assignments = _load_model(
        taxonomy_root / "assembly_taxon_assignments.manifest.json",
        AssemblyTaxonAssignmentManifest,
    )
    ncbi = _load_model(
        taxonomy_root / "ncbi_taxonomy_snapshot.manifest.json", TaxonomySnapshotManifest
    )
    ncbi_artifact = _load_model(
        taxonomy_root / "ncbi_taxonomy_artifact.manifest.json",
        NcbiTaxonomyArtifactManifest,
    )
    ictv = _load_model(
        taxonomy_root / "ictv_msl41_snapshot.manifest.json", TaxonomySnapshotManifest
    )
    ictv_artifact = _load_model(
        taxonomy_root / "ictv_msl41_artifact.manifest.json", IctvArtifactManifest
    )
    mapping_manifest = _load_model(
        taxonomy_root / "study_formal_mapping.manifest.json", StudyFormalMappingManifest
    )
    source_manifest_sha256 = _sha256(source_manifest_path)
    source_audit_sha256 = _sha256(source_audit_path)
    if source_audit["report"]["passed"] is not True:
        raise ValueError("frozen source audit did not pass")
    if (
        cohort.source_manifest_sha256 != source_manifest_sha256
        or cohort.source_audit_sha256 != source_audit_sha256
    ):
        raise ValueError("parent cohort does not bind the tracked source manifest and audit")
    if (
        flanks.cohort_manifest_sha256 != cohort.manifest_sha256
        or decisions.cohort_manifest_sha256 != cohort.manifest_sha256
        or decisions.flank_manifest_sha256 != flanks.manifest_sha256
    ):
        raise ValueError("parent cohort, flank, and inclusion manifests are not bound")

    source_rows = _load_source_rows(workbook)
    cohort_by_row = {record.source_row: record for record in cohort.primary_records}
    flank_by_locus = {record.locus_key: record for record in flanks.records}
    decision_by_locus = {record.locus_key: record for record in decisions.decisions}
    public_by_locus = {record.locus_key: record for record in public_loci.memberships}
    assignment_by_assembly = {
        record.assembly_accession_version: record for record in assignments.assignments
    }
    assembly_reports = _jsonl_index(assembly_report_path, ("accession",))
    sequence_reports = _jsonl_index(
        sequence_report_path, ("assembly_accession", "genbank_accession")
    )

    family = next(term for term in ncbi.terms if term.canonical_name == "Unionidae")
    formal_mapping = mapping_manifest.mappings[0]
    formal_term = next(
        term for term in ictv.terms if term.term_key == formal_mapping.formal_term_key
    )
    if formal_term.canonical_name != "Amphintovirales" or formal_term.rank != "order":
        raise ValueError("frozen ICTV mapping does not resolve to Amphintovirales/order")

    records: list[MiniLocusRecord] = []
    for source_row in sorted(SELECTED_SOURCE_ROWS):
        source = source_rows[source_row]
        cohort_record = cohort_by_row[source_row]
        if (
            source.assembly_accession_version not in SELECTED_ASSEMBLIES
            or source.locus_key != cohort_record.locus_key
            or source.sequence_accession_version != cohort_record.sequence_accession_version
            or source.start0 != cohort_record.start0
            or source.end0 != cohort_record.end0
        ):
            raise ValueError(f"source/cohort identity mismatch at S3!{source_row}")
        flank = flank_by_locus[source.locus_key]
        decision = decision_by_locus[source.locus_key]
        public = public_by_locus.get(source.locus_key)
        if decision.decision != "include" or public is None:
            raise ValueError(f"S3!{source_row} lacks an existing include/public candidate record")

        assignment = assignment_by_assembly[source.assembly_accession_version]
        assembly_line, assembly_report = assembly_reports[(source.assembly_accession_version,)]
        assembly_info = cast(Mapping[str, object], assembly_report["assembly_info"])
        organism = cast(Mapping[str, object], assembly_report["organism"])
        if assembly_info.get("assembly_status") != "current":
            raise ValueError(
                f"selected assembly is not current: {source.assembly_accession_version}"
            )
        if (
            organism.get("organism_name") != source.raw_row["Organism Name"]
            or organism.get("tax_id") != assignment.resolved_ncbi_tax_id
        ):
            raise ValueError(f"assembly taxon mismatch at S3!{source_row}")
        if not _taxon_ancestry_contains(assignment.resolved_ncbi_tax_id, family.term_key, ncbi):
            raise ValueError(
                f"selected assembly is outside Unionidae: {source.assembly_accession_version}"
            )

        sequence_key = (source.assembly_accession_version, source.sequence_accession_version)
        sequence_line, sequence_report = sequence_reports[sequence_key]
        if sequence_report.get("length") != source.contig_length:
            raise ValueError(f"NCBI contig length mismatch at S3!{source_row}")

        left = _side(flank.left)
        right = _side(flank.right)
        payload: dict[str, object] = {
            "record_schema_version": "endoviho-mini-locus-v1",
            "release_key": RELEASE_KEY,
            "public_membership": True,
            "inclusion_decision": "include",
            "relation_class_assigned": False,
            "assembly_source_taxon": AssemblySourceTaxon(
                assembly_accession_version=source.assembly_accession_version,
                assembly_status_at_snapshot="current",
                ncbi_tax_id=assignment.resolved_ncbi_tax_id,
                organism_name=cast(str, organism["organism_name"]),
                host_family_name="Unionidae",
                host_family_term_key=family.term_key,
                ncbi_taxonomy_snapshot_key=ncbi.snapshot_key,
            ),
            "eve_locus_or_reported_viral_region": EveLocusOrReportedViralRegion(
                locus_key=source.locus_key,
                contig_accession_version=source.sequence_accession_version,
                coordinate_system=cast(Any, source.coordinate_system),
                start0=source.start0,
                end0=source.end0,
                length=source.length,
                placement_key=cast(str, cohort_record.placement_key),
            ),
            "viral_lineage_affinity": ViralLineageAffinity(
                source_label="Orthopolintovirales",
                source_term_key="study-viral-major-taxon:orthopolintovirales",
                source_role="study_defined",
                formal_mapping_relation="renamed_to",
                formal_label="Amphintovirales",
                formal_term_key=formal_term.term_key,
                formal_rank="order",
                ictv_snapshot_key=ictv.snapshot_key,
                mapping_key=formal_mapping.mapping_key,
            ),
            "evidence_and_source": EvidenceAndSource(
                source_snapshot_key=DATA_S1_SOURCE_SNAPSHOT_KEY,
                source_artifact_sha256=DATA_S1_ARTIFACT_SHA256,
                source_worksheet="S3",
                source_row=source_row,
                source_record_key=source.source_record_key,
                detection_call_key=source.record_key,
                ncbi_assembly_report_sha256=assembly_resolution["assembly_report"]["sha256"],
                ncbi_assembly_report_line=assembly_line,
                ncbi_sequence_report_sha256=assembly_resolution["sequence_report"]["sha256"],
                ncbi_sequence_report_line=sequence_line,
                flank_policy_key=flank.assessment_policy_key,
                flank_assessed_by=flank.assessed_by,
                flank_assessed_at=flank.assessed_at,
                flank_record_sha256=flank.record_sha256,
                flank_response_sha256=cast(str, flank.response_sha256),
                normalized_sequence_sha256=cast(str, flank.normalized_sequence_sha256),
                left_flank=left,
                right_flank=right,
                inclusion_policy_key=decision.policy_key,
                inclusion_decision_sha256=decision.decision_sha256,
                inclusion_reason_codes=decision.reason_codes,
            ),
            "source_annotations": SourceAnnotations(
                hcvr="Yes",
                vr_type="Integration",
                viral_major_taxon="Orthopolintovirales",
                native_vr_token=source.native_vr_token,
                annotated_viral_proportion=source.raw_row["Annoated Viral Proportion"],
                unique_rate=source.raw_row["Unique Rate"],
                conserved_og=source.raw_row["Conserved OG"],
                busco_score=source.raw_row["Busco score"],
            ),
        }
        payload["record_sha256"] = canonical_model_sha256(payload)
        records.append(MiniLocusRecord.model_validate(payload))

    records.sort(key=lambda value: value.eve_locus_or_reported_viral_region.locus_key)
    loci_bytes = b"".join(compact_json_line(record) for record in records)
    ncbi_history = ncbi.ncbi_history
    if ncbi_history is None:
        raise ValueError("NCBI taxonomy snapshot lacks merged/deleted history")

    manifest_payload: dict[str, object] = {
        "manifest_schema_version": "endoviho-mini-dataset-release-v1",
        "dataset_key": "dataset:endoviho-rag",
        "release_key": RELEASE_KEY,
        "title": "EndoViHo real mini DatasetRelease v1",
        "release_status": "published",
        "publication_channel": "version-controlled-portable-release",
        "published_at": PUBLISHED_AT,
        "database_activation_status": "not_performed",
        "coordinate_system": "0-based-half-open",
        "selection_policy_key": "policy:mini-v1-source-high-full-flanks-v1",
        "inclusion_policy_key": "policy:v0-pilot-inclusion-v1",
        "relation_class_policy": (
            "HCVR and VR Type are source annotations; no Transferred gene or Integrated virus "
            "class is assigned"
        ),
        "scope_statement": (
            "Eleven source-high loci on three current Unionidae assemblies, each with exact NCBI "
            "contig coordinates, two complete unambiguous 20 kb flanks, and an explicit include "
            "decision; this is not an exhaustive survey of the source workbook."
        ),
        "host_lineage": "Unionidae",
        "source_viral_lineage": "Orthopolintovirales",
        "formal_viral_lineage": "Amphintovirales",
        "source_manifest_sha256": source_manifest_sha256,
        "source_audit_sha256": source_audit_sha256,
        "source": SourcePublicationBinding(
            authors_short=source_manifest["primary_source"]["authors_short"],
            title=source_manifest["primary_source"]["title"],
            doi=source_manifest["primary_source"]["doi"],
            version=source_manifest["primary_source"]["version"],
            posted_date=source_manifest["primary_source"]["posted_date"],
            artifact_label=expected_artifact["source_label"],
            artifact_filename=expected_artifact["native_filename"],
            worksheet=expected_artifact["worksheet"],
            artifact_sha256=expected_artifact["sha256"],
            license_key=expected_artifact["license_key"],
            provenance_uri=expected_artifact["media_url"],
        ),
        "parent_cohort_manifest_sha256": cohort.manifest_sha256,
        "parent_flank_manifest_sha256": flanks.manifest_sha256,
        "parent_inclusion_manifest_sha256": decisions.manifest_sha256,
        "authority_snapshots": (
            AuthoritySnapshotBinding(
                authority="NCBI Taxonomy",
                version=ncbi.version,
                snapshot_key=ncbi.snapshot_key,
                manifest_sha256=ncbi.manifest_sha256,
                coverage=ncbi.coverage,
                merged_history_included=True,
                deleted_history_included=True,
                artifacts=(
                    AuthorityArtifactBinding(
                        role="taxdump",
                        version=ncbi.version,
                        filename=ncbi_artifact.archive.filename,
                        sha256=ncbi_artifact.archive.sha256,
                        source_uri=ncbi_artifact.archive.source_uri,
                        retrieved_at=ncbi_artifact.archive.retrieved_at,
                        license_key=ncbi_artifact.archive.license_key,
                    ),
                ),
            ),
            AuthoritySnapshotBinding(
                authority="ICTV",
                version=ictv.version,
                snapshot_key=ictv.snapshot_key,
                manifest_sha256=ictv.manifest_sha256,
                coverage=ictv.coverage,
                merged_history_included=False,
                deleted_history_included=False,
                artifacts=(
                    AuthorityArtifactBinding(
                        role="master_species_list",
                        version=ictv_artifact.msl_version,
                        filename=ictv_artifact.msl.filename,
                        sha256=ictv_artifact.msl.sha256,
                        source_uri=ictv_artifact.msl.source_uri,
                        retrieved_at=ictv_artifact.msl.retrieved_at,
                        license_key=ictv_artifact.msl.license_key,
                    ),
                    AuthorityArtifactBinding(
                        role="corrected_virus_metadata_resource",
                        version=ictv_artifact.vmr_revision,
                        filename=ictv_artifact.corrected_vmr.filename,
                        sha256=ictv_artifact.corrected_vmr.sha256,
                        source_uri=ictv_artifact.corrected_vmr.source_uri,
                        retrieved_at=ictv_artifact.corrected_vmr.retrieved_at,
                        license_key=ictv_artifact.corrected_vmr.license_key,
                    ),
                ),
            ),
        ),
        "counts": MiniReleaseCounts(
            assemblies=3,
            loci=len(records),
            host_lineages=1,
            source_viral_lineages=1,
            formal_viral_lineages=1,
            include=len(records),
            exclude=0,
            review=0,
        ),
        "public_membership_file": PublicMembershipFile(
            path="loci.jsonl",
            media_type="application/x-ndjson",
            byte_size=len(loci_bytes),
            sha256=hashlib.sha256(loci_bytes).hexdigest(),
            record_count=len(records),
        ),
        "limitations": (
            "The release includes only the explicitly listed 11 loci and says nothing about the "
            "remaining source calls.",
            "Flank support means exact adjacent assembly sequence was inspectable under the frozen "
            "policy; it does not independently prove germline transmission or fixation.",
            "Orthopolintovirales is preserved as the study label and linked by a frozen approved "
            "rename mapping to ICTV MSL41 v1 Amphintovirales.",
            "HCVR and VR Type remain source annotations and are not relation-class evidence.",
            "The portable release is public data; activation in a live PostgreSQL deployment is a "
            "separate operational step.",
        ),
    }
    manifest_payload["manifest_sha256"] = canonical_model_sha256(manifest_payload)
    manifest = MiniDatasetReleaseManifest.model_validate(manifest_payload)

    output.mkdir(parents=True, exist_ok=True)
    (output / "loci.jsonl").write_bytes(loci_bytes)
    manifest_bytes = (
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    (output / "dataset_release.json").write_bytes(manifest_bytes)
    loaded = load_mini_dataset_release(output)
    if loaded.manifest.manifest_sha256 != manifest.manifest_sha256:
        raise ValueError("written release failed round-trip validation")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = arguments.root.resolve()
    output = (
        arguments.output.resolve()
        if arguments.output is not None
        else root / "data/releases/endoviho-mini-v1"
    )
    if arguments.verify_only:
        release = load_mini_dataset_release(output)
    else:
        manifest = build_release(root, output)
        release = load_mini_dataset_release(output)
        if release.manifest.manifest_sha256 != manifest.manifest_sha256:
            raise ValueError("build/load manifest identities differ")
    print(f"release_key={release.manifest.release_key}")
    print(f"release_status={release.manifest.release_status}")
    print(f"manifest_sha256={release.manifest.manifest_sha256}")
    print(f"assemblies={release.manifest.counts.assemblies}")
    print(f"loci={release.manifest.counts.loci}")
    print(f"database_activation_status={release.manifest.database_activation_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
