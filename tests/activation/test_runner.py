from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from eve_relation_rag.activation.release_state import DatasetPublicationEvidence
from eve_relation_rag.activation.runner import (
    ActivationRunnerError,
    build_corpus_publication_evidence,
    build_dataset_publication_evidence,
    build_structured_route_benchmark_report,
    run_candidate_route_answers,
)
from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.cli_v0 import v0_app
from eve_relation_rag.hybrid.contracts import ExecutionFlags, StructuredRouteAnswer
from eve_relation_rag.literature.publication import PublicationReport
from eve_relation_rag.releases.publication import DatasetPublicationReport
from eve_relation_rag.releases.receipt_integrity import (
    build_dataset_candidate_activation_evidence,
    build_dataset_candidate_validation_input,
    structured_candidate_capability_sha256,
)
from eve_relation_rag.retrieval.structured.candidate_gate import (
    ValidatedCandidateReleaseGate,
)
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.results import (
    QuerySuccess,
    ValidationCandidateReleaseRef,
)
from tests.generation.test_human_review_v0 import (
    RELEASE_KEY,
    RELEASE_MANIFEST_SHA,
    _definition_and_answers,
    _response,
)
from tests.test_release_validator import _request


def _candidate_input():
    activation = build_dataset_candidate_activation_evidence(
        release_key=RELEASE_KEY,
        structured_activation_manifest_sha256=RELEASE_MANIFEST_SHA,
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
        release_manifest_sha256=RELEASE_MANIFEST_SHA,
        expected_dependency_graph_sha256="c" * 64,
        candidate_activation_evidence=activation,
        complete_lineage_closure_roles=(
            "assembly_source_taxonomy",
            "formal_viral_taxonomy",
        ),
        request=replace(_request(), release_key=RELEASE_KEY),
    )


def _structured_candidate_answers() -> tuple[StructuredRouteAnswer, ...]:
    candidate = _candidate_input()
    capability_sha256 = structured_candidate_capability_sha256(candidate)
    answers: list[StructuredRouteAnswer] = []
    for ordinal in range(1, 11):
        _, _, hybrid = _response(ordinal)
        published = hybrid.query_success.structured_result.release
        candidate_ref = ValidationCandidateReleaseRef(
            dataset_key=published.dataset_key,
            release_key=candidate.release_key,
            schema_version=published.schema_version,
            status="validation_candidate",
            manifest_sha256=candidate.release_manifest_sha256,
            candidate_created_at=published.published_at,
            candidate_validation_input_sha256=candidate.input_sha256,
            candidate_capability_sha256=capability_sha256,
        )
        result = hybrid.query_success.structured_result.model_copy(
            update={"release": candidate_ref}
        )
        success = QuerySuccess.model_validate(
            hybrid.query_success.model_dump(mode="python")
            | {"structured_result": result}
        )
        answers.append(
            StructuredRouteAnswer(
                original_request=hybrid.original_request.model_copy(
                    update={
                        "question": success.query_plan.original_question,
                        "corpus_release_key": None,
                        "literature_top_k": None,
                    }
                ),
                query_success=success,
                structured_text="candidate structured benchmark result",
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        )
    return tuple(answers)


def test_structured_report_retains_candidate_provenance(tmp_path: Path) -> None:
    definition, _ = _definition_and_answers(tmp_path)
    candidate = _candidate_input()

    report = build_structured_route_benchmark_report(
        definition=definition,
        candidate_validation_input=candidate,
        responses=_structured_candidate_answers(),
    )

    assert report.route == "structured"
    assert len(report.cases) == 10
    assert all(
        case.structured_response is not None
        and case.structured_response.query_success.structured_result.release.status
        == "validation_candidate"
        for case in report.cases
    )


def test_structured_report_rejects_published_response(tmp_path: Path) -> None:
    definition, _ = _definition_and_answers(tmp_path)
    responses = list(_structured_candidate_answers())
    _, _, published = _response(1)
    responses[0] = StructuredRouteAnswer(
        original_request=responses[0].original_request,
        query_success=published.query_success,
        structured_text="published response must not become candidate evidence",
        execution=responses[0].execution,
    )

    with pytest.raises(ActivationRunnerError, match="published provenance"):
        build_structured_route_benchmark_report(
            definition=definition,
            candidate_validation_input=_candidate_input(),
            responses=tuple(responses),
        )


def test_bound_candidate_gate_rejects_another_request_key_before_database() -> None:
    gate = ValidatedCandidateReleaseGate(cast(Engine, object())).bind(
        _candidate_input()
    )

    with pytest.raises(RetrievalRefusal, match="bound validation candidate"):
        gate.authorize("release:endoviho-rag:v0:20260830:999")


def test_candidate_route_runner_stops_at_first_non_typed_response(
    tmp_path: Path,
) -> None:
    class RejectingApplication:
        def __init__(self) -> None:
            self.calls = 0

        def query(self, _request: object) -> object:
            self.calls += 1
            return object()

    definition, _ = _definition_and_answers(tmp_path)
    application = RejectingApplication()

    with pytest.raises(ActivationRunnerError, match="structured.*case 01"):
        run_candidate_route_answers(
            application=cast(RagQueryApplication, application),
            definition=definition,
            candidate_validation_input=_candidate_input(),
        )
    assert application.calls == 1


def test_publication_evidence_seals_exact_published_reports() -> None:
    published_at = datetime(2026, 8, 30, 12, 34, 56, 123456, tzinfo=UTC)
    dataset = build_dataset_publication_evidence(
        DatasetPublicationReport(
            release_key=RELEASE_KEY,
            manifest_sha256="1" * 64,
            receipt_sha256="2" * 64,
            status="published",
            published_at=published_at,
            replayed=False,
        ),
        receipt_key=f"dataset-receipt:sha256:{'2' * 64}",
    )
    corpus = build_corpus_publication_evidence(
        PublicationReport(
            corpus_release_key="corpus:endoviho-rag:v0:20260830:001",
            manifest_sha256="3" * 64,
            receipt_sha256="4" * 64,
            status="published",
            published_at=published_at,
            replayed=True,
        ),
        receipt_key=f"corpus-receipt:sha256:{'4' * 64}",
    )

    assert dataset.status == corpus.status == "published"
    assert dataset.published_at.microsecond == 123456
    assert corpus.published_at.microsecond == 123456

    with pytest.raises(ActivationRunnerError, match="not published"):
        build_corpus_publication_evidence(
            PublicationReport(
                corpus_release_key="corpus:endoviho-rag:v0:20260830:001",
                manifest_sha256="3" * 64,
                receipt_sha256="4" * 64,
                status="validated",
                published_at=published_at,
                replayed=False,
            ),
            receipt_key=f"corpus-receipt:sha256:{'4' * 64}",
        )


def test_publication_evidence_cli_requires_raw_report_approval(tmp_path: Path) -> None:
    report = DatasetPublicationReport(
        release_key=RELEASE_KEY,
        manifest_sha256="1" * 64,
        receipt_sha256="2" * 64,
        status="published",
        published_at=datetime(2026, 8, 30, 12, 34, 56, tzinfo=UTC),
        replayed=False,
    )
    report_path = tmp_path / "publication-report.json"
    report_path.write_text(report.model_dump_json(), encoding="utf-8")
    output_path = tmp_path / "publication-evidence.json"
    args = [
        "dataset-publication-evidence-build",
        "--report-path",
        str(report_path),
        "--approved-report-file-sha256",
        hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "--receipt-key",
        f"dataset-receipt:sha256:{'2' * 64}",
        "--output-path",
        str(output_path),
    ]

    result = CliRunner().invoke(v0_app, args)

    assert result.exit_code == 0
    evidence = DatasetPublicationEvidence.model_validate_json(output_path.read_bytes())
    assert evidence.receipt_sha256 == "2" * 64

    rejected_output = tmp_path / "rejected.json"
    args[args.index("--approved-report-file-sha256") + 1] = "0" * 64
    args[args.index("--output-path") + 1] = str(rejected_output)
    rejected = CliRunner().invoke(v0_app, args)
    assert rejected.exit_code == 4
    assert not rejected_output.exists()
