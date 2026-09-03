"""Strict loader and deterministic audit for the frozen route-regression questions.

This module is experiment-only.  The frozen legacy JSONL intentionally retains the
old ``candidate_metadata`` field and therefore must not be admitted through the
trusted :class:`EvaluationQuestion` contract.  Loading and auditing are pure: they
perform no database, release-gate, retrieval, provider, or model work.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import QuestionFamily
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.literature.contracts import (
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.planning.parser import ControlledEnglishPlanner, StructuredQueryRequest
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LocusResolverRecord,
)
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.results import PlanSuccess

type SystemRegressionRoute = Literal["structured", "literature", "hybrid", "unsupported"]
type SystemRegressionStructuredIntent = Literal[
    "assembly_detail",
    "locus_detail",
    "list_loci",
    "list_assemblies",
    "list_source_taxa",
    "aggregate",
]

FIXTURE_RELEASE_KEY: Final = "release:endoviho-rag:v0:20990101:001"
FIXTURE_CORPUS_KEY: Final = "corpus:endoviho-rag:v0:20990101:001"
FIXTURE_ASSEMBLY: Final = "GCA_029931535.1"
FIXTURE_LOCUS: Final = f"locus:eve:v1:sha256:{'a' * 64}"
PARSER_FIXTURE_PROFILE: Final = "rag-value-candidate-parser-fixture-v1"

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
SYSTEM_REGRESSION_QUESTIONS_PATH: Final = (
    _REPOSITORY_ROOT
    / "benchmark"
    / "system_regression"
    / "rag_value_route_questions_v1.jsonl"
)
SYSTEM_REGRESSION_SOURCE_PATH: Final = (
    "benchmark/system_regression/rag_value_route_questions_v1.jsonl"
)
SYSTEM_REGRESSION_SOURCE_SHA256: Final = (
    "9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34"
)
_FAMILY_ORDER: Final[tuple[QuestionFamily, ...]] = (
    "hybrid",
    "literature",
    "structured",
    "unsupported",
)

_ALLOWED_SEMANTIC_CODES: Final[dict[QuestionFamily, frozenset[str]]] = {
    "structured": frozenset(
        {
            "assembly_identity",
            "coordinates",
            "detection_calls",
            "locus_identity",
            "release_provenance",
            "source_taxonomy",
            "structured_count",
            "structured_record_set",
            "viral_lineage_identity",
        }
    ),
    "literature": frozenset(
        {
            "assembly_fragmentation_limitation",
            "biological_interpretation_limitation",
            "coordinate_provenance",
            "detection_method",
            "endogeneity_evidence",
            "false_positive_control",
            "high_confidence_definition",
            "lineage_assignment",
        }
    ),
    "hybrid": frozenset(
        {
            "assembly_identity",
            "biological_interpretation_limitation",
            "coordinates",
            "detection_calls",
            "detection_method",
            "lineage_assignment",
            "locus_identity",
            "release_provenance",
            "source_taxonomy",
            "structured_count",
            "structured_record_set",
            "viral_lineage_identity",
        }
    ),
    "unsupported": frozenset(
        {
            "arbitrary_sql_requested",
            "biological_absence_not_established",
            "codivergence_not_established",
            "conversation_memory_requested",
            "external_computation_requested",
            "external_knowledge_requested",
            "host_lineage_comparison_unsupported",
            "independent_event_not_established",
            "live_web_search_requested",
            "modern_infection_not_established",
            "multilingual_output_requested",
            "prevalence_not_established",
        }
    ),
}


class SystemRegressionError(ValueError):
    """Raised when the frozen system-regression artifact or its audit drifts."""


class SystemRegressionQuestionMetadata(StrictFrozenSchema):
    """Exact legacy non-Gold metadata retained for route/parser regression."""

    wording_sources: tuple[StableToken, ...] = Field(min_length=1)
    evaluation_focus: StableToken
    expected_route: SystemRegressionRoute
    expected_structured_intent: SystemRegressionStructuredIntent | None = None
    expected_refusal_code: Literal["unsupported_request"] | None = None
    semantic_boundary_codes: tuple[StableToken, ...] = Field(min_length=1)
    parser_fixture_profile: StableToken | None = None
    uses_fixture_entities: bool = False

    @field_validator("wording_sources", "semantic_boundary_codes")
    @classmethod
    def canonical_metadata_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("system-regression metadata collections must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_expected_route(self) -> Self:
        has_structured_clause = self.expected_route in {"structured", "hybrid"}
        if has_structured_clause != (self.expected_structured_intent is not None):
            raise ValueError("structured/hybrid rows require one expected QueryPlan intent")
        if has_structured_clause != (self.parser_fixture_profile is not None):
            raise ValueError("structured/hybrid rows require one parser fixture profile")
        if (self.expected_route == "unsupported") != (
            self.expected_refusal_code is not None
        ):
            raise ValueError("unsupported rows alone require a refusal code")
        if self.uses_fixture_entities and not has_structured_clause:
            raise ValueError("fixture entities belong only to structured clauses")
        return self


class SystemRegressionQuestion(StrictFrozenSchema):
    """One frozen pending regression row, deliberately separate from trusted questions."""

    question_schema_version: Literal["rag-value-question-v1"]
    question_id: StableToken
    family: QuestionFamily
    question_text: QuestionText
    question_text_sha256: Sha256
    review_status: Literal["pending"]
    approval: None
    gold: None
    candidate_metadata: SystemRegressionQuestionMetadata
    authoring_notes: str | None = Field(default=None, max_length=4000)
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity_and_status(self) -> Self:
        if self.question_text_sha256 != hashlib.sha256(
            self.question_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("question_text_sha256 does not match question_text")
        if self.family != self.candidate_metadata.expected_route:
            raise ValueError("question family does not match the expected regression route")
        payload = self.model_dump(mode="python")
        del payload["record_sha256"]
        if self.record_sha256 != canonical_json_sha256(payload):
            raise ValueError("system-regression record checksum does not match")
        return self


class SystemRegressionAudit(StrictFrozenSchema):
    """Checksum-bound route/parser audit; this is not a benchmark result or Gold."""

    audit_schema_version: Literal["rag-value-system-regression-audit-v1"] = (
        "rag-value-system-regression-audit-v1"
    )
    artifact_status: Literal["system_regression_only_not_gold"] = (
        "system_regression_only_not_gold"
    )
    source_path: Literal[
        "benchmark/system_regression/rag_value_route_questions_v1.jsonl"
    ]
    source_sha256: Sha256
    question_set_sha256: Sha256
    question_count: Literal[64]
    family_counts: dict[QuestionFamily, int]
    route_counts: dict[SystemRegressionRoute, int]
    pending_count: Literal[64]
    parser_applicable_count: Literal[32]
    parser_accepted_count: Literal[32]
    parser_rejection_count: Literal[0]
    route_mismatch_count: Literal[0]
    normalized_duplicate_count: Literal[0]
    evaluation_focus_duplicate_count: Literal[0]
    semantic_boundary_violation_count: Literal[0]
    gold_annotation_count: Literal[0]
    audit_sha256: Sha256

    @field_validator("family_counts", "route_counts")
    @classmethod
    def canonical_counts(cls, values: dict[str, int]) -> dict[str, int]:
        if tuple(values) != tuple(sorted(values)):
            raise ValueError("system-regression counts must use canonical key order")
        return values

    @model_validator(mode="after")
    def validate_audit(self) -> Self:
        expected = {"hybrid": 16, "literature": 16, "structured": 16, "unsupported": 16}
        if self.family_counts != expected or self.route_counts != expected:
            raise ValueError("system-regression families and routes must contain 16 rows each")
        if self.source_sha256 != SYSTEM_REGRESSION_SOURCE_SHA256:
            raise ValueError("system-regression source checksum drifted")
        payload = self.model_dump(mode="python")
        del payload["audit_sha256"]
        if self.audit_sha256 != canonical_json_sha256(payload):
            raise ValueError("system-regression audit checksum does not match")
        return self


def load_system_regression_questions(
    path: Path = SYSTEM_REGRESSION_QUESTIONS_PATH,
) -> tuple[SystemRegressionQuestion, ...]:
    """Strictly load and fully audit the frozen canonical JSONL artifact."""

    questions, source_sha256 = _read_canonical_questions(path)
    _audit_questions(questions, source_sha256=source_sha256)
    return questions


def audit_system_regression_questions(
    questions: Sequence[SystemRegressionQuestion] | None = None,
) -> SystemRegressionAudit:
    """Return the deterministic route/parser audit without running scientific systems."""

    if questions is None:
        selected, source_sha256 = _read_canonical_questions(
            SYSTEM_REGRESSION_QUESTIONS_PATH
        )
    else:
        selected = tuple(questions)
        source_sha256 = SYSTEM_REGRESSION_SOURCE_SHA256
    return _audit_questions(selected, source_sha256=source_sha256)


def system_regression_questions_bytes(
    questions: Sequence[SystemRegressionQuestion] | None = None,
) -> bytes:
    """Serialize the frozen rows in canonical JSONL order."""

    selected = (
        load_system_regression_questions() if questions is None else tuple(questions)
    )
    _audit_questions(selected, source_sha256=SYSTEM_REGRESSION_SOURCE_SHA256)
    return b"".join(canonical_json_bytes(question) + b"\n" for question in selected)


def system_regression_audit_bytes() -> bytes:
    """Serialize the deterministic non-result audit as canonical JSON."""

    return canonical_json_bytes(audit_system_regression_questions()) + b"\n"


def _read_canonical_questions(
    path: Path,
) -> tuple[tuple[SystemRegressionQuestion, ...], str]:
    if path.is_symlink() or not path.is_file():
        raise SystemRegressionError("system-regression source must be a regular non-symlink file")
    raw = path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if source_sha256 != SYSTEM_REGRESSION_SOURCE_SHA256:
        raise SystemRegressionError("system-regression source checksum does not match")
    lines = raw.splitlines()
    if len(lines) != 64 or any(not line for line in lines):
        raise SystemRegressionError("system-regression source must contain exactly 64 JSON rows")
    try:
        questions = tuple(SystemRegressionQuestion.model_validate_json(line) for line in lines)
    except Exception as error:
        raise SystemRegressionError("system-regression source contains an invalid row") from error
    canonical = b"".join(canonical_json_bytes(question) + b"\n" for question in questions)
    if canonical != raw:
        raise SystemRegressionError("system-regression source is not canonical JSONL")
    return questions, source_sha256


def _audit_questions(
    questions: Sequence[SystemRegressionQuestion],
    *,
    source_sha256: str,
) -> SystemRegressionAudit:
    selected = tuple(questions)
    if len(selected) != 64:
        raise SystemRegressionError("system-regression set must contain exactly 64 rows")
    canonical_source = b"".join(
        canonical_json_bytes(question) + b"\n" for question in selected
    )
    if hashlib.sha256(canonical_source).hexdigest() != SYSTEM_REGRESSION_SOURCE_SHA256:
        raise SystemRegressionError("system-regression set differs from the frozen source")
    ids = tuple(question.question_id for question in selected)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise SystemRegressionError("system-regression IDs must be sorted and unique")
    normalized = tuple(_normalize_question(question.question_text) for question in selected)
    if len(normalized) != len(set(normalized)):
        raise SystemRegressionError("system-regression questions contain a normalized duplicate")
    focuses = tuple(question.candidate_metadata.evaluation_focus for question in selected)
    if len(focuses) != len(set(focuses)):
        raise SystemRegressionError("system-regression evaluation focuses must be unique")

    family_counts: dict[QuestionFamily, int] = {
        family: sum(question.family == family for question in selected)
        for family in _FAMILY_ORDER
    }
    expected_counts = {
        "hybrid": 16,
        "literature": 16,
        "structured": 16,
        "unsupported": 16,
    }
    if family_counts != expected_counts:
        raise SystemRegressionError("system-regression families must contain 16 rows each")

    route_counts: dict[SystemRegressionRoute, int] = {
        "hybrid": 0,
        "literature": 0,
        "structured": 0,
        "unsupported": 0,
    }
    router = DeterministicRouter()
    planner = ControlledEnglishPlanner()
    resolver = _fixture_resolver()
    parser_applicable_count = 0
    parser_accepted_count = 0

    for question in selected:
        metadata = question.candidate_metadata
        if not set(metadata.semantic_boundary_codes) <= _ALLOWED_SEMANTIC_CODES[
            question.family
        ]:
            raise SystemRegressionError("a semantic code exceeds its family boundary")
        if metadata.expected_route in {"structured", "hybrid"}:
            if metadata.parser_fixture_profile != PARSER_FIXTURE_PROFILE:
                raise SystemRegressionError("the parser fixture profile drifted")
        request = _routing_request(question)
        decision = router.route(request)
        if (
            decision.original_question != question.question_text
            or decision.route != metadata.expected_route
        ):
            raise SystemRegressionError(f"route drifted for {question.question_id}")
        route_counts[metadata.expected_route] += 1
        if metadata.expected_route == "unsupported":
            if decision.refusal_code != metadata.expected_refusal_code:
                raise SystemRegressionError("unsupported refusal code drifted")
            continue
        if metadata.expected_route == "literature":
            continue

        parser_applicable_count += 1
        if decision.structured_question is None:
            raise SystemRegressionError("structured route lacks a parser question")
        planned = planner.plan(
            StructuredQueryRequest(
                release_key=FIXTURE_RELEASE_KEY,
                question=decision.structured_question,
            ),
            resolver,
        )
        if not isinstance(planned, PlanSuccess):
            raise SystemRegressionError(
                f"controlled-English parser rejected {question.question_id}"
            )
        if planned.query_plan.intent != metadata.expected_structured_intent:
            raise SystemRegressionError("QueryPlan intent drifted")
        if (
            planned.planning_audit.unresolved_condition_ids
            or planned.planning_audit.unconsumed_semantic_spans
        ):
            raise SystemRegressionError("parser audit contains an unresolved condition")
        parser_accepted_count += 1

    if route_counts != expected_counts:
        raise SystemRegressionError("system-regression routes must contain 16 rows each")
    if parser_applicable_count != 32 or parser_accepted_count != 32:
        raise SystemRegressionError("all 32 structured clauses must pass the parser")

    payload: dict[str, object] = {
        "audit_schema_version": "rag-value-system-regression-audit-v1",
        "artifact_status": "system_regression_only_not_gold",
        "source_path": SYSTEM_REGRESSION_SOURCE_PATH,
        "source_sha256": source_sha256,
        "question_set_sha256": canonical_json_sha256(selected),
        "question_count": 64,
        "family_counts": family_counts,
        "route_counts": route_counts,
        "pending_count": 64,
        "parser_applicable_count": parser_applicable_count,
        "parser_accepted_count": parser_accepted_count,
        "parser_rejection_count": 0,
        "route_mismatch_count": 0,
        "normalized_duplicate_count": 0,
        "evaluation_focus_duplicate_count": 0,
        "semantic_boundary_violation_count": 0,
        "gold_annotation_count": 0,
    }
    return SystemRegressionAudit.model_validate(
        {**payload, "audit_sha256": canonical_json_sha256(payload)}
    )


def _routing_request(question: SystemRegressionQuestion) -> RagQueryRequest:
    if question.family == "structured":
        return RagQueryRequest(
            release_key=FIXTURE_RELEASE_KEY,
            question=question.question_text,
        )
    if question.family == "literature":
        return RagQueryRequest(
            corpus_release_key=FIXTURE_CORPUS_KEY,
            question=question.question_text,
        )
    if question.family == "hybrid":
        return RagQueryRequest(
            release_key=FIXTURE_RELEASE_KEY,
            corpus_release_key=FIXTURE_CORPUS_KEY,
            question=question.question_text,
        )
    return RagQueryRequest(question=question.question_text)


def _fixture_resolver() -> CatalogReleaseResolver:
    return CatalogReleaseResolver(
        release_key=FIXTURE_RELEASE_KEY,
        assemblies=(
            AssemblyResolverRecord(
                accession_version=FIXTURE_ASSEMBLY,
                canonical_name="Margaritifera margaritifera",
            ),
        ),
        loci=(LocusResolverRecord(locus_key=FIXTURE_LOCUS),),
        lineages=(
            LineageResolverRecord(
                entity_kind="source_lineage",
                term_key="ncbi-taxonomy:taxid:6544",
                canonical_name="Bivalvia",
                snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
                authority_namespace="ncbi-taxonomy",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="assembly_source_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="ictv:orthopolintovirales",
                canonical_name="Orthopolintovirales",
                snapshot_key="lineage-snapshot:ictv:test",
                authority_namespace="ictv",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="formal_viral_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="study:orthopolintovirales",
                canonical_name="Orthopolintovirales",
                snapshot_key="lineage-snapshot:study:zhao-v4",
                authority_namespace="study-defined:zhao-v4",
                snapshot_version="v4",
                scheme_kind="study_defined",
                role="study_viral_lineage",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key="extended:asfa-like",
                canonical_name="Asfa-like",
                snapshot_key="lineage-snapshot:extended:asfa-like-v1",
                authority_namespace="curated-extended-viral-lineage",
                snapshot_version="test-v1",
                scheme_kind="study_defined",
                role="extended_viral_lineage",
            ),
        ),
    )


def _normalize_question(question: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", question.casefold()))


__all__ = [
    "SYSTEM_REGRESSION_QUESTIONS_PATH",
    "SYSTEM_REGRESSION_SOURCE_SHA256",
    "SystemRegressionAudit",
    "SystemRegressionError",
    "SystemRegressionQuestion",
    "SystemRegressionQuestionMetadata",
    "audit_system_regression_questions",
    "load_system_regression_questions",
    "system_regression_audit_bytes",
    "system_regression_questions_bytes",
]
