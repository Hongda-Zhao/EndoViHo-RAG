"""Pure, replayable builders for the pre-receipt V0 activation sequence.

These builders do not publish releases and do not turn caller-authored booleans into
evidence.  They only package already typed route, review, receipt, and rebuild outputs
after cross-validating their exact candidate identities.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from eve_relation_rag.activation.release_state import (
    V0_CORPUS_IMPORTER_CODE_SHA256,
    V0_CORPUS_POLICY_CODE_SHA256,
    CorpusPublicationEvidence,
    CorpusValidationExport,
    DatasetPublicationEvidence,
    V0CleanActivationRebuildReport,
    V0RouteBenchmarkCase,
    V0RouteBenchmarkReport,
    activation_validator_code_sha256,
)
from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.generation.human_review import (
    HumanBenchmarkDefinition,
    HumanReviewEvaluation,
    HumanReviewPacket,
)
from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBindingManifest,
    HybridRouteAnswer,
    RagQueryRequest,
    StrictFrozenSchema,
    StructuredRouteAnswer,
    canonical_model_sha256,
    canonical_self_sha256,
)
from eve_relation_rag.literature.contracts import CorpusReleaseKey, Sha256
from eve_relation_rag.literature.publication import PublicationReport
from eve_relation_rag.literature.receipt_integrity import (
    receipt_identity as corpus_receipt_identity,
)
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.releases.publication import DatasetPublicationReport
from eve_relation_rag.releases.receipt_integrity import (
    DatasetActivationEvidence,
    DatasetCandidateValidationInput,
    build_dataset_activation_evidence,
    release_validator_code_sha256,
    structured_activation_policy_code_sha256,
    structured_candidate_capability_sha256,
)
from eve_relation_rag.retrieval.structured.results import (
    ValidationCandidateReleaseRef,
)

_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_PERFECT_METRIC = "1.000000000000"


class ActivationRunnerError(RuntimeError):
    """A replay input is incomplete, failing, or bound to another candidate."""


class V0CleanActivationRebuildInput(StrictFrozenSchema):
    """Complete immutable input required before an empty-database rebuild starts."""

    input_schema_version: Literal["v0-clean-activation-rebuild-input-v1"]
    activation_evidence_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    release_key: str
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    candidate_validation_input_sha256: Sha256
    dataset_validation_request_sha256: Sha256
    dependency_graph_sha256: Sha256
    candidate_capability_sha256: Sha256
    corpus_receipt_sha256: Sha256
    corpus_rebuild_report_sha256: Sha256
    structured_benchmark_report_sha256: Sha256
    hybrid_benchmark_report_sha256: Sha256
    human_review_evaluation_sha256: Sha256
    dependency_lock_sha256: Sha256
    dataset_validator_code_sha256: Sha256
    activation_policy_code_sha256: Sha256
    activation_state_validator_code_sha256: Sha256
    corpus_validator_code_sha256: Sha256
    corpus_importer_code_sha256: Sha256
    corpus_policy_code_sha256: Sha256
    input_sha256: Sha256

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if self.input_sha256 != canonical_self_sha256(self, "input_sha256"):
            raise ValueError("clean activation rebuild input checksum does not match")
        return self


def _trusted[ModelT: BaseModel](value: ModelT, schema: type[ModelT]) -> ModelT:
    try:
        return schema.model_validate_json(value.model_dump_json(), strict=True)
    except Exception as exc:
        raise ActivationRunnerError("activation runner received invalid typed evidence") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ActivationRunnerError(message)


def _question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def _candidate_identity(
    candidate: DatasetCandidateValidationInput,
) -> tuple[DatasetCandidateValidationInput, str]:
    candidate = _trusted(candidate, DatasetCandidateValidationInput)
    _require(
        candidate.validator_code_sha256 == release_validator_code_sha256()
        and candidate.candidate_activation_evidence.activation_policy_code_sha256
        == structured_activation_policy_code_sha256(),
        "candidate validator or activation policy identity is stale",
    )
    return candidate, structured_candidate_capability_sha256(candidate)


def _require_candidate_response(
    response: StructuredRouteAnswer,
    *,
    candidate: DatasetCandidateValidationInput,
    candidate_capability_sha256: str,
    expected_question: str,
) -> None:
    release = response.query_success.structured_result.release
    _require_candidate_release(
        release,
        candidate=candidate,
        candidate_capability_sha256=candidate_capability_sha256,
    )
    _require(
        response.original_request.release_key == candidate.release_key
        and response.original_request.corpus_release_key is None
        and response.original_request.question == expected_question,
        "structured response request does not bind the approved validation candidate",
    )


def _require_candidate_release(
    release: object,
    *,
    candidate: DatasetCandidateValidationInput,
    candidate_capability_sha256: str,
) -> None:
    _require(
        isinstance(release, ValidationCandidateReleaseRef),
        "benchmark response claims published provenance instead of validation candidate",
    )
    assert isinstance(release, ValidationCandidateReleaseRef)
    _require(
        release.release_key == candidate.release_key
        and release.manifest_sha256 == candidate.release_manifest_sha256
        and release.candidate_validation_input_sha256 == candidate.input_sha256
        and release.candidate_capability_sha256 == candidate_capability_sha256,
        "response provenance does not bind the approved validation candidate",
    )


def build_structured_route_benchmark_report(
    *,
    definition: HumanBenchmarkDefinition,
    candidate_validation_input: DatasetCandidateValidationInput,
    responses: tuple[StructuredRouteAnswer, ...],
) -> V0RouteBenchmarkReport:
    """Build the exact ten-case structured report from retained candidate outputs."""

    definition = _trusted(definition, HumanBenchmarkDefinition)
    candidate, capability_sha256 = _candidate_identity(candidate_validation_input)
    _require(len(responses) == 10, "structured benchmark requires exactly ten responses")
    _require(
        definition.release_key == candidate.release_key
        and definition.release_manifest_sha256 == candidate.release_manifest_sha256,
        "benchmark definition targets another structured candidate",
    )
    cases: list[V0RouteBenchmarkCase] = []
    for case_definition, response in zip(definition.cases, responses, strict=True):
        response = _trusted(response, StructuredRouteAnswer)
        _require_candidate_response(
            response,
            candidate=candidate,
            candidate_capability_sha256=capability_sha256,
            expected_question=case_definition.structured_question,
        )
        cases.append(
            V0RouteBenchmarkCase(
                case_ordinal=case_definition.case_ordinal,
                case_key=case_definition.case_key,
                question_sha256=_question_sha256(case_definition.structured_question),
                response_sha256=canonical_model_sha256(response),
                structured_response=response,
                result="passed",
            )
        )
    payload: dict[str, object] = {
        "benchmark_report_schema_version": "v0-route-benchmark-report-v1",
        "route": "structured",
        "release_key": candidate.release_key,
        "release_manifest_sha256": candidate.release_manifest_sha256,
        "candidate_validation_input_sha256": candidate.input_sha256,
        "dataset_validation_request_sha256": candidate.validation_request_sha256,
        "dependency_graph_sha256": candidate.expected_dependency_graph_sha256,
        "candidate_capability_sha256": capability_sha256,
        "corpus_release_key": None,
        "corpus_manifest_sha256": None,
        "corpus_receipt_sha256": None,
        "binding_manifest_sha256": None,
        "human_review_evaluation_sha256": None,
        "cases": tuple(cases),
        "report_sha256": "0" * 64,
    }
    payload["report_sha256"] = canonical_self_sha256(payload, "report_sha256")
    return V0RouteBenchmarkReport.model_validate(payload)


def run_candidate_route_answers(
    *,
    application: RagQueryApplication,
    definition: HumanBenchmarkDefinition,
    candidate_validation_input: DatasetCandidateValidationInput,
) -> tuple[tuple[StructuredRouteAnswer, ...], tuple[HybridRouteAnswer, ...]]:
    """Execute the frozen ten cases, in order, through the two bound candidate gates."""

    definition = _trusted(definition, HumanBenchmarkDefinition)
    candidate, capability_sha256 = _candidate_identity(candidate_validation_input)
    _require(
        definition.release_key == candidate.release_key
        and definition.release_manifest_sha256 == candidate.release_manifest_sha256,
        "benchmark definition targets another structured candidate",
    )
    structured_answers: list[StructuredRouteAnswer] = []
    for case in definition.cases:
        response = application.query(
            RagQueryRequest(
                release_key=definition.release_key,
                question=case.structured_question,
                page=PageSpec(limit=case.page_limit),
            )
        )
        _require(
            isinstance(response, StructuredRouteAnswer),
            f"structured candidate route failed at case {case.case_ordinal:02d}",
        )
        assert isinstance(response, StructuredRouteAnswer)
        _require_candidate_response(
            response,
            candidate=candidate,
            candidate_capability_sha256=capability_sha256,
            expected_question=case.structured_question,
        )
        structured_answers.append(response)

    hybrid_answers: list[HybridRouteAnswer] = []
    for case in definition.cases:
        response = application.query(
            RagQueryRequest(
                release_key=definition.release_key,
                corpus_release_key=definition.corpus_release_key,
                question=case.question,
                page=PageSpec(limit=case.page_limit),
                literature_top_k=case.literature_top_k,
            )
        )
        _require(
            isinstance(response, HybridRouteAnswer),
            f"hybrid candidate route failed at case {case.case_ordinal:02d}",
        )
        assert isinstance(response, HybridRouteAnswer)
        _require_candidate_release(
            response.query_success.structured_result.release,
            candidate=candidate,
            candidate_capability_sha256=capability_sha256,
        )
        _require(
            response.original_request.question == case.question
            and response.original_request.release_key == candidate.release_key
            and response.original_request.corpus_release_key == definition.corpus_release_key
            and response.retrieved_chunks.corpus_release_key == definition.corpus_release_key
            and response.retrieved_chunks.corpus_manifest_sha256
            == definition.corpus_manifest_sha256,
            "hybrid response does not bind the preregistered candidate case",
        )
        hybrid_answers.append(response)
    return tuple(structured_answers), tuple(hybrid_answers)


def _require_perfect_human_evaluation(evaluation: HumanReviewEvaluation) -> None:
    metrics = evaluation.metrics
    _require(
        evaluation.status == "passed"
        and not evaluation.issue_codes
        and metrics.case_count == 10
        and metrics.claim_count > 0
        and metrics.reviewed_claim_count == metrics.claim_count
        and metrics.supported_count == metrics.claim_count
        and metrics.partially_supported_count == 0
        and metrics.unsupported_count == 0
        and metrics.unreviewed_count == 0
        and all(
            metric == _PERFECT_METRIC
            for metric in (
                metrics.citation_existence,
                metrics.release_match,
                metrics.locator_validity,
                metrics.citation_coverage,
            )
        ),
        "human review evaluation does not pass every ten-case gate",
    )


def build_hybrid_route_benchmark_report(
    *,
    definition: HumanBenchmarkDefinition,
    candidate_validation_input: DatasetCandidateValidationInput,
    packet: HumanReviewPacket,
    evaluation: HumanReviewEvaluation,
    corpus_export: CorpusValidationExport,
    binding_manifest: HybridReleaseBindingManifest,
) -> V0RouteBenchmarkReport:
    """Build the hybrid report only from the exact reviewed ten candidate answers."""

    definition = _trusted(definition, HumanBenchmarkDefinition)
    candidate, capability_sha256 = _candidate_identity(candidate_validation_input)
    packet = _trusted(packet, HumanReviewPacket)
    evaluation = _trusted(evaluation, HumanReviewEvaluation)
    corpus_export = _trusted(corpus_export, CorpusValidationExport)
    binding_manifest = _trusted(binding_manifest, HybridReleaseBindingManifest)
    _require_perfect_human_evaluation(evaluation)
    corpus_key, corpus_sha256 = corpus_receipt_identity(corpus_export.receipt.validation_report)
    del corpus_key
    _require(
        definition.release_key == candidate.release_key
        and definition.release_manifest_sha256 == candidate.release_manifest_sha256
        and packet.definition_sha256 == definition.definition_sha256
        and packet.packet_sha256 == evaluation.packet_sha256
        and packet.release_key == candidate.release_key
        and packet.release_manifest_sha256 == candidate.release_manifest_sha256
        and packet.corpus_release_key == corpus_export.corpus_release.corpus_release_key
        and packet.corpus_manifest_sha256 == corpus_export.corpus_release.manifest_sha256
        and packet.binding_manifest_sha256 == binding_manifest.manifest_sha256,
        "hybrid benchmark inputs do not form one reviewed candidate run",
    )
    exact_bindings = tuple(
        item
        for item in binding_manifest.bindings
        if item.release_key == candidate.release_key
        and item.corpus_release_key == packet.corpus_release_key
        and item.release_manifest_sha256 == candidate.release_manifest_sha256
        and item.corpus_manifest_sha256 == packet.corpus_manifest_sha256
    )
    _require(
        len(binding_manifest.bindings) == 1 and len(exact_bindings) == 1,
        "hybrid benchmark binding manifest is not one exact release pair",
    )
    cases: list[V0RouteBenchmarkCase] = []
    for definition_case, packet_case in zip(definition.cases, packet.cases, strict=True):
        _require_candidate_release(
            packet_case.response.query_success.structured_result.release,
            candidate=candidate,
            candidate_capability_sha256=capability_sha256,
        )
        _require(
            packet_case.case_ordinal == definition_case.case_ordinal
            and packet_case.case_key == definition_case.case_key
            and packet_case.response.original_request.question == definition_case.question
            and packet_case.response.query_success.query_plan.original_question
            == definition_case.structured_question
            and packet_case.response.original_request.corpus_release_key
            == packet.corpus_release_key,
            "review packet does not retain the preregistered hybrid case",
        )
        cases.append(
            V0RouteBenchmarkCase(
                case_ordinal=definition_case.case_ordinal,
                case_key=definition_case.case_key,
                question_sha256=_question_sha256(definition_case.question),
                response_sha256=packet_case.response_sha256,
                result="passed",
            )
        )
    payload: dict[str, object] = {
        "benchmark_report_schema_version": "v0-route-benchmark-report-v1",
        "route": "hybrid",
        "release_key": candidate.release_key,
        "release_manifest_sha256": candidate.release_manifest_sha256,
        "candidate_validation_input_sha256": candidate.input_sha256,
        "dataset_validation_request_sha256": candidate.validation_request_sha256,
        "dependency_graph_sha256": candidate.expected_dependency_graph_sha256,
        "candidate_capability_sha256": capability_sha256,
        "corpus_release_key": packet.corpus_release_key,
        "corpus_manifest_sha256": packet.corpus_manifest_sha256,
        "corpus_receipt_sha256": corpus_sha256,
        "binding_manifest_sha256": binding_manifest.manifest_sha256,
        "human_review_evaluation_sha256": evaluation.evaluation_sha256,
        "cases": tuple(cases),
        "report_sha256": "0" * 64,
    }
    payload["report_sha256"] = canonical_self_sha256(payload, "report_sha256")
    return V0RouteBenchmarkReport.model_validate(payload)


def _validate_pre_receipt_reports(
    *,
    candidate: DatasetCandidateValidationInput,
    structured_report: V0RouteBenchmarkReport,
    hybrid_report: V0RouteBenchmarkReport,
    evaluation: HumanReviewEvaluation,
) -> str:
    capability_sha256 = structured_candidate_capability_sha256(candidate)
    expected = (
        candidate.release_key,
        candidate.release_manifest_sha256,
        candidate.input_sha256,
        candidate.validation_request_sha256,
        candidate.expected_dependency_graph_sha256,
        capability_sha256,
    )
    for report, route in (
        (structured_report, "structured"),
        (hybrid_report, "hybrid"),
    ):
        observed = (
            report.release_key,
            report.release_manifest_sha256,
            report.candidate_validation_input_sha256,
            report.dataset_validation_request_sha256,
            report.dependency_graph_sha256,
            report.candidate_capability_sha256,
        )
        _require(
            report.route == route and observed == expected,
            f"{route} report targets another candidate",
        )
    _require(
        hybrid_report.human_review_evaluation_sha256 == evaluation.evaluation_sha256,
        "hybrid report does not bind the human evaluation",
    )
    _require_perfect_human_evaluation(evaluation)
    return capability_sha256


def build_clean_activation_rebuild_input(
    *,
    activation_evidence_commit: str,
    candidate_validation_input: DatasetCandidateValidationInput,
    corpus_export: CorpusValidationExport,
    structured_report: V0RouteBenchmarkReport,
    hybrid_report: V0RouteBenchmarkReport,
    evaluation: HumanReviewEvaluation,
    dependency_lock_path: Path,
) -> V0CleanActivationRebuildInput:
    """Freeze every input needed by a later empty-database replay operation."""

    candidate, _ = _candidate_identity(candidate_validation_input)
    corpus_export = _trusted(corpus_export, CorpusValidationExport)
    structured_report = _trusted(structured_report, V0RouteBenchmarkReport)
    hybrid_report = _trusted(hybrid_report, V0RouteBenchmarkReport)
    evaluation = _trusted(evaluation, HumanReviewEvaluation)
    capability_sha256 = _validate_pre_receipt_reports(
        candidate=candidate,
        structured_report=structured_report,
        hybrid_report=hybrid_report,
        evaluation=evaluation,
    )
    try:
        if dependency_lock_path.is_symlink() or not dependency_lock_path.is_file():
            raise OSError
        dependency_lock_sha256 = hashlib.sha256(dependency_lock_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ActivationRunnerError("dependency lock is unavailable") from exc
    corpus_evidence = corpus_export.receipt.validation_report
    _, corpus_sha256 = corpus_receipt_identity(corpus_evidence)
    _require(
        hybrid_report.corpus_release_key == corpus_export.corpus_release.corpus_release_key
        and hybrid_report.corpus_manifest_sha256 == corpus_export.corpus_release.manifest_sha256
        and hybrid_report.corpus_receipt_sha256 == corpus_sha256,
        "hybrid report does not bind the corpus receipt export",
    )
    payload: dict[str, object] = {
        "input_schema_version": "v0-clean-activation-rebuild-input-v1",
        "activation_evidence_commit": activation_evidence_commit,
        "release_key": candidate.release_key,
        "release_manifest_sha256": candidate.release_manifest_sha256,
        "corpus_release_key": corpus_export.corpus_release.corpus_release_key,
        "corpus_manifest_sha256": corpus_export.corpus_release.manifest_sha256,
        "candidate_validation_input_sha256": candidate.input_sha256,
        "dataset_validation_request_sha256": candidate.validation_request_sha256,
        "dependency_graph_sha256": candidate.expected_dependency_graph_sha256,
        "candidate_capability_sha256": capability_sha256,
        "corpus_receipt_sha256": corpus_sha256,
        "corpus_rebuild_report_sha256": corpus_evidence.rebuild_report.rebuild_sha256,
        "structured_benchmark_report_sha256": structured_report.report_sha256,
        "hybrid_benchmark_report_sha256": hybrid_report.report_sha256,
        "human_review_evaluation_sha256": evaluation.evaluation_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
        "dataset_validator_code_sha256": candidate.validator_code_sha256,
        "activation_policy_code_sha256": (
            candidate.candidate_activation_evidence.activation_policy_code_sha256
        ),
        "activation_state_validator_code_sha256": activation_validator_code_sha256(),
        "corpus_validator_code_sha256": corpus_evidence.validator_code_sha256,
        "corpus_importer_code_sha256": V0_CORPUS_IMPORTER_CODE_SHA256,
        "corpus_policy_code_sha256": V0_CORPUS_POLICY_CODE_SHA256,
        "input_sha256": "0" * 64,
    }
    payload["input_sha256"] = canonical_self_sha256(payload, "input_sha256")
    return V0CleanActivationRebuildInput.model_validate(payload)


def build_dataset_activation_evidence_from_reports(
    *,
    candidate_validation_input: DatasetCandidateValidationInput,
    clean_rebuild_report: V0CleanActivationRebuildReport,
    structured_report: V0RouteBenchmarkReport,
    hybrid_report: V0RouteBenchmarkReport,
    evaluation: HumanReviewEvaluation,
) -> DatasetActivationEvidence:
    """Build ACT-D04 evidence only after replaying all exact passing reports."""

    candidate, capability_sha256 = _candidate_identity(candidate_validation_input)
    clean_rebuild_report = _trusted(clean_rebuild_report, V0CleanActivationRebuildReport)
    structured_report = _trusted(structured_report, V0RouteBenchmarkReport)
    hybrid_report = _trusted(hybrid_report, V0RouteBenchmarkReport)
    evaluation = _trusted(evaluation, HumanReviewEvaluation)
    _validate_pre_receipt_reports(
        candidate=candidate,
        structured_report=structured_report,
        hybrid_report=hybrid_report,
        evaluation=evaluation,
    )
    _require(
        clean_rebuild_report.status == "passed"
        and clean_rebuild_report.database_started_empty
        and clean_rebuild_report.release_key == candidate.release_key
        and clean_rebuild_report.release_manifest_sha256 == candidate.release_manifest_sha256
        and clean_rebuild_report.candidate_validation_input_sha256 == candidate.input_sha256
        and clean_rebuild_report.dataset_validation_request_sha256
        == candidate.validation_request_sha256
        and clean_rebuild_report.dependency_graph_sha256
        == candidate.expected_dependency_graph_sha256
        and clean_rebuild_report.candidate_capability_sha256 == capability_sha256
        and clean_rebuild_report.structured_benchmark_report_sha256
        == structured_report.report_sha256
        and clean_rebuild_report.hybrid_benchmark_report_sha256 == hybrid_report.report_sha256
        and clean_rebuild_report.human_review_evaluation_sha256 == evaluation.evaluation_sha256,
        "clean rebuild report does not bind the exact passing candidate reports",
    )
    _require(
        tuple(item.evidence_sha256 for item in clean_rebuild_report.route_replays)
        == (
            structured_report.report_sha256,
            clean_rebuild_report.corpus_rebuild_report_sha256,
            hybrid_report.report_sha256,
        ),
        "clean rebuild route replays do not bind the exact reports",
    )
    return build_dataset_activation_evidence(
        candidate_validation_input_sha256=candidate.input_sha256,
        release_key=candidate.release_key,
        clean_rebuild_report_sha256=clean_rebuild_report.rebuild_sha256,
        structured_benchmark_report_sha256=structured_report.report_sha256,
        hybrid_benchmark_report_sha256=hybrid_report.report_sha256,
        human_review_report_sha256=evaluation.evaluation_sha256,
    )


def build_dataset_publication_evidence(
    report: DatasetPublicationReport,
    *,
    receipt_key: str,
) -> DatasetPublicationEvidence:
    """Seal the exact structured DB publication report for final activation."""

    report = DatasetPublicationReport.model_validate_json(report.model_dump_json(), strict=True)
    timestamp = report.model_dump(mode="json")["published_at"]
    payload: dict[str, object] = {
        "publication_evidence_schema_version": "v0-dataset-publication-evidence-v1",
        "release_key": report.release_key,
        "manifest_sha256": report.manifest_sha256,
        "receipt_key": receipt_key,
        "receipt_sha256": report.receipt_sha256,
        "status": report.status,
        "published_at": timestamp,
        "replayed": report.replayed,
        "publication_sha256": "0" * 64,
    }
    payload["publication_sha256"] = canonical_self_sha256(payload, "publication_sha256")
    return DatasetPublicationEvidence.model_validate_json(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), strict=True
    )


def build_corpus_publication_evidence(
    report: PublicationReport,
    *,
    receipt_key: str,
) -> CorpusPublicationEvidence:
    """Seal the exact corpus DB publication report for final activation."""

    report = PublicationReport.model_validate_json(report.model_dump_json(), strict=True)
    _require(report.status == "published", "corpus publication report is not published")
    timestamp = report.model_dump(mode="json")["published_at"]
    payload: dict[str, object] = {
        "publication_evidence_schema_version": "v0-corpus-publication-evidence-v1",
        "corpus_release_key": report.corpus_release_key,
        "manifest_sha256": report.manifest_sha256,
        "receipt_key": receipt_key,
        "receipt_sha256": report.receipt_sha256,
        "status": "published",
        "published_at": timestamp,
        "replayed": report.replayed,
        "publication_sha256": "0" * 64,
    }
    payload["publication_sha256"] = canonical_self_sha256(payload, "publication_sha256")
    return CorpusPublicationEvidence.model_validate_json(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), strict=True
    )


__all__ = [
    "ActivationRunnerError",
    "V0CleanActivationRebuildInput",
    "build_clean_activation_rebuild_input",
    "build_corpus_publication_evidence",
    "build_dataset_activation_evidence_from_reports",
    "build_dataset_publication_evidence",
    "build_hybrid_route_benchmark_report",
    "build_structured_route_benchmark_report",
    "run_candidate_route_answers",
]
