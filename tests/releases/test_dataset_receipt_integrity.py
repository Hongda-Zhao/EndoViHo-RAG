from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from eve_relation_rag.releases.receipt_integrity import (
    DatasetCandidateValidationInput,
    DatasetReceiptIntegrityError,
    TrustedDatasetReceiptEvidence,
    build_approved_validation_input,
    build_dataset_activation_evidence,
    build_dataset_candidate_activation_evidence,
    build_dataset_candidate_validation_input,
    build_trusted_receipt_evidence,
    load_approved_validation_input,
    load_validation_request,
    receipt_identity,
    validate_persisted_dataset_receipt,
    validation_request_from_payload,
    validation_request_payload,
)
from eve_relation_rag.retrieval.structured.capability import LineageRole
from tests.test_release_validator import _request

MANIFEST_SHA256 = "d" * 64
GRAPH_SHA256 = "e" * 64


def _activation_evidence():
    candidate = _candidate_input()
    return build_dataset_activation_evidence(
        candidate_validation_input_sha256=candidate.input_sha256,
        release_key=_request().release_key,
        clean_rebuild_report_sha256="c" * 64,
        structured_benchmark_report_sha256="d" * 64,
        hybrid_benchmark_report_sha256="e" * 64,
        human_review_report_sha256="f" * 64,
    )


def _candidate_input(
    complete_roles: tuple[LineageRole, ...] = (
        "assembly_source_taxonomy",
        "formal_viral_taxonomy",
    ),
) -> DatasetCandidateValidationInput:
    activation = build_dataset_candidate_activation_evidence(
        release_key=_request().release_key,
        structured_activation_manifest_sha256=MANIFEST_SHA256,
        source_manifest_sha256="1" * 64,
        source_audit_sha256="2" * 64,
        ncbi_artifact_manifest_sha256="3" * 64,
        ncbi_snapshot_manifest_sha256="4" * 64,
        ictv_artifact_manifest_sha256="5" * 64,
        ictv_snapshot_manifest_sha256="6" * 64,
        flank_manifest_sha256="7" * 64,
        inclusion_manifest_sha256="8" * 64,
        adjudication_manifest_sha256="9" * 64,
        public_locus_membership_manifest_sha256="a" * 64,
        public_assertion_membership_manifest_sha256="b" * 64,
    )
    return build_dataset_candidate_validation_input(
        release_schema_version="endoviho-structured-v0",
        release_manifest_sha256=MANIFEST_SHA256,
        expected_dependency_graph_sha256=GRAPH_SHA256,
        candidate_activation_evidence=activation,
        complete_lineage_closure_roles=complete_roles,
        request=_request(),
    )


def _approved():
    candidate = _candidate_input()
    return build_approved_validation_input(
        candidate_validation_input=candidate,
        activation_evidence=build_dataset_activation_evidence(
            candidate_validation_input_sha256=candidate.input_sha256,
            release_key=candidate.release_key,
            clean_rebuild_report_sha256="c" * 64,
            structured_benchmark_report_sha256="d" * 64,
            hybrid_benchmark_report_sha256="e" * 64,
            human_review_report_sha256="f" * 64,
        ),
    )


def test_validation_request_payload_round_trips_without_coercion() -> None:
    request = _request()
    payload = validation_request_payload(request)

    assert validation_request_from_payload(payload) == request
    forged = dict(payload)
    forged["unexpected"] = True
    with pytest.raises(DatasetReceiptIntegrityError, match="not canonical"):
        validation_request_from_payload(forged)


def test_approved_input_is_self_hashed_and_requires_external_checksum(tmp_path) -> None:
    approved = _approved()
    path = tmp_path / "approved.json"
    path.write_text(approved.model_dump_json(), encoding="utf-8")

    assert (
        load_approved_validation_input(
            path,
            approved_input_sha256=approved.input_sha256,
        )
        == approved
    )
    with pytest.raises(DatasetReceiptIntegrityError, match="does not match"):
        load_approved_validation_input(path, approved_input_sha256="0" * 64)


def test_validation_request_file_loader_is_canonical(tmp_path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(validation_request_payload(_request())), encoding="utf-8")

    assert load_validation_request(path) == _request()

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(DatasetReceiptIntegrityError, match="JSON object"):
        load_validation_request(path)


def test_receipt_replays_validator_and_binds_every_persisted_column() -> None:
    approved = _approved()
    evidence, request, report = build_trusted_receipt_evidence(
        approved,
        dependency_graph_sha256=GRAPH_SHA256,
    )
    receipt_key, receipt_sha256 = receipt_identity(evidence)

    assert request == _request()
    assert report.valid is True
    replayed = validate_persisted_dataset_receipt(
        release_key=approved.release_key,
        release_schema_version=approved.release_schema_version,
        release_manifest_sha256=approved.release_manifest_sha256,
        current_dependency_graph_sha256=GRAPH_SHA256,
        receipt_key=receipt_key,
        receipt_status="passed",
        receipt_trusted=True,
        receipt_manifest_sha256=approved.release_manifest_sha256,
        receipt_dependency_graph_sha256=GRAPH_SHA256,
        receipt_validation_request_sha256=approved.validation_request_sha256,
        receipt_activation_evidence_sha256=approved.activation_evidence_sha256,
        receipt_candidate_validation_input_sha256=(
            approved.candidate_validation_input_sha256
        ),
        receipt_validation_input_sha256=approved.input_sha256,
        receipt_validation_report_sha256=evidence.validation_report_sha256,
        receipt_validator_code_sha256=approved.validator_code_sha256,
        receipt_sha256=receipt_sha256,
        receipt_complete_lineage_closure_roles=list(approved.complete_lineage_closure_roles),
        validation_evidence=evidence.model_dump(mode="json"),
    )
    assert replayed == evidence

    with pytest.raises(DatasetReceiptIntegrityError, match="does not match"):
        validate_persisted_dataset_receipt(
            release_key=approved.release_key,
            release_schema_version=approved.release_schema_version,
            release_manifest_sha256=approved.release_manifest_sha256,
            current_dependency_graph_sha256="f" * 64,
            receipt_key=receipt_key,
            receipt_status="passed",
            receipt_trusted=True,
            receipt_manifest_sha256=approved.release_manifest_sha256,
            receipt_dependency_graph_sha256=GRAPH_SHA256,
            receipt_validation_request_sha256=approved.validation_request_sha256,
            receipt_activation_evidence_sha256=approved.activation_evidence_sha256,
            receipt_candidate_validation_input_sha256=(
                approved.candidate_validation_input_sha256
            ),
            receipt_validation_input_sha256=approved.input_sha256,
            receipt_validation_report_sha256=evidence.validation_report_sha256,
            receipt_validator_code_sha256=approved.validator_code_sha256,
            receipt_sha256=receipt_sha256,
            receipt_complete_lineage_closure_roles=list(approved.complete_lineage_closure_roles),
            validation_evidence=evidence.model_dump(mode="json"),
        )


def test_tampered_report_cannot_be_revalidated() -> None:
    approved = _approved()
    evidence, _request_value, _report = build_trusted_receipt_evidence(
        approved,
        dependency_graph_sha256=GRAPH_SHA256,
    )
    payload = evidence.model_dump(mode="json")
    payload["validation_report"] = dict(payload["validation_report"])
    payload["validation_report"]["valid"] = False

    with pytest.raises(ValidationError, match="does not replay exactly"):
        TrustedDatasetReceiptEvidence.model_validate_json(json.dumps(payload))


def test_receipt_requires_the_separately_approved_dependency_graph() -> None:
    approved = _approved()

    with pytest.raises(DatasetReceiptIntegrityError, match="dependency graph"):
        build_trusted_receipt_evidence(
            approved,
            dependency_graph_sha256="f" * 64,
        )


def test_noncanonical_lineage_role_order_is_refused() -> None:
    approved = _approved()
    payload = approved.model_dump(mode="json")
    payload["complete_lineage_closure_roles"] = tuple(
        reversed(approved.complete_lineage_closure_roles)
    )

    with pytest.raises(ValidationError, match="canonically ordered"):
        type(approved).model_validate_json(json.dumps(payload), strict=True)


def test_extended_lineage_closure_role_is_optional_and_canonically_ordered() -> None:
    candidate = _candidate_input(
        complete_roles=(
            "assembly_source_taxonomy",
            "formal_viral_taxonomy",
            "extended_viral_lineage",
        )
    )

    assert candidate.complete_lineage_closure_roles[-1] == "extended_viral_lineage"
