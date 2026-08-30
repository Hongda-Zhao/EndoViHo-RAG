from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

import eve_relation_rag.cli as cli_module
from eve_relation_rag.cli import app
from eve_relation_rag.releases.publication import (
    DatasetPublicationReport,
    DatasetReceiptReport,
)
from eve_relation_rag.releases.receipt_integrity import (
    ApprovedDatasetValidationInput,
    build_approved_validation_input,
    build_dataset_activation_evidence,
    build_dataset_candidate_activation_evidence,
    build_dataset_candidate_validation_input,
    validation_request_payload,
)
from eve_relation_rag.retrieval.structured.capability import LineageRole
from tests.test_release_validator import _request

GRAPH_SHA256 = "e" * 64
MANIFEST_SHA256 = "d" * 64
RECEIPT_SHA256 = "f" * 64
RECEIPT_KEY = f"dataset-receipt:sha256:{'a' * 64}"
runner = CliRunner()


def _approved() -> ApprovedDatasetValidationInput:
    candidate_activation = build_dataset_candidate_activation_evidence(
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
    candidate = build_dataset_candidate_validation_input(
        release_schema_version="endoviho-structured-v0",
        release_manifest_sha256=MANIFEST_SHA256,
        expected_dependency_graph_sha256=GRAPH_SHA256,
        candidate_activation_evidence=candidate_activation,
        complete_lineage_closure_roles=(
            "assembly_source_taxonomy",
            "formal_viral_taxonomy",
        ),
        request=_request(),
    )
    activation_evidence = build_dataset_activation_evidence(
        candidate_validation_input_sha256=candidate.input_sha256,
        release_key=candidate.release_key,
        clean_rebuild_report_sha256="c" * 64,
        structured_benchmark_report_sha256="d" * 64,
        hybrid_benchmark_report_sha256="e" * 64,
        human_review_report_sha256="f" * 64,
    )
    return build_approved_validation_input(
        candidate_validation_input=candidate,
        activation_evidence=activation_evidence,
    )


def test_structured_release_admin_help_exposes_exact_checksum_boundaries() -> None:
    candidate = runner.invoke(
        app,
        ["structured", "release-prepare-candidate-validation-input", "--help"],
    )
    prepare = runner.invoke(
        app,
        ["structured", "release-prepare-validation-input", "--help"],
    )
    validate = runner.invoke(app, ["structured", "release-validate", "--help"])
    publish = runner.invoke(app, ["structured", "release-publish", "--help"])

    assert candidate.exit_code == 0, candidate.output
    assert prepare.exit_code == 0, prepare.output
    assert validate.exit_code == 0, validate.output
    assert publish.exit_code == 0, publish.output
    assert "--request-path" in unstyle(candidate.stdout)
    assert "--candidate-input-path" in unstyle(prepare.stdout)
    assert "--approved-input-sha256" in unstyle(validate.stdout)
    assert "--expected-manifest-sha256" in unstyle(publish.stdout)
    assert "--expected-receipt-sha256" in unstyle(publish.stdout)


def test_release_prepare_cli_builds_read_only_approval_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _approved()
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(validation_request_payload(_request())),
        encoding="utf-8",
    )
    candidate = approved.candidate_validation_input
    activation_path = tmp_path / "candidate-activation-evidence.json"
    activation_path.write_text(
        candidate.candidate_activation_evidence.model_dump_json(), encoding="utf-8"
    )
    observed: list[tuple[object, tuple[LineageRole, ...]]] = []

    def prepare(
        engine: object,
        *,
        request: object,
        candidate_activation_evidence: object,
        complete_lineage_closure_roles: tuple[LineageRole, ...],
    ) -> object:
        observed.append((engine, complete_lineage_closure_roles))
        assert request == _request()
        assert candidate_activation_evidence == candidate.candidate_activation_evidence
        return candidate

    engine = object()
    monkeypatch.setattr(cli_module, "get_engine", lambda: engine)
    monkeypatch.setattr(cli_module, "prepare_dataset_candidate_validation_input", prepare)
    result = runner.invoke(
        app,
        [
            "structured",
            "release-prepare-candidate-validation-input",
            "--request-path",
            str(request_path),
            "--candidate-activation-evidence-path",
            str(activation_path),
            "--approved-candidate-activation-evidence-sha256",
            candidate.candidate_activation_evidence_sha256,
            "--include-study-viral-lineage",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["input_sha256"] == candidate.input_sha256
    assert observed == [
        (
            engine,
            (
                "assembly_source_taxonomy",
                "formal_viral_taxonomy",
                "study_viral_lineage",
            ),
        )
    ]


def test_release_validate_cli_loads_exact_approved_input_and_emits_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _approved()
    input_path = tmp_path / "approved.json"
    input_path.write_text(approved.model_dump_json(), encoding="utf-8")
    observed: list[tuple[ApprovedDatasetValidationInput, str]] = []

    def record(
        _engine: object,
        *,
        approved_input: ApprovedDatasetValidationInput,
        approved_input_sha256: str,
    ) -> DatasetReceiptReport:
        observed.append((approved_input, approved_input_sha256))
        return DatasetReceiptReport(
            receipt_key=RECEIPT_KEY,
            receipt_sha256=RECEIPT_SHA256,
            release_key=approved.release_key,
            status="validated",
            replayed=False,
        )

    monkeypatch.setattr(cli_module, "get_engine", object)
    monkeypatch.setattr(cli_module, "record_dataset_validation_receipt", record)
    result = runner.invoke(
        app,
        [
            "structured",
            "release-validate",
            "--input-path",
            str(input_path),
            "--approved-input-sha256",
            approved.input_sha256,
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["receipt_sha256"] == RECEIPT_SHA256
    assert observed == [(approved, approved.input_sha256)]


def test_release_validate_cli_refuses_unapproved_input_checksum(tmp_path: Path) -> None:
    approved = _approved()
    input_path = tmp_path / "approved.json"
    input_path.write_text(approved.model_dump_json(), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "structured",
            "release-validate",
            "--input-path",
            str(input_path),
            "--approved-input-sha256",
            "0" * 64,
        ],
    )

    assert result.exit_code == 4
    assert result.stdout == ""
    assert "checksum does not match" in json.loads(result.stderr)["message"]


def test_release_publish_cli_passes_all_exact_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _approved()
    observed: list[tuple[str, str, str]] = []

    def publish(
        _engine: object,
        *,
        release_key: str,
        expected_manifest_sha256: str,
        expected_receipt_sha256: str,
    ) -> DatasetPublicationReport:
        observed.append((release_key, expected_manifest_sha256, expected_receipt_sha256))
        return DatasetPublicationReport(
            release_key=release_key,
            manifest_sha256=expected_manifest_sha256,
            receipt_sha256=expected_receipt_sha256,
            status="published",
            published_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
            replayed=False,
        )

    monkeypatch.setattr(cli_module, "get_engine", object)
    monkeypatch.setattr(cli_module, "publish_dataset_release", publish)
    result = runner.invoke(
        app,
        [
            "structured",
            "release-publish",
            "--release-key",
            approved.release_key,
            "--expected-manifest-sha256",
            MANIFEST_SHA256,
            "--expected-receipt-sha256",
            RECEIPT_SHA256,
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "published"
    assert observed == [(approved.release_key, MANIFEST_SHA256, RECEIPT_SHA256)]
