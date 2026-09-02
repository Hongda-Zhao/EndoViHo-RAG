"""Frozen S0-S6 definitions and fail-closed fairness/route checks."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence

from pydantic import model_validator

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationEvidencePack,
    EvaluationSystem,
    ExecutionTrace,
    GenerationIdentity,
    SystemKey,
)
from eve_relation_rag.literature.contracts import (
    QuestionText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

LLM_SYSTEM_KEYS: tuple[SystemKey, ...] = ("S0", "S1", "S2", "S3", "S5", "S6")
ALL_SYSTEM_KEYS: tuple[SystemKey, ...] = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")


class SystemPolicyError(ValueError):
    """Raised when a comparison changes more than the evidence condition."""


class ComparisonInputRecord(StrictFrozenSchema):
    """Non-model-visible identity record for one LLM system/question request."""

    system_key: SystemKey
    question_id: StableToken
    question_text: QuestionText
    question_text_sha256: Sha256
    generation_identity_sha256: Sha256
    evidence_pack_sha256: Sha256

    @model_validator(mode="after")
    def validate_question_hash(self) -> ComparisonInputRecord:
        if self.system_key == "S4":
            raise ValueError("S4 has no LLM comparison input")
        if self.question_text_sha256 != hashlib.sha256(
            self.question_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("comparison question checksum does not match")
        return self


def build_system_definitions(
    generation_identity: GenerationIdentity | None,
) -> tuple[EvaluationSystem, ...]:
    """Build canonical systems, optionally without provider binding for Phase 3."""

    generation_sha = (
        None if generation_identity is None else generation_identity.identity_sha256
    )
    return (
        _build_system(
            system_key="S0",
            display_name="Closed-book LLM",
            evidence_mode="none",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=("llm_provider",),
            required_success_stages=("generation", "mechanical_validation"),
            allowed_stages=("generation", "mechanical_validation"),
        ),
        _build_system(
            system_key="S1",
            display_name="Raw-context / long-context baseline",
            evidence_mode="raw_context",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=("raw_context_loader", "llm_provider"),
            required_success_stages=(
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
            allowed_stages=(
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
        ),
        _build_system(
            system_key="S2",
            display_name="Keyword literature RAG",
            evidence_mode="keyword_literature",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=("database", "corpus", "fts", "llm_provider"),
            required_success_stages=(
                "fts_retrieval",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
            allowed_stages=(
                "fts_retrieval",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
        ),
        _build_system(
            system_key="S3",
            display_name="Current literature hybrid retrieval",
            evidence_mode="hybrid_literature",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=(
                "database",
                "corpus",
                "fts",
                "embedding_provider",
                "dense_index",
                "summary_index",
                "rrf",
                "llm_provider",
            ),
            required_success_stages=(
                "fts_retrieval",
                "dense_retrieval",
                "summary_retrieval",
                "rrf_fusion",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
            allowed_stages=(
                "fts_retrieval",
                "dense_retrieval",
                "summary_retrieval",
                "rrf_fusion",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
        ),
        _build_system(
            system_key="S4",
            display_name="Structured retrieval",
            evidence_mode="structured",
            uses_llm=False,
            generation_identity_sha256=None,
            allowed_dependencies=("database", "structured_retrieval"),
            required_success_stages=(
                "structured_planning",
                "structured_retrieval",
                "deterministic_render",
            ),
            allowed_stages=(
                "structured_planning",
                "structured_retrieval",
                "deterministic_render",
            ),
        ),
        _build_system(
            system_key="S5",
            display_name="EndoViHo structured-first Hybrid RAG",
            evidence_mode="structured_first_hybrid",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=(
                "database",
                "structured_retrieval",
                "corpus",
                "fts",
                "embedding_provider",
                "dense_index",
                "summary_index",
                "rrf",
                "llm_provider",
            ),
            required_success_stages=(
                "structured_planning",
                "release_binding",
                "structured_retrieval",
                "anchor_resolution",
                "fts_retrieval",
                "dense_retrieval",
                "summary_retrieval",
                "rrf_fusion",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
                "deterministic_render",
            ),
            allowed_stages=(
                "structured_planning",
                "release_binding",
                "structured_retrieval",
                "anchor_resolution",
                "fts_retrieval",
                "dense_retrieval",
                "summary_retrieval",
                "rrf_fusion",
                "chunk_hydration",
                "context_construction",
                "generation",
                "mechanical_validation",
                "deterministic_render",
            ),
        ),
        _build_system(
            system_key="S6",
            display_name="Oracle evidence plus same LLM",
            evidence_mode="oracle",
            uses_llm=True,
            generation_identity_sha256=generation_sha,
            allowed_dependencies=("oracle_loader", "llm_provider"),
            required_success_stages=(
                "oracle_load",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
            allowed_stages=(
                "oracle_load",
                "context_construction",
                "generation",
                "mechanical_validation",
            ),
        ),
    )


def validate_system_definitions(
    systems: Sequence[EvaluationSystem],
    generation_identity: GenerationIdentity | None,
) -> None:
    """Require the byte-exact canonical S0-S6 policy graph."""

    if tuple(systems) != build_system_definitions(generation_identity):
        raise SystemPolicyError("system definitions differ from the canonical S0-S6 policy")


def validate_execution_trace(system: EvaluationSystem, trace: ExecutionTrace) -> None:
    """Reject forbidden dependency construction, routes, fallbacks, and post-refusal work."""

    if trace.system_key != system.system_key:
        raise SystemPolicyError("execution trace system key does not match system")
    forbidden_dependencies = set(trace.constructed_dependencies) - set(
        system.allowed_dependencies
    )
    if forbidden_dependencies:
        raise SystemPolicyError(
            f"system constructed forbidden dependencies: {sorted(forbidden_dependencies)}"
        )
    forbidden_stages = set(trace.called_stages) - set(system.allowed_stages)
    if forbidden_stages:
        raise SystemPolicyError(f"system called forbidden stages: {sorted(forbidden_stages)}")
    expected_order = tuple(
        stage for stage in system.allowed_stages if stage in set(trace.called_stages)
    )
    if trace.called_stages != expected_order:
        raise SystemPolicyError("execution stages do not follow the frozen system order")
    if trace.status == "completed" and not set(system.required_success_stages) <= set(
        trace.called_stages
    ):
        raise SystemPolicyError("completed execution is missing required stages")
    if trace.status == "completed" and trace.generation_call_count != int(system.uses_llm):
        raise SystemPolicyError("completed execution has the wrong generation call count")
    if trace.status == "retrieval_only":
        if "llm_provider" in trace.constructed_dependencies:
            raise SystemPolicyError("retrieval-only execution constructed an LLM provider")
        pre_generation_stages = tuple(
            stage
            for stage in system.required_success_stages
            if stage not in {"generation", "mechanical_validation", "deterministic_render"}
        )
        if not system.uses_llm or not pre_generation_stages:
            raise SystemPolicyError("system has no retrieval-only execution path")
        if not set(pre_generation_stages) <= set(trace.called_stages):
            raise SystemPolicyError("retrieval-only execution is missing required preparation")
        if {"generation", "mechanical_validation", "deterministic_render"} & set(
            trace.called_stages
        ):
            raise SystemPolicyError("retrieval-only execution called an answer stage")
    if not system.uses_llm and trace.generation_call_count:
        raise SystemPolicyError("non-LLM system called generation")


def validate_evidence_for_system(
    system: EvaluationSystem,
    evidence: EvaluationEvidencePack | None,
) -> None:
    """Reject hidden or cross-condition evidence before a provider can be called."""

    if system.system_key == "S4":
        if evidence is not None:
            raise SystemPolicyError("S4 must not construct an LLM evidence pack")
        return
    if evidence is None:
        raise SystemPolicyError("LLM system requires an evidence pack")
    has_structured = evidence.structured_success is not None
    has_citations = bool(evidence.citations)
    has_raw = bool(evidence.raw_context_segments)
    expected_shapes = {
        "S0": (False, False, False),
        "S1": (False, False, True),
        "S2": (False, True, False),
        "S3": (False, True, False),
        "S5": (True, True, False),
    }
    if system.system_key in expected_shapes and (
        has_structured,
        has_citations,
        has_raw,
    ) != expected_shapes[system.system_key]:
        raise SystemPolicyError("evidence shape does not match the frozen system condition")
    if system.system_key == "S6" and has_raw:
        raise SystemPolicyError("S6 cannot substitute raw context for approved oracle evidence")
    if system.system_key == "S6" and evidence.oracle_entry_sha256 is None:
        raise SystemPolicyError("S6 requires a manually approved oracle entry")
    if system.system_key != "S6" and evidence.oracle_entry_sha256 is not None:
        raise SystemPolicyError("oracle provenance is valid only for S6")
    if system.system_key not in {"S3", "S5"} and (
        evidence.production_context_pack_sha256 is not None
    ):
        raise SystemPolicyError("production ContextPack provenance is valid only for S3/S5")


def validate_llm_comparison_inputs(
    records: Sequence[ComparisonInputRecord],
    systems: Sequence[EvaluationSystem],
) -> None:
    """Prove that evidence is the only model-input difference for every question."""

    canonical_systems = tuple(system.system_key for system in systems)
    if canonical_systems != ALL_SYSTEM_KEYS:
        raise SystemPolicyError("comparison requires canonical S0-S6 definitions")
    expected_generation = {
        system.generation_identity_sha256 for system in systems if system.uses_llm
    }
    if len(expected_generation) != 1 or None in expected_generation:
        raise SystemPolicyError("LLM systems do not share one generation identity")
    by_question: defaultdict[str, list[ComparisonInputRecord]] = defaultdict(list)
    for record in records:
        by_question[record.question_id].append(record)
    if not by_question:
        raise SystemPolicyError("comparison input is empty")
    for question_id, question_records in by_question.items():
        observed_keys = tuple(record.system_key for record in question_records)
        if observed_keys != LLM_SYSTEM_KEYS:
            raise SystemPolicyError(
                f"question {question_id} does not cover LLM systems in canonical order"
            )
        if len({record.question_text for record in question_records}) != 1:
            raise SystemPolicyError("question wording differs between systems")
        if len({record.question_text_sha256 for record in question_records}) != 1:
            raise SystemPolicyError("question checksum differs between systems")
        if {
            record.generation_identity_sha256 for record in question_records
        } != expected_generation:
            raise SystemPolicyError("generation identity differs between systems")


def _build_system(
    *,
    system_key: SystemKey,
    display_name: str,
    evidence_mode: str,
    uses_llm: bool,
    generation_identity_sha256: str | None,
    allowed_dependencies: tuple[str, ...],
    required_success_stages: tuple[str, ...],
    allowed_stages: tuple[str, ...],
) -> EvaluationSystem:
    payload: dict[str, object] = {
        "system_schema_version": "rag-value-system-v1",
        "system_key": system_key,
        "display_name": display_name,
        "evidence_mode": evidence_mode,
        "uses_llm": uses_llm,
        "generation_identity_sha256": generation_identity_sha256,
        "allowed_dependencies": allowed_dependencies,
        "required_success_stages": required_success_stages,
        "allowed_stages": allowed_stages,
    }
    return EvaluationSystem.model_validate(
        {**payload, "system_sha256": canonical_json_sha256(payload)}
    )
