"""Offline candidate-artifact driver for V0 structured-science activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from eve_relation_rag.activation.cohort import export_adjudication_cohort
from eve_relation_rag.activation.contracts import (
    AdjudicationCohortManifest,
    AssemblyTaxonAssignmentManifest,
    FlankEvidenceManifest,
    FlankEvidenceRequestPlan,
    FullSequenceBundleManifest,
    IctvArtifactManifest,
    InclusionDecisionManifest,
    NcbiTaxonomyArtifactManifest,
    PublicAssertionMembershipManifest,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StructuredAdjudicationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
)
from eve_relation_rag.activation.flanks import (
    load_full_sequence_bundle,
    materialize_primary_flank_artifacts,
)
from eve_relation_rag.activation.membership import (
    export_public_assertion_memberships,
    load_m1_gate_evidence,
)
from eve_relation_rag.activation.policy import (
    DependencyBindings,
    InclusionEvaluationInput,
    build_adjudication_manifest,
    build_inclusion_manifest,
    build_public_locus_membership_manifest,
    build_structured_activation_manifest,
)
from eve_relation_rag.activation.staging import stage_structured_activation_candidate
from eve_relation_rag.activation.taxonomy import (
    build_assembly_taxon_assignment_manifest,
    build_ictv_artifact_manifest,
    build_ncbi_taxonomy_artifact_manifest,
    build_polintovirus_rename_mapping_manifest,
    load_approved_assembly_tax_ids,
    load_ictv_taxonomy_snapshot,
    load_ncbi_taxonomy_snapshot,
)
from eve_relation_rag.config import get_settings
from eve_relation_rag.domain.keys import stable_key
from eve_relation_rag.importers.data_s1 import verify_file_bytes
from eve_relation_rag.releases.receipt_integrity import validation_request_payload
from eve_relation_rag.releases.request_export import (
    build_candidate_release_validation_request,
)

V0_SOURCE_MANIFEST_SHA256 = "afa5982542c592aaec6ec1033e0ac9ebbd3786e881baed0d81a1a602a30adf0d"
V0_SOURCE_AUDIT_SHA256 = "3429fe94b6e7c2da8bbdf107ad69a39e91998433ffd963ba8fd65f8701ea75c6"
V0_SEQUENCE_BUNDLE_SHA256 = "2e40b33d50a9d10252574749ccb27af38b6af5a0944ba87ba5a9bcd62fe91508"
V0_SEQUENCE_BUNDLE_BYTE_SIZE = 61_170_632
V0_SEQUENCE_RETRIEVED_AT = "2026-08-29T05:46:54Z"
V0_SEQUENCE_SOURCE_URI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
V0_SEQUENCE_TOOL_VERSION = "sha256:a16daa8e5f9e58329f6cc5c64c8df1cb439aedcec24805d18feb62661b32ef5a"
V0_NCBI_TAXDUMP_SHA256 = "3f60957db12bf78e61da18effb2b280b0c81c7d7cbf34fef20bce73aef4f55fa"
V0_NCBI_TAXDUMP_BYTE_SIZE = 78_562_422
V0_NCBI_TAXDUMP_MD5 = "8330404af450a246e8cd6ebb4d8a3eee"
V0_ICTV_MSL_SHA256 = "9d262d7864f1f619445a897ae568718ed15b1309c8f0c157a12fd7fb9fd07801"
V0_ICTV_MSL_BYTE_SIZE = 1_803_176
V0_ICTV_VMR_SHA256 = "b79b5d82a1b3b8e9dd5e19afe8fe1a8f441267474918a7cefa8ae4913adf45bb"
V0_ICTV_VMR_BYTE_SIZE = 3_879_426
V0_ASSEMBLY_REPORT_SHA256 = "adcbef683cbc1ad592464e6a7ec64bd3d5612b91e4d44fb531d5d4cfdf4d81d4"
V0_ASSEMBLY_REPORT_BYTE_SIZE = 39_377


class CandidateArtifactWriteError(RuntimeError):
    """Raised when a new candidate directory cannot be written atomically enough."""


@dataclass(frozen=True, slots=True)
class PrimaryFlankCandidate:
    cohort: AdjudicationCohortManifest
    bundle_manifest: FullSequenceBundleManifest
    request_plan: FlankEvidenceRequestPlan
    flank_evidence: FlankEvidenceManifest


@dataclass(frozen=True, slots=True)
class TaxonomyCandidate:
    ncbi_artifact: NcbiTaxonomyArtifactManifest
    ncbi_snapshot: TaxonomySnapshotManifest
    assembly_assignments: AssemblyTaxonAssignmentManifest
    ictv_artifact: IctvArtifactManifest
    ictv_snapshot: TaxonomySnapshotManifest
    study_formal_mapping: StudyFormalMappingManifest


def build_primary_flank_candidate(
    session: Session,
    *,
    bundle_path: str | Path,
    assessed_by: str,
    assessed_at: str,
    source_manifest_sha256: str = V0_SOURCE_MANIFEST_SHA256,
    source_audit_sha256: str = V0_SOURCE_AUDIT_SHA256,
    bundle_sha256: str = V0_SEQUENCE_BUNDLE_SHA256,
    bundle_byte_size: int = V0_SEQUENCE_BUNDLE_BYTE_SIZE,
    bundle_source_uri: str = V0_SEQUENCE_SOURCE_URI,
    bundle_retrieved_at: str = V0_SEQUENCE_RETRIEVED_AT,
    tool_version: str = V0_SEQUENCE_TOOL_VERSION,
) -> PrimaryFlankCandidate:
    """Read candidate truth and derive all 71 primary flank assessments offline."""

    cohort = export_adjudication_cohort(
        session,
        source_manifest_sha256=source_manifest_sha256,
        source_audit_sha256=source_audit_sha256,
    )
    bundle = load_full_sequence_bundle(
        bundle_path,
        expected_file_sha256=bundle_sha256,
        expected_file_byte_size=bundle_byte_size,
        source_uri=bundle_source_uri,
        retrieved_at=bundle_retrieved_at,
        tool_version=tool_version,
    )
    materialized = materialize_primary_flank_artifacts(
        cohort,
        bundle,
        assessed_by=assessed_by,
        assessed_at=assessed_at,
    )
    return PrimaryFlankCandidate(
        cohort=cohort,
        bundle_manifest=bundle.manifest,
        request_plan=materialized.request_plan,
        flank_evidence=materialized.evidence_manifest,
    )


def write_candidate_manifests(
    output_dir: str | Path,
    manifests: Mapping[str, BaseModel],
) -> tuple[Path, ...]:
    """Write a complete set into a new directory; existing output is never replaced."""

    destination = Path(output_dir)
    if destination.exists():
        raise CandidateArtifactWriteError("candidate output directory already exists")
    filenames = tuple(manifests)
    if not filenames or len(filenames) != len(set(filenames)):
        raise CandidateArtifactWriteError("candidate manifest filenames must be unique")
    if any(Path(name).name != name or not name.endswith(".json") for name in filenames):
        raise CandidateArtifactWriteError("candidate manifest names must be safe JSON basenames")
    try:
        destination.mkdir(parents=True, exist_ok=False)
        written: list[Path] = []
        for filename, manifest in manifests.items():
            path = destination / filename
            path.write_text(
                json.dumps(
                    manifest.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            written.append(path)
    except OSError as exc:
        raise CandidateArtifactWriteError("cannot write candidate manifest directory") from exc
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    """Build one offline candidate package and emit a compact JSON summary."""

    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "flanks":
            summary = _run_flanks(arguments)
        elif arguments.command == "taxonomy":
            summary = _run_taxonomy(arguments)
        elif arguments.command == "activation":
            summary = _run_activation(arguments)
        elif arguments.command == "apply":
            summary = _run_apply(arguments)
        else:
            summary = _run_validation_request(arguments)
    except Exception as exc:
        error = {
            "schema_version": "v0-structured-activation-driver-result-v1",
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _run_flanks(arguments: argparse.Namespace) -> dict[str, object]:
    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with Session(engine, autoflush=False) as session:
            candidate = build_primary_flank_candidate(
                session,
                bundle_path=arguments.bundle,
                assessed_by=arguments.assessed_by,
                assessed_at=arguments.assessed_at,
                source_manifest_sha256=arguments.source_manifest_sha256,
                source_audit_sha256=arguments.source_audit_sha256,
                bundle_sha256=arguments.bundle_sha256,
                bundle_byte_size=arguments.bundle_byte_size,
                bundle_source_uri=arguments.bundle_source_uri,
                bundle_retrieved_at=arguments.bundle_retrieved_at,
                tool_version=arguments.tool_version,
            )
    finally:
        engine.dispose()
    paths = write_candidate_manifests(
        arguments.output_dir,
        {
            "structured_adjudication_cohort.json": candidate.cohort,
            "source_high_full_sequences.manifest.json": candidate.bundle_manifest,
            "source_high_flank_request_plan.json": candidate.request_plan,
            "source_high_flank_evidence.json": candidate.flank_evidence,
        },
    )
    return {
        "schema_version": "v0-structured-activation-driver-result-v1",
        "status": "ok",
        "candidate_only": True,
        "database_writes": False,
        "primary_record_count": len(candidate.cohort.primary_records),
        "flank_record_count": len(candidate.flank_evidence.records),
        "output_files": [str(path) for path in paths],
        "manifest_sha256s": {
            "cohort": candidate.cohort.manifest_sha256,
            "bundle": candidate.bundle_manifest.manifest_sha256,
            "request_plan": candidate.request_plan.manifest_sha256,
            "flank_evidence": candidate.flank_evidence.manifest_sha256,
        },
    }


def _run_taxonomy(arguments: argparse.Namespace) -> dict[str, object]:
    ncbi_artifact = build_ncbi_taxonomy_artifact_manifest(
        arguments.taxdump,
        expected_sha256=arguments.taxdump_sha256,
        expected_byte_size=arguments.taxdump_byte_size,
        upstream_md5=arguments.taxdump_md5,
        version=arguments.ncbi_version,
        source_uri=arguments.ncbi_source_uri,
        checksum_source_uri=arguments.ncbi_checksum_source_uri,
        retrieved_at=arguments.ncbi_retrieved_at,
        usage_policy_source_uri=arguments.ncbi_policy_source_uri,
        usage_policy_retrieved_at=arguments.ncbi_policy_retrieved_at,
        usage_policy_capture_path=arguments.ncbi_policy_capture,
        expected_usage_policy_sha256=arguments.ncbi_policy_capture_sha256,
    )
    assembly_tax_ids = load_approved_assembly_tax_ids(
        arguments.assembly_report,
        expected_sha256=arguments.assembly_report_sha256,
        expected_byte_size=arguments.assembly_report_byte_size,
    )
    loaded_ncbi = load_ncbi_taxonomy_snapshot(
        ncbi_artifact,
        arguments.taxdump,
        required_tax_ids=assembly_tax_ids.values(),
    )
    assembly_artifact_key = stable_key(
        "source-artifact:ncbi-datasets-report",
        {
            "filename": Path(arguments.assembly_report).name,
            "sha256": arguments.assembly_report_sha256,
        },
    )
    assignments = build_assembly_taxon_assignment_manifest(
        loaded_ncbi,
        assembly_report_path=arguments.assembly_report,
        expected_assembly_report_sha256=arguments.assembly_report_sha256,
        expected_assembly_report_byte_size=arguments.assembly_report_byte_size,
        assembly_report_artifact_key=assembly_artifact_key,
    )
    ictv_artifact = build_ictv_artifact_manifest(
        msl_path=arguments.msl,
        corrected_vmr_path=arguments.vmr,
        expected_msl_sha256=arguments.msl_sha256,
        expected_msl_byte_size=arguments.msl_byte_size,
        expected_vmr_sha256=arguments.vmr_sha256,
        expected_vmr_byte_size=arguments.vmr_byte_size,
        msl_source_uri=arguments.msl_source_uri,
        vmr_source_uri=arguments.vmr_source_uri,
        retrieved_at=arguments.ictv_retrieved_at,
        usage_policy_source_uri=arguments.ictv_policy_source_uri,
        usage_policy_retrieved_at=arguments.ictv_policy_retrieved_at,
        usage_policy_capture_path=arguments.ictv_policy_capture,
        expected_usage_policy_sha256=arguments.ictv_policy_capture_sha256,
        msl_upstream_sha256=arguments.msl_upstream_sha256,
        msl_checksum_source_uri=arguments.msl_checksum_source_uri,
        vmr_upstream_sha256=arguments.vmr_upstream_sha256,
        vmr_checksum_source_uri=arguments.vmr_checksum_source_uri,
    )
    ictv_snapshot = load_ictv_taxonomy_snapshot(
        ictv_artifact,
        msl_path=arguments.msl,
        corrected_vmr_path=arguments.vmr,
    )
    proposal = verify_file_bytes(
        arguments.proposal_evidence,
        expected_sha256=arguments.proposal_evidence_sha256,
    )
    study_term_keys = {"Orthopolintovirales": arguments.study_order_term_key}
    if arguments.study_family_term_key is not None:
        study_term_keys["Adintoviridae"] = arguments.study_family_term_key
    mapping = build_polintovirus_rename_mapping_manifest(
        ictv_snapshot,
        study_snapshot_key=arguments.study_snapshot_key,
        study_term_keys=study_term_keys,
        evidence_artifact_sha256=proposal.sha256,
        evidence_locator=arguments.proposal_evidence_locator,
    )
    candidate = TaxonomyCandidate(
        ncbi_artifact=ncbi_artifact,
        ncbi_snapshot=loaded_ncbi.manifest,
        assembly_assignments=assignments,
        ictv_artifact=ictv_artifact,
        ictv_snapshot=ictv_snapshot,
        study_formal_mapping=mapping,
    )
    paths = write_candidate_manifests(
        arguments.output_dir,
        {
            "ncbi_taxonomy_artifact.manifest.json": candidate.ncbi_artifact,
            "ncbi_taxonomy_snapshot.manifest.json": candidate.ncbi_snapshot,
            "assembly_taxon_assignments.manifest.json": candidate.assembly_assignments,
            "ictv_msl41_artifact.manifest.json": candidate.ictv_artifact,
            "ictv_msl41_snapshot.manifest.json": candidate.ictv_snapshot,
            "study_formal_mapping.manifest.json": candidate.study_formal_mapping,
        },
    )
    return {
        "schema_version": "v0-structured-activation-driver-result-v1",
        "status": "ok",
        "candidate_only": True,
        "database_writes": False,
        "ncbi_term_count": len(candidate.ncbi_snapshot.terms),
        "ictv_term_count": len(candidate.ictv_snapshot.terms),
        "assignment_count": len(candidate.assembly_assignments.assignments),
        "mapping_count": len(candidate.study_formal_mapping.mappings),
        "output_files": [str(path) for path in paths],
        "manifest_sha256s": {
            "ncbi_artifact": candidate.ncbi_artifact.manifest_sha256,
            "ncbi_snapshot": candidate.ncbi_snapshot.manifest_sha256,
            "assembly_assignments": candidate.assembly_assignments.manifest_sha256,
            "ictv_artifact": candidate.ictv_artifact.manifest_sha256,
            "ictv_snapshot": candidate.ictv_snapshot.manifest_sha256,
            "study_formal_mapping": candidate.study_formal_mapping.manifest_sha256,
        },
    }


def _run_activation(arguments: argparse.Namespace) -> dict[str, object]:
    cohort = _load_manifest(arguments.cohort, AdjudicationCohortManifest)
    bundle = _load_manifest(arguments.bundle_manifest, FullSequenceBundleManifest)
    request_plan = _load_manifest(arguments.request_plan, FlankEvidenceRequestPlan)
    flanks = _load_manifest(arguments.flank_evidence, FlankEvidenceManifest)
    ncbi_artifact = _load_manifest(arguments.ncbi_artifact, NcbiTaxonomyArtifactManifest)
    ncbi_snapshot = _load_manifest(arguments.ncbi_snapshot, TaxonomySnapshotManifest)
    assignments = _load_manifest(arguments.assembly_assignments, AssemblyTaxonAssignmentManifest)
    ictv_artifact = _load_manifest(arguments.ictv_artifact, IctvArtifactManifest)
    ictv_snapshot = _load_manifest(arguments.ictv_snapshot, TaxonomySnapshotManifest)
    mapping = _load_manifest(arguments.study_formal_mapping, StudyFormalMappingManifest)
    m1 = load_m1_gate_evidence(
        source_manifest_path=arguments.source_manifest,
        expected_source_manifest_sha256=arguments.source_manifest_sha256,
        source_audit_path=arguments.source_audit,
        expected_source_audit_sha256=arguments.source_audit_sha256,
    )
    if (
        cohort.source_manifest_sha256 != m1.source_manifest_sha256
        or cohort.source_audit_sha256 != m1.source_audit_sha256
    ):
        raise ValueError("cohort does not bind the verified M1 manifest and audit")
    flank_by_locus = {row.locus_key: row for row in flanks.records}
    dependencies = DependencyBindings(
        ncbi_snapshot_manifest_sha256=ncbi_snapshot.manifest_sha256,
        ictv_snapshot_manifest_sha256=ictv_snapshot.manifest_sha256,
        mapping_manifest_sha256=mapping.manifest_sha256,
    )
    evaluations = tuple(
        InclusionEvaluationInput(
            record=record,
            flank=flank_by_locus.get(record.locus_key),
            dependencies=dependencies,
            m1_gates_pass=(
                m1.passed
                and record.import_outcome == "normalized_candidate"
                and record.placement_key is not None
                and not record.quarantine_issue_codes
            ),
            exact_placement_count=1 if record.placement_key is not None else 0,
        )
        for record in cohort.primary_records
    )
    inclusions = build_inclusion_manifest(cohort, flanks, evaluations)
    adjudication = build_adjudication_manifest(cohort, flanks, inclusions)
    public_loci = build_public_locus_membership_manifest(
        cohort,
        flanks,
        inclusions,
        adjudication,
    )

    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with Session(engine, autoflush=False) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            public_assertions = export_public_assertion_memberships(session, public_loci)
            session.rollback()
    finally:
        engine.dispose()
    activation = build_structured_activation_manifest(
        ncbi_artifact=ncbi_artifact,
        ncbi_snapshot=ncbi_snapshot,
        assembly_assignments=assignments,
        ictv_artifact=ictv_artifact,
        ictv_snapshot=ictv_snapshot,
        study_formal_mapping=mapping,
        cohort=cohort,
        full_sequence_bundle=bundle,
        flank_request_plan=request_plan,
        flanks=flanks,
        inclusions=inclusions,
        adjudication=adjudication,
        public_loci=public_loci,
        public_assertions=public_assertions,
    )
    manifests: dict[str, BaseModel] = {
        "inclusion_decisions.manifest.json": inclusions,
        "structured_adjudication.manifest.json": adjudication,
        "public_locus_membership.manifest.json": public_loci,
        "public_assertion_membership.manifest.json": public_assertions,
        "structured_activation.manifest.json": activation,
    }
    paths = write_candidate_manifests(arguments.output_dir, manifests)
    decision_counts = {
        decision: sum(row.decision == decision for row in inclusions.decisions)
        for decision in ("include", "review", "quarantine", "exclude")
    }
    quarantine = tuple(row for row in cohort.primary_records if row.import_outcome == "quarantine")
    return {
        "schema_version": "v0-structured-activation-driver-result-v1",
        "status": "ok",
        "candidate_only": True,
        "database_writes": False,
        "decision_counts": decision_counts,
        "public_locus_membership_count": public_loci.membership_count,
        "public_assertion_membership_count": public_assertions.membership_count,
        "assembly_outcomes": [
            row.model_dump(mode="json") for row in adjudication.assembly_outcomes
        ],
        "quarantine_records": [
            {
                "source_record_key": row.source_record_key,
                "source_row": row.source_row,
                "locus_key": row.locus_key,
                "assembly_accession_version": row.assembly_accession_version,
                "sequence_accession_version": row.sequence_accession_version,
                "start0": row.start0,
                "end0": row.end0,
                "interval_basis": row.interval_basis,
                "issue_codes": list(row.quarantine_issue_codes),
            }
            for row in quarantine
        ],
        "output_files": [str(path) for path in paths],
        "manifest_sha256s": {
            "inclusion": inclusions.manifest_sha256,
            "adjudication": adjudication.manifest_sha256,
            "public_loci": public_loci.manifest_sha256,
            "public_assertions": public_assertions.manifest_sha256,
            "structured_activation": activation.manifest_sha256,
        },
        "file_sha256s": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        },
    }


def _run_apply(arguments: argparse.Namespace) -> dict[str, object]:
    """Atomically stage one checksum-named packet; never validate or publish it."""

    ncbi_artifact = _load_manifest(
        arguments.ncbi_artifact, NcbiTaxonomyArtifactManifest
    )
    ncbi_snapshot = _load_manifest(arguments.ncbi_snapshot, TaxonomySnapshotManifest)
    assembly_assignments = _load_manifest(
        arguments.assembly_assignments, AssemblyTaxonAssignmentManifest
    )
    ictv_artifact = _load_manifest(arguments.ictv_artifact, IctvArtifactManifest)
    ictv_snapshot = _load_manifest(arguments.ictv_snapshot, TaxonomySnapshotManifest)
    study_formal_mapping = _load_manifest(
        arguments.study_formal_mapping, StudyFormalMappingManifest
    )
    cohort = _load_manifest(arguments.cohort, AdjudicationCohortManifest)
    full_sequence_bundle = _load_manifest(
        arguments.bundle_manifest, FullSequenceBundleManifest
    )
    flank_request_plan = _load_manifest(
        arguments.request_plan, FlankEvidenceRequestPlan
    )
    flanks = _load_manifest(arguments.flank_evidence, FlankEvidenceManifest)
    inclusions = _load_manifest(arguments.inclusion_manifest, InclusionDecisionManifest)
    adjudication = _load_manifest(
        arguments.adjudication_manifest, StructuredAdjudicationManifest
    )
    public_loci = _load_manifest(
        arguments.public_locus_manifest, PublicLocusMembershipManifest
    )
    public_assertions = _load_manifest(
        arguments.public_assertion_manifest, PublicAssertionMembershipManifest
    )
    activation = _load_manifest(
        arguments.activation_manifest, StructuredActivationManifest
    )
    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with Session(engine, autoflush=False) as session, session.begin():
            report = stage_structured_activation_candidate(
                session,
                expected_activation_manifest_sha256=(
                    arguments.expected_activation_manifest_sha256
                ),
                ncbi_artifact=ncbi_artifact,
                ncbi_snapshot=ncbi_snapshot,
                assembly_assignments=assembly_assignments,
                ictv_artifact=ictv_artifact,
                ictv_snapshot=ictv_snapshot,
                study_formal_mapping=study_formal_mapping,
                cohort=cohort,
                full_sequence_bundle=full_sequence_bundle,
                flank_request_plan=flank_request_plan,
                flanks=flanks,
                inclusions=inclusions,
                adjudication=adjudication,
                public_loci=public_loci,
                public_assertions=public_assertions,
                activation=activation,
            )
    finally:
        engine.dispose()
    return report.model_dump(mode="json")


def _run_validation_request(arguments: argparse.Namespace) -> dict[str, object]:
    activation = _load_manifest(
        arguments.activation_manifest, StructuredActivationManifest
    )
    if activation.manifest_sha256 != arguments.expected_activation_manifest_sha256:
        raise ValueError("expected structured activation checksum does not match")
    ncbi_artifact = _load_manifest(
        arguments.ncbi_artifact, NcbiTaxonomyArtifactManifest
    )
    ncbi_snapshot = _load_manifest(arguments.ncbi_snapshot, TaxonomySnapshotManifest)
    ictv_artifact = _load_manifest(arguments.ictv_artifact, IctvArtifactManifest)
    ictv_snapshot = _load_manifest(arguments.ictv_snapshot, TaxonomySnapshotManifest)
    m1_gate = load_m1_gate_evidence(
        source_manifest_path=arguments.source_manifest,
        expected_source_manifest_sha256=arguments.source_manifest_sha256,
        source_audit_path=arguments.source_audit,
        expected_source_audit_sha256=arguments.source_audit_sha256,
    )
    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ",
            postgresql_readonly=True,
        ) as connection:
            with Session(bind=connection, autoflush=False) as session, session.begin():
                request = build_candidate_release_validation_request(
                    session,
                    activation=activation,
                    m1_gate=m1_gate,
                    ncbi_artifact=ncbi_artifact,
                    ncbi_snapshot=ncbi_snapshot,
                    ictv_artifact=ictv_artifact,
                    ictv_snapshot=ictv_snapshot,
                )
    finally:
        engine.dispose()
    return validation_request_payload(request)


def _load_manifest[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValidationError) as exc:
        raise ValueError(f"candidate manifest failed strict validation: {path}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build checksum-frozen V0 activation candidate artifacts offline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_flank_parser(subparsers.add_parser("flanks", help="export and assess 71 primaries"))
    _add_taxonomy_parser(
        subparsers.add_parser("taxonomy", help="parse NCBI/ICTV and build mapping manifests")
    )
    _add_activation_parser(
        subparsers.add_parser("activation", help="seal policy and public-membership candidates")
    )
    _add_apply_parser(
        subparsers.add_parser("apply", help="atomically stage one exact candidate packet")
    )
    _add_validation_request_parser(
        subparsers.add_parser(
            "export-validation-request",
            help="project one passing request from the staged candidate",
        )
    )
    return parser


def _add_flank_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path(".artifacts/v0_activation/flanks/source_high_full_sequences.json"),
    )
    parser.add_argument("--bundle-sha256", default=V0_SEQUENCE_BUNDLE_SHA256)
    parser.add_argument("--bundle-byte-size", type=int, default=V0_SEQUENCE_BUNDLE_BYTE_SIZE)
    parser.add_argument("--bundle-source-uri", default=V0_SEQUENCE_SOURCE_URI)
    parser.add_argument("--bundle-retrieved-at", default=V0_SEQUENCE_RETRIEVED_AT)
    parser.add_argument("--tool-version", default=V0_SEQUENCE_TOOL_VERSION)
    parser.add_argument("--source-manifest-sha256", default=V0_SOURCE_MANIFEST_SHA256)
    parser.add_argument("--source-audit-sha256", default=V0_SOURCE_AUDIT_SHA256)
    parser.add_argument("--assessed-by", default="method:v0-flank-context-v1")
    parser.add_argument("--assessed-at", required=True)


def _add_taxonomy_parser(parser: argparse.ArgumentParser) -> None:
    taxonomy_dir = Path(".artifacts/v0_activation/taxonomy")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--taxdump", type=Path, default=taxonomy_dir / "taxdump.tar.gz")
    parser.add_argument("--taxdump-sha256", default=V0_NCBI_TAXDUMP_SHA256)
    parser.add_argument("--taxdump-byte-size", type=int, default=V0_NCBI_TAXDUMP_BYTE_SIZE)
    parser.add_argument("--taxdump-md5", default=V0_NCBI_TAXDUMP_MD5)
    parser.add_argument("--ncbi-version", required=True)
    parser.add_argument(
        "--ncbi-source-uri",
        default="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
    )
    parser.add_argument(
        "--ncbi-checksum-source-uri",
        default="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.md5",
    )
    parser.add_argument("--ncbi-retrieved-at", required=True)
    parser.add_argument(
        "--ncbi-policy-source-uri",
        default="https://www.ncbi.nlm.nih.gov/home/about/policies/",
    )
    parser.add_argument("--ncbi-policy-retrieved-at", required=True)
    parser.add_argument("--ncbi-policy-capture", type=Path, required=True)
    parser.add_argument("--ncbi-policy-capture-sha256", required=True)
    parser.add_argument(
        "--assembly-report",
        type=Path,
        default=Path(".artifacts/milestone1/ncbi/assembly_data_report.jsonl"),
    )
    parser.add_argument("--assembly-report-sha256", default=V0_ASSEMBLY_REPORT_SHA256)
    parser.add_argument(
        "--assembly-report-byte-size", type=int, default=V0_ASSEMBLY_REPORT_BYTE_SIZE
    )
    parser.add_argument(
        "--msl",
        type=Path,
        default=taxonomy_dir / "ICTV_Master_Species_List_2025_MSL41.v1.xlsx",
    )
    parser.add_argument("--msl-sha256", default=V0_ICTV_MSL_SHA256)
    parser.add_argument("--msl-byte-size", type=int, default=V0_ICTV_MSL_BYTE_SIZE)
    parser.add_argument("--vmr", type=Path, default=taxonomy_dir / "VMR_MSL41.v1.20260729.xlsx")
    parser.add_argument("--vmr-sha256", default=V0_ICTV_VMR_SHA256)
    parser.add_argument("--vmr-byte-size", type=int, default=V0_ICTV_VMR_BYTE_SIZE)
    parser.add_argument("--msl-source-uri", required=True)
    parser.add_argument("--vmr-source-uri", required=True)
    parser.add_argument("--ictv-retrieved-at", required=True)
    parser.add_argument("--ictv-policy-source-uri", default="https://ictv.global/taxonomy")
    parser.add_argument("--ictv-policy-retrieved-at", required=True)
    parser.add_argument("--ictv-policy-capture", type=Path, required=True)
    parser.add_argument("--ictv-policy-capture-sha256", required=True)
    parser.add_argument("--msl-upstream-sha256")
    parser.add_argument("--msl-checksum-source-uri")
    parser.add_argument("--vmr-upstream-sha256")
    parser.add_argument("--vmr-checksum-source-uri")
    parser.add_argument("--proposal-evidence", type=Path, required=True)
    parser.add_argument("--proposal-evidence-sha256", required=True)
    parser.add_argument(
        "--proposal-evidence-locator", default="ICTV proposal 2024.010D rename table"
    )
    parser.add_argument("--study-snapshot-key", required=True)
    parser.add_argument("--study-order-term-key", required=True)
    parser.add_argument("--study-family-term-key")


def _add_activation_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--request-plan", type=Path, required=True)
    parser.add_argument("--flank-evidence", type=Path, required=True)
    parser.add_argument("--ncbi-artifact", type=Path, required=True)
    parser.add_argument("--ncbi-snapshot", type=Path, required=True)
    parser.add_argument("--assembly-assignments", type=Path, required=True)
    parser.add_argument("--ictv-artifact", type=Path, required=True)
    parser.add_argument("--ictv-snapshot", type=Path, required=True)
    parser.add_argument("--study-formal-mapping", type=Path, required=True)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("data/manifests/milestone1_zhao_v4_data_s1.json"),
    )
    parser.add_argument("--source-manifest-sha256", default=V0_SOURCE_MANIFEST_SHA256)
    parser.add_argument(
        "--source-audit",
        type=Path,
        default=Path("data/audits/milestone1_data_s1_import_audit.json"),
    )
    parser.add_argument("--source-audit-sha256", default=V0_SOURCE_AUDIT_SHA256)


def _add_apply_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-activation-manifest-sha256", required=True)
    parser.add_argument("--activation-manifest", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--request-plan", type=Path, required=True)
    parser.add_argument("--flank-evidence", type=Path, required=True)
    parser.add_argument("--ncbi-artifact", type=Path, required=True)
    parser.add_argument("--ncbi-snapshot", type=Path, required=True)
    parser.add_argument("--assembly-assignments", type=Path, required=True)
    parser.add_argument("--ictv-artifact", type=Path, required=True)
    parser.add_argument("--ictv-snapshot", type=Path, required=True)
    parser.add_argument("--study-formal-mapping", type=Path, required=True)
    parser.add_argument("--inclusion-manifest", type=Path, required=True)
    parser.add_argument("--adjudication-manifest", type=Path, required=True)
    parser.add_argument("--public-locus-manifest", type=Path, required=True)
    parser.add_argument("--public-assertion-manifest", type=Path, required=True)


def _add_validation_request_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-activation-manifest-sha256", required=True)
    parser.add_argument("--activation-manifest", type=Path, required=True)
    parser.add_argument("--ncbi-artifact", type=Path, required=True)
    parser.add_argument("--ncbi-snapshot", type=Path, required=True)
    parser.add_argument("--ictv-artifact", type=Path, required=True)
    parser.add_argument("--ictv-snapshot", type=Path, required=True)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("data/manifests/milestone1_zhao_v4_data_s1.json"),
    )
    parser.add_argument("--source-manifest-sha256", default=V0_SOURCE_MANIFEST_SHA256)
    parser.add_argument(
        "--source-audit",
        type=Path,
        default=Path("data/audits/milestone1_data_s1_import_audit.json"),
    )
    parser.add_argument("--source-audit-sha256", default=V0_SOURCE_AUDIT_SHA256)


if __name__ == "__main__":
    raise SystemExit(main())
