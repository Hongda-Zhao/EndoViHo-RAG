"""Isolated deterministic Phase 2 runner for the RAG-value ablation.

This module exercises S0--S6 with in-memory synthetic fixtures only.  It never
reads production configuration, constructs a real provider, opens a database,
or writes anywhere unless the caller supplies a new output directory.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    AnswerStructuredFacts,
    DependencyKind,
    EvaluationAnswer,
    EvaluationEvidencePack,
    EvaluationSystem,
    EvidenceCitation,
    ExecutionStage,
    ExecutionTrace,
    GenerationIdentity,
    MechanicalValidation,
    RawContextPolicy,
    RawContextSegment,
    RuntimeIdentity,
    StructuredPreservationProof,
    SystemKey,
    build_evidence_pack,
    build_experiment_manifest,
    build_raw_context_policy,
    build_retrieval_policy_identity,
    mechanically_validate_answer,
    model_visible_evidence,
    prove_structured_result_preserved,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    EfficiencyObservation,
    RefusalObservation,
    RefusalOrigin,
    RetrievalMetrics,
    StructuredMetrics,
    StructuredPrediction,
    score_retrieval,
    score_structured,
    structured_prediction_from_answer,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import (
    SYSTEM_INSTRUCTION,
    PromptPolicy,
    build_prompt_policy,
    render_user_payload,
    validate_generation_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.reporting import (
    BenchmarkRun,
    PerQuestionEvaluation,
    build_benchmark_run,
    build_per_question_evaluation,
    write_benchmark_outputs,
)
from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
    CHUNK_A,
    CHUNK_B,
    SYNTHETIC_CORPUS_KEY,
    SYNTHETIC_FIXTURE_STATUS,
    SYNTHETIC_RELEASE_KEY,
    DeterministicFakeGenerationProvider,
    SyntheticCase,
    SyntheticDeterministicOutput,
    SyntheticFactRepository,
    SyntheticFixtureManifest,
    SyntheticGenerationRequest,
    SyntheticOracleEvidence,
    SyntheticOracleLoader,
    SyntheticRankProvider,
    SyntheticStructuredStack,
    authorize_synthetic_hybrid_binding,
    build_synthetic_deterministic_output,
    build_synthetic_fixture_manifest,
    build_synthetic_generation_identity,
    build_synthetic_generation_request,
    build_synthetic_oracle_entry,
    build_synthetic_structured_stack,
    synthetic_citations,
    synthetic_raw_segments,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    LLM_SYSTEM_KEYS,
    ComparisonInputRecord,
    build_system_definitions,
    validate_evidence_for_system,
    validate_execution_trace,
    validate_llm_comparison_inputs,
    validate_system_definitions,
)
from eve_relation_rag.experiments.rag_value_ablation.trust import (
    PHASE2_SYNTHETIC_TRUST_REASONS,
    RunTrustDecision,
    issue_phase2_synthetic_trust,
)
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic
from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorTarget,
    extract_structured_anchor_targets,
)
from eve_relation_rag.retrieval.literature.fusion import fuse_ranked_candidates
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    PlanSuccess,
    QuerySuccess,
)

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class SyntheticExecutionEvent:
    """One ordered event emitted only after an operation actually succeeds."""

    sequence: int
    event_kind: Literal["dependency_constructed", "stage_completed"]
    name: DependencyKind | ExecutionStage


@dataclass(frozen=True, slots=True)
class _SyntheticDependencyMarker:
    """Explicit in-memory stand-in for a dependency that performs no I/O in Phase 2."""

    kind: DependencyKind


@dataclass(frozen=True, slots=True)
class _AdmissionDecision:
    """Policy result derived from question text, never from expected-refusal Gold."""

    admitted: bool
    refusal_origin: RefusalOrigin


@dataclass(slots=True)
class _ExecutionRecorder:
    dependencies: list[DependencyKind]
    stages: list[ExecutionStage]
    events: list[SyntheticExecutionEvent]
    generation_call_count: int = 0

    @classmethod
    def create(cls) -> _ExecutionRecorder:
        return cls(dependencies=[], stages=[], events=[])

    def construct(self, kind: DependencyKind, factory: Callable[[], _T]) -> _T:
        if kind in self.dependencies:
            raise ValueError(f"synthetic dependency was constructed twice: {kind}")
        value = factory()
        self.dependencies.append(kind)
        self._event("dependency_constructed", kind)
        return value

    def call(self, stage: ExecutionStage, operation: Callable[[], _T]) -> _T:
        if stage in self.stages:
            raise ValueError(f"synthetic stage was called twice: {stage}")
        value = operation()
        self.stages.append(stage)
        self._event("stage_completed", stage)
        if stage == "generation":
            self.generation_call_count += 1
        return value

    def complete(self, stage: ExecutionStage) -> None:
        """Record completion reported by an existing lifecycle hook."""

        if stage in self.stages:
            raise ValueError(f"synthetic stage was completed twice: {stage}")
        self.stages.append(stage)
        self._event("stage_completed", stage)

    def trace(
        self,
        *,
        system_key: SystemKey,
        question_id: str,
        status: Literal["completed", "refused", "retrieval_only", "not_applicable", "failed"],
        refusal_stage: ExecutionStage | None = None,
    ) -> ExecutionTrace:
        return ExecutionTrace(
            system_key=system_key,
            question_id=question_id,
            status=status,
            constructed_dependencies=tuple(self.dependencies),
            called_stages=tuple(self.stages),
            refusal_stage=refusal_stage,
            generation_call_count=self.generation_call_count,
        )

    def _event(
        self,
        event_kind: Literal["dependency_constructed", "stage_completed"],
        name: DependencyKind | ExecutionStage,
    ) -> None:
        self.events.append(
            SyntheticExecutionEvent(
                sequence=len(self.events) + 1,
                event_kind=event_kind,
                name=name,
            )
        )


@dataclass(frozen=True, slots=True)
class SyntheticExecutionArtifact:
    """Non-reporting audit material retained for route-isolation assertions."""

    system_key: SystemKey
    question_id: str
    evidence: EvaluationEvidencePack | None
    answer: EvaluationAnswer | None
    structured_success: QuerySuccess | None
    returned_chunk_keys: tuple[str, ...]
    trace: ExecutionTrace
    events: tuple[SyntheticExecutionEvent, ...]
    deterministic_output: SyntheticDeterministicOutput | None
    binding_manifest_sha256: str | None
    structured_anchor_targets: tuple[StructuredAnchorTarget, ...]
    anchor_resolution_adapter: str | None


@dataclass(frozen=True, slots=True)
class SyntheticHarnessExecution:
    """Complete in-memory result plus its issuer-bound publication authority."""

    fixture_manifest: SyntheticFixtureManifest
    run: BenchmarkRun
    trust_decision: RunTrustDecision
    artifacts: tuple[SyntheticExecutionArtifact, ...]
    comparison_inputs: tuple[ComparisonInputRecord, ...]
    comparison_eligible_question_ids: tuple[str, ...]
    provider_requests: tuple[SyntheticGenerationRequest, ...]
    execution_order: tuple[tuple[SystemKey, str], ...]


def run_synthetic_benchmark(output_directory: Path) -> SyntheticHarnessExecution:
    """Create one explicit, create-once ``tests_only`` benchmark directory."""

    execution = execute_synthetic_harness()
    write_benchmark_outputs(
        output_directory,
        execution.run,
        execution.trust_decision,
        allow_test_output=True,
    )
    return execution


def execute_synthetic_harness() -> SyntheticHarnessExecution:
    """Run five synthetic cases through the isolated S0--S6 execution matrix."""

    fixture = build_synthetic_fixture_manifest()
    policy = build_prompt_policy()
    identity = build_synthetic_generation_identity(policy)
    validate_generation_identity(identity, policy)
    systems = build_system_definitions(identity)
    validate_system_definitions(systems, identity)

    raw_policy = _raw_context_policy(identity.context_limit_tokens, identity.max_output_tokens)
    retrieval_policy = build_retrieval_policy_identity(embedding_artifact_manifest_sha256=None)
    provider_requests: list[SyntheticGenerationRequest] = []
    outcomes: dict[
        tuple[SystemKey, str],
        tuple[
            PerQuestionEvaluation,
            SyntheticExecutionArtifact,
            ComparisonInputRecord | None,
        ],
    ] = {}
    execution_order: list[tuple[SystemKey, str]] = []
    comparisons: list[ComparisonInputRecord] = []
    eligible_questions: list[str] = []

    for system in systems:
        for case_index, case in enumerate(fixture.cases, start=1):
            outcomes[(system.system_key, case.question_id)] = _execute_case(
                system_key=system.system_key,
                system=system,
                case=case,
                case_index=case_index,
                policy=policy,
                identity=identity,
                provider_requests=provider_requests,
                raw_policy=raw_policy,
            )
            execution_order.append((system.system_key, case.question_id))

    canonical_pairs = tuple(
        (system.system_key, case.question_id) for system in systems for case in fixture.cases
    )
    results = [outcomes[pair][0] for pair in canonical_pairs]
    artifacts = [outcomes[pair][1] for pair in canonical_pairs]
    comparisons.extend(
        comparison for pair in canonical_pairs if (comparison := outcomes[pair][2]) is not None
    )

    oracle_records = tuple(
        {
            "fixture_status": SYNTHETIC_FIXTURE_STATUS,
            "question_id": artifact.question_id,
            "entry_sha256": artifact.evidence.oracle_entry_sha256,
        }
        for artifact in artifacts
        if artifact.system_key == "S6"
        and artifact.evidence is not None
        and artifact.evidence.oracle_entry_sha256 is not None
    )
    if len(oracle_records) != 4:
        raise ValueError("synthetic S6 did not lazily load exactly four Oracle entries")
    oracle_manifest_sha256 = canonical_json_sha256(oracle_records)
    binding_hashes = {
        artifact.binding_manifest_sha256
        for artifact in artifacts
        if artifact.binding_manifest_sha256 is not None
    }
    if len(binding_hashes) != 1:
        raise ValueError("synthetic S5 did not use one exact hybrid binding manifest")
    binding_manifest_sha256 = binding_hashes.pop()

    manifest = build_experiment_manifest(
        experiment_key="experiment:rag-value:phase2-synthetic-v1",
        phase="phase2_synthetic",
        trust_status="test_only",
        trust_reasons=PHASE2_SYNTHETIC_TRUST_REASONS,
        source_commit="f" * 40,
        source_tree_clean=False,
        production_source_fingerprint_sha256=canonical_json_sha256(
            {"production_inputs_accessed": False, "fixture": SYNTHETIC_FIXTURE_STATUS}
        ),
        question_manifest_sha256=fixture.fixture_sha256,
        synthetic_fixture_manifest_sha256=fixture.fixture_sha256,
        oracle_manifest_sha256=oracle_manifest_sha256,
        dataset_release_key=None,
        dataset_manifest_sha256=None,
        corpus_release_key=None,
        corpus_manifest_sha256=None,
        binding_manifest_sha256=binding_manifest_sha256,
        generation_identity=identity,
        retrieval_policy=retrieval_policy,
        raw_context_policy=raw_policy,
        pricing_manifest_sha256=None,
        runtime_identity=_runtime_identity(),
        systems=systems,
    )

    comparable_ids = tuple(
        case.question_id for case in fixture.cases if case.family in {"structured", "hybrid"}
    )
    comparison_records = tuple(
        record for record in comparisons if record.question_id in comparable_ids
    )
    comparison_records = tuple(
        record
        for question_id in comparable_ids
        for system_key in LLM_SYSTEM_KEYS
        for record in comparison_records
        if record.question_id == question_id and record.system_key == system_key
    )
    validate_llm_comparison_inputs(comparison_records, systems)
    eligible_questions.extend(comparable_ids)

    run = build_benchmark_run(
        manifest=manifest,
        human_review_status="not_required",
        results=tuple(results),
        comparison_eligible_question_ids=tuple(sorted(eligible_questions)),
        comparison_inputs=comparison_records,
        failures=(),
    )
    trust_decision = issue_phase2_synthetic_trust(
        run=run,
        fixture_manifest=fixture,
    )
    return SyntheticHarnessExecution(
        fixture_manifest=fixture,
        run=run,
        trust_decision=trust_decision,
        artifacts=tuple(artifacts),
        comparison_inputs=comparison_records,
        comparison_eligible_question_ids=tuple(eligible_questions),
        provider_requests=tuple(provider_requests),
        execution_order=tuple(execution_order),
    )


def _execute_case(
    *,
    system_key: SystemKey,
    system: EvaluationSystem,
    case: SyntheticCase,
    case_index: int,
    policy: PromptPolicy,
    identity: GenerationIdentity,
    provider_requests: list[SyntheticGenerationRequest],
    raw_policy: RawContextPolicy,
) -> tuple[
    PerQuestionEvaluation,
    SyntheticExecutionArtifact,
    ComparisonInputRecord | None,
]:
    recorder = _ExecutionRecorder.create()

    if case.family == "literature" and system_key in {"S4", "S5"}:
        trace = ExecutionTrace(
            system_key=system_key,
            question_id=case.question_id,
            status="not_applicable",
            generation_call_count=0,
        )
        validate_execution_trace(system, trace)
        result = build_per_question_evaluation(
            system_key=system_key,
            question_id=case.question_id,
            family=case.family,
            trust_status="test_only",
            status="not_applicable",
            question_text_sha256=case.question_text_sha256,
            execution_trace=trace,
        )
        return result, _artifact(system_key, case, trace, recorder=recorder), None

    admission = recorder.call(
        "request_validation",
        lambda: _admission_decision(system_key, case.question_text),
    )
    if not admission.admitted:
        trace = recorder.trace(
            system_key=system_key,
            question_id=case.question_id,
            status="refused",
            refusal_stage="request_validation",
        )
        validate_execution_trace(system, trace)
        result = build_per_question_evaluation(
            system_key=system_key,
            question_id=case.question_id,
            family=case.family,
            trust_status="test_only",
            status="refused",
            question_text_sha256=case.question_text_sha256,
            execution_trace=trace,
            refusal_observation=RefusalObservation(
                question_id=case.question_id,
                expected_refusal=case.expected_refusal,
                abstained=True,
                refusal_origin=admission.refusal_origin,
                refusal_appropriate=case.expected_refusal,
                unsafe_acceptance=False,
                downstream_call_count_after_refusal=0,
            ),
            efficiency=_efficiency(system_key, case, case_index, None, None),
        )
        return result, _artifact(system_key, case, trace, recorder=recorder), None

    if system_key == "S4":
        stack = _construct_structured_stack(recorder)
        s4_structured, binding_sha256 = _execute_structured_query(
            stack=stack,
            case=case,
            recorder=recorder,
            bind_hybrid=False,
        )
        if binding_sha256 is not None:  # pragma: no cover - defensive invariant
            raise ValueError("S4 unexpectedly authorized a hybrid binding")
        s4_output = recorder.call(
            "deterministic_render",
            lambda: build_synthetic_deterministic_output(
                mode="structured",
                structured_success=s4_structured,
            ),
        )
        trace = recorder.trace(
            system_key=system_key,
            question_id=case.question_id,
            status="completed",
        )
        validate_execution_trace(system, trace)
        metrics = _structured_metrics(case, s4_structured)
        result = build_per_question_evaluation(
            system_key=system_key,
            question_id=case.question_id,
            family=case.family,
            trust_status="test_only",
            status="completed",
            question_text_sha256=case.question_text_sha256,
            deterministic_output_text=s4_output.output_text,
            deterministic_output_sha256=s4_output.output_sha256,
            execution_trace=trace,
            structured_metrics=metrics,
            refusal_observation=_refusal(case, abstained=False),
            efficiency=_efficiency(system_key, case, case_index, None, None),
        )
        return (
            result,
            _artifact(
                system_key,
                case,
                trace,
                recorder=recorder,
                structured_success=s4_structured,
                deterministic_output=s4_output,
            ),
            None,
        )

    (
        evidence,
        structured,
        returned,
        binding_manifest_sha256,
        anchor_targets,
        anchor_adapter,
    ) = _construct_evidence(
        system_key=system_key,
        case=case,
        policy=policy,
        raw_policy=raw_policy,
        recorder=recorder,
    )
    validate_evidence_for_system(system, evidence)
    provider = recorder.construct(
        "llm_provider",
        lambda: DeterministicFakeGenerationProvider(
            identity,
            call_sink=provider_requests,
        ),
    )
    answer, mechanical = _generate(
        provider,
        policy,
        evidence,
        recorder,
        expected_structured=(structured if system_key == "S5" else None),
    )
    status: Literal["completed", "refused"] = "refused" if answer.abstained else "completed"
    deterministic_output: SyntheticDeterministicOutput | None = None
    if system_key == "S5":
        if structured is None:
            raise ValueError("S5 synthetic execution lacks its structured result")
        deterministic_output = recorder.call(
            "deterministic_render",
            lambda: build_synthetic_deterministic_output(
                mode="structured_first_hybrid",
                structured_success=structured,
                generated_answer=answer,
            ),
        )
    proof = _preservation_proof(system_key, structured, deterministic_output)
    trace = recorder.trace(
        system_key=system_key,
        question_id=case.question_id,
        status=status,
        refusal_stage=("mechanical_validation" if status == "refused" else None),
    )
    validate_execution_trace(system, trace)
    retrieval_metrics = _retrieval_metrics(system_key, case, returned)
    if case.structured_gold is None:
        structured_metrics = None
    elif system_key == "S5":
        if deterministic_output is None:
            raise ValueError("S5 structured metrics require deterministic output")
        structured_metrics = _structured_metrics(
            case,
            deterministic_output.structured_success,
        )
    else:
        structured_metrics = score_structured(
            case.structured_gold,
            structured_prediction_from_answer(answer),
        )
    result = build_per_question_evaluation(
        system_key=system_key,
        question_id=case.question_id,
        family=case.family,
        trust_status="test_only",
        status=status,
        question_text_sha256=case.question_text_sha256,
        evidence_pack_sha256=evidence.pack_sha256,
        answer=answer,
        answer_sha256=canonical_json_sha256(answer),
        deterministic_output_text=(
            None if deterministic_output is None else deterministic_output.output_text
        ),
        deterministic_output_sha256=(
            None if deterministic_output is None else deterministic_output.output_sha256
        ),
        execution_trace=trace,
        mechanical_validation=mechanical,
        structured_preservation=proof,
        structured_metrics=structured_metrics,
        retrieval_metrics=retrieval_metrics,
        refusal_observation=_refusal(case, abstained=answer.abstained),
        efficiency=_efficiency(system_key, case, case_index, evidence, answer),
    )
    comparison = ComparisonInputRecord(
        system_key=system_key,
        question_id=case.question_id,
        question_text=case.question_text,
        question_text_sha256=case.question_text_sha256,
        generation_identity_sha256=provider.identity.identity_sha256,
        evidence_pack_sha256=evidence.pack_sha256,
    )
    return (
        result,
        _artifact(
            system_key,
            case,
            trace,
            recorder=recorder,
            evidence=evidence,
            answer=answer,
            structured_success=structured,
            returned_chunk_keys=returned,
            deterministic_output=deterministic_output,
            binding_manifest_sha256=binding_manifest_sha256,
            structured_anchor_targets=anchor_targets,
            anchor_resolution_adapter=anchor_adapter,
        ),
        comparison,
    )


def _admission_decision(system_key: SystemKey, question_text: str) -> _AdmissionDecision:
    """Apply frozen production scope/route policy without consulting benchmark Gold."""

    if contains_forbidden_topic(question_text):
        return _AdmissionDecision(
            admitted=False,
            refusal_origin="shared_scope_policy",
        )
    if system_key in {"S4", "S5"}:
        route = DeterministicRouter().route(
            RagQueryRequest(
                release_key=SYNTHETIC_RELEASE_KEY,
                question=question_text,
            )
        )
        if route.route != "structured":
            return _AdmissionDecision(
                admitted=False,
                refusal_origin="system_route_policy",
            )
    return _AdmissionDecision(admitted=True, refusal_origin="none")


def _construct_evidence(
    *,
    system_key: SystemKey,
    case: SyntheticCase,
    policy: PromptPolicy,
    raw_policy: RawContextPolicy,
    recorder: _ExecutionRecorder,
) -> tuple[
    EvaluationEvidencePack,
    QuerySuccess | None,
    tuple[str, ...],
    str | None,
    tuple[StructuredAnchorTarget, ...],
    str | None,
]:
    structured: QuerySuccess | None = None
    citations: tuple[EvidenceCitation, ...] = ()
    raw_segments: tuple[RawContextSegment, ...] = ()
    oracle_entry_sha256 = None
    binding_manifest_sha256 = None
    anchor_targets: tuple[StructuredAnchorTarget, ...] = ()
    anchor_adapter = None

    if system_key == "S1":
        recorder.construct(
            "raw_context_loader",
            lambda: _SyntheticDependencyMarker("raw_context_loader"),
        )
        raw_segments = synthetic_raw_segments()
    elif system_key == "S2":
        recorder.construct("database", lambda: _SyntheticDependencyMarker("database"))
        recorder.construct("corpus", lambda: _SyntheticDependencyMarker("corpus"))
        fts = recorder.construct("fts", lambda: SyntheticRankProvider("fts", (CHUNK_A,)))
        returned = recorder.call("fts_retrieval", lambda: fts.rank(case.question_text))
        citations = recorder.call("chunk_hydration", lambda: synthetic_citations(returned))
    elif system_key in {"S3", "S5"}:
        if system_key == "S5":
            stack = _construct_structured_stack(recorder)
            structured, binding_manifest_sha256 = _execute_structured_query(
                stack=stack,
                case=case,
                recorder=recorder,
                bind_hybrid=True,
            )
            if structured is None:  # pragma: no cover - helper is strictly typed
                raise ValueError("S5 structured retrieval did not return a result")
            structured_for_anchor = structured
            anchor_targets = recorder.call(
                "anchor_resolution",
                lambda: extract_structured_anchor_targets(structured_for_anchor),
            )
            # Aggregate fixtures have no target-bearing records.  This explicit
            # adapter calls the production extraction contract but performs no
            # persisted-anchor lookup or database I/O.
            anchor_adapter = "production_target_extraction_without_anchor_store"
        else:
            recorder.construct("database", lambda: _SyntheticDependencyMarker("database"))
        recorder.construct("corpus", lambda: _SyntheticDependencyMarker("corpus"))
        fts = recorder.construct("fts", lambda: SyntheticRankProvider("fts", (CHUNK_A,)))
        recorder.construct(
            "embedding_provider",
            lambda: _SyntheticDependencyMarker("embedding_provider"),
        )
        dense = recorder.construct(
            "dense_index",
            lambda: SyntheticRankProvider("dense", (CHUNK_B, CHUNK_A)),
        )
        summary = recorder.construct(
            "summary_index",
            lambda: SyntheticRankProvider("summary", (CHUNK_B,)),
        )
        recorder.construct("rrf", lambda: _SyntheticDependencyMarker("rrf"))
        fts_keys = recorder.call("fts_retrieval", lambda: fts.rank(case.question_text))
        dense_keys = recorder.call("dense_retrieval", lambda: dense.rank(case.question_text))
        summary_keys = recorder.call("summary_retrieval", lambda: summary.rank(case.question_text))
        fused = recorder.call(
            "rrf_fusion",
            lambda: fuse_ranked_candidates(
                fts_chunk_keys=fts_keys,
                vector_chunk_keys=dense_keys,
                summary_vector_chunk_keys=summary_keys,
            ),
        )
        returned = tuple(candidate.chunk_key for candidate in fused)
        citations = recorder.call("chunk_hydration", lambda: synthetic_citations(returned))
    elif system_key == "S6":
        oracle_loader = recorder.construct(
            "oracle_loader",
            lambda: SyntheticOracleLoader(
                entry_factory=lambda question_id: _oracle_entry_for_case(
                    case,
                    question_id,
                )
            ),
        )
        oracle = recorder.call("oracle_load", lambda: oracle_loader.load(case.question_id))
        structured = oracle.structured_success
        citations = oracle.citations
        oracle_entry_sha256 = oracle.entry_sha256

    returned_keys = tuple(citation.chunk_key for citation in citations)

    def evidence_factory() -> EvaluationEvidencePack:
        return _evidence_pack(
            case=case,
            policy=policy,
            raw_policy=raw_policy,
            structured=structured,
            citations=citations,
            raw_segments=raw_segments,
            oracle_entry_sha256=oracle_entry_sha256,
        )

    evidence = (
        evidence_factory()
        if system_key == "S0"
        else recorder.call("context_construction", evidence_factory)
    )
    return (
        evidence,
        structured,
        returned_keys,
        binding_manifest_sha256,
        anchor_targets,
        anchor_adapter,
    )


def _construct_structured_stack(
    recorder: _ExecutionRecorder,
) -> SyntheticStructuredStack:
    repository = recorder.construct("database", SyntheticFactRepository)
    return recorder.construct(
        "structured_retrieval",
        lambda: build_synthetic_structured_stack(repository=repository),
    )


def _execute_structured_query(
    *,
    stack: SyntheticStructuredStack,
    case: SyntheticCase,
    recorder: _ExecutionRecorder,
    bind_hybrid: bool,
) -> tuple[QuerySuccess, str | None]:
    request = StructuredQueryRequest(
        release_key=stack.gate.release.release_key,
        question=case.question_text,
    )
    planned_responses: list[PlanSuccess] = []
    binding_manifest_sha256 = None

    def after_planning(release: ReleaseCapability, planned: PlanSuccess) -> None:
        nonlocal binding_manifest_sha256
        planned_responses.append(planned)
        recorder.complete("structured_planning")
        if not bind_hybrid:
            return
        binding, binding_manifest_sha256 = recorder.call(
            "release_binding",
            authorize_synthetic_hybrid_binding,
        )
        if (
            binding.release_key != release.release_key
            or binding.release_manifest_sha256 != release.manifest_sha256
            or binding.corpus_release_key != SYNTHETIC_CORPUS_KEY
        ):
            raise ValueError("synthetic binding differs from the structured release")

    response = recorder.call(
        "structured_retrieval",
        lambda: stack.application.query_with_pre_fact_hook(request, after_planning),
    )
    if not isinstance(response, QuerySuccess):
        raise ValueError("synthetic structured retrieval did not produce QuerySuccess")
    if len(planned_responses) != 1 or response.query_plan != planned_responses[0].query_plan:
        raise ValueError("synthetic structured lifecycle did not expose one exact plan")
    if len(stack.repository.calls) != 1:
        raise ValueError("synthetic structured repository did not execute exactly once")
    return response, binding_manifest_sha256


def _oracle_entry_for_case(
    case: SyntheticCase,
    question_id: str,
) -> SyntheticOracleEvidence:
    if question_id != case.question_id:
        raise ValueError("synthetic Oracle loader received a different question")
    return build_synthetic_oracle_entry(case)


def _evidence_pack(
    *,
    case: SyntheticCase,
    policy: PromptPolicy,
    raw_policy: RawContextPolicy,
    structured: QuerySuccess | None,
    citations: tuple[EvidenceCitation, ...],
    raw_segments: tuple[RawContextSegment, ...],
    oracle_entry_sha256: str | None,
) -> EvaluationEvidencePack:
    provisional = build_evidence_pack(
        question_id=case.question_id,
        question_text=case.question_text,
        structured_success=structured,
        citations=citations,
        raw_context_segments=raw_segments,
        policy_sha256=policy.policy_sha256,
        tokenizer_key=raw_policy.tokenizer_key,
        model_context_limit_tokens=raw_policy.model_context_limit_tokens,
        reserved_output_tokens=raw_policy.reserved_output_tokens,
        input_token_count=0,
        context_token_count=0,
        oracle_entry_sha256=oracle_entry_sha256,
    )
    context_tokens = len(canonical_json_bytes(model_visible_evidence(provisional)))
    input_tokens = len(SYSTEM_INSTRUCTION.encode("utf-8")) + len(render_user_payload(provisional))
    return build_evidence_pack(
        question_id=case.question_id,
        question_text=case.question_text,
        structured_success=structured,
        citations=citations,
        raw_context_segments=raw_segments,
        policy_sha256=policy.policy_sha256,
        tokenizer_key=raw_policy.tokenizer_key,
        model_context_limit_tokens=raw_policy.model_context_limit_tokens,
        reserved_output_tokens=raw_policy.reserved_output_tokens,
        input_token_count=input_tokens,
        context_token_count=context_tokens,
        oracle_entry_sha256=oracle_entry_sha256,
    )


def _generate(
    provider: DeterministicFakeGenerationProvider,
    policy: PromptPolicy,
    evidence: EvaluationEvidencePack,
    recorder: _ExecutionRecorder,
    *,
    expected_structured: QuerySuccess | None,
) -> tuple[EvaluationAnswer, MechanicalValidation]:
    validate_generation_identity(provider.identity, policy)
    request = build_synthetic_generation_request(
        system_instruction=policy.system_instruction,
        user_payload_json=render_user_payload(evidence).decode("utf-8"),
        generation_identity=provider.identity,
    )
    raw_answer = recorder.call("generation", lambda: provider.generate(request))
    output_bytes = raw_answer.encode("utf-8")
    if len(output_bytes) > provider.identity.max_output_bytes:
        raise ValueError("synthetic provider exceeded the frozen byte limit")
    if len(output_bytes) > provider.identity.max_output_tokens:
        raise ValueError("synthetic provider exceeded the frozen byte-token limit")
    answer = EvaluationAnswer.model_validate_json(raw_answer)

    def validate() -> MechanicalValidation:
        validation = mechanically_validate_answer(answer, evidence)
        if expected_structured is not None:
            expected_facts = _answer_structured_facts(expected_structured)
            if answer.structured_facts != expected_facts:
                raise ValueError("S5 generated structured projection modified the supplied result")
        return validation

    validation = recorder.call("mechanical_validation", validate)
    if not validation.passed:
        raise ValueError("synthetic answer failed mechanical validation")
    return answer, validation


def _answer_structured_facts(structured: QuerySuccess) -> AnswerStructuredFacts:
    data = structured.structured_result.data
    if not isinstance(data, AggregateData):
        raise ValueError("synthetic answer projection requires an aggregate")
    return AnswerStructuredFacts(
        exact_count=data.value,
        metric_key=data.metric_key,
        release_key=structured.structured_result.release.release_key,
        release_manifest_sha256=structured.structured_result.release.manifest_sha256,
        limitation_codes=tuple(
            sorted(item.code for item in structured.structured_result.limitations)
        ),
    )


def _preservation_proof(
    system_key: SystemKey,
    structured: QuerySuccess | None,
    deterministic_output: SyntheticDeterministicOutput | None,
) -> StructuredPreservationProof | None:
    if system_key != "S5":
        if deterministic_output is not None and system_key != "S4":
            raise ValueError("non-structured system produced a deterministic output")
        return None
    if structured is None or deterministic_output is None:
        raise ValueError("S5 synthetic execution lacks its rendered structured result")
    if deterministic_output.mode != "structured_first_hybrid":
        raise ValueError("S5 synthetic execution used the wrong deterministic adapter")
    return prove_structured_result_preserved(
        structured.structured_result,
        deterministic_output.structured_success.structured_result,
    )


def _structured_metrics(
    case: SyntheticCase,
    structured: QuerySuccess,
) -> StructuredMetrics | None:
    if case.structured_gold is None:
        return None
    data = structured.structured_result.data
    if not isinstance(data, AggregateData):
        raise ValueError("synthetic structured fixture is not an aggregate")
    prediction = StructuredPrediction(
        exact_count=data.value,
        metric_key=data.metric_key,
        release_key=structured.structured_result.release.release_key,
        release_manifest_sha256=structured.structured_result.release.manifest_sha256,
        limitation_codes=tuple(
            sorted(item.code for item in structured.structured_result.limitations)
        ),
    )
    return score_structured(case.structured_gold, prediction)


def _retrieval_metrics(
    system_key: SystemKey,
    case: SyntheticCase,
    returned: tuple[str, ...],
) -> RetrievalMetrics | None:
    if system_key not in {"S2", "S3", "S5"} or case.literature_gold is None:
        return None
    return score_retrieval(case.literature_gold, returned)


def _refusal(case: SyntheticCase, *, abstained: bool) -> RefusalObservation:
    return RefusalObservation(
        question_id=case.question_id,
        expected_refusal=case.expected_refusal,
        abstained=abstained,
        refusal_origin="model_abstention" if abstained else "none",
        refusal_appropriate=case.expected_refusal and abstained,
        unsafe_acceptance=case.expected_refusal and not abstained,
        downstream_call_count_after_refusal=0,
    )


def _efficiency(
    system_key: SystemKey,
    case: SyntheticCase,
    case_index: int,
    evidence: EvaluationEvidencePack | None,
    answer: EvaluationAnswer | None,
) -> EfficiencyObservation:
    system_index = int(system_key[1:])
    construction = None if evidence is None else evidence.construction
    return EfficiencyObservation(
        system_key=system_key,
        question_id=case.question_id,
        latency_ns=(system_index + 1) * 1_000 + case_index,
        input_tokens=0 if construction is None else construction.input_token_count,
        output_tokens=(0 if answer is None else len(canonical_json_bytes(answer))),
        context_tokens=0 if construction is None else construction.context_token_count,
        cost=None,
        peak_process_rss_bytes=1_000_000 + system_index,
        peak_accelerator_memory_bytes=None,
    )


def _artifact(
    system_key: SystemKey,
    case: SyntheticCase,
    trace: ExecutionTrace,
    *,
    recorder: _ExecutionRecorder,
    evidence: EvaluationEvidencePack | None = None,
    answer: EvaluationAnswer | None = None,
    structured_success: QuerySuccess | None = None,
    returned_chunk_keys: tuple[str, ...] = (),
    deterministic_output: SyntheticDeterministicOutput | None = None,
    binding_manifest_sha256: str | None = None,
    structured_anchor_targets: tuple[StructuredAnchorTarget, ...] = (),
    anchor_resolution_adapter: str | None = None,
) -> SyntheticExecutionArtifact:
    if tuple(recorder.dependencies) != trace.constructed_dependencies:
        raise ValueError("synthetic dependency ledger differs from execution trace")
    if tuple(recorder.stages) != trace.called_stages:
        raise ValueError("synthetic stage ledger differs from execution trace")
    if recorder.generation_call_count != trace.generation_call_count:
        raise ValueError("synthetic generation ledger differs from execution trace")
    return SyntheticExecutionArtifact(
        system_key=system_key,
        question_id=case.question_id,
        evidence=evidence,
        answer=answer,
        structured_success=structured_success,
        returned_chunk_keys=returned_chunk_keys,
        trace=trace,
        events=tuple(recorder.events),
        deterministic_output=deterministic_output,
        binding_manifest_sha256=binding_manifest_sha256,
        structured_anchor_targets=structured_anchor_targets,
        anchor_resolution_adapter=anchor_resolution_adapter,
    )


def _raw_context_policy(context_limit: int, output_limit: int) -> RawContextPolicy:
    segments = synthetic_raw_segments()
    return build_raw_context_policy(
        source_manifest_sha256=canonical_json_sha256(
            tuple(segment.source_sha256 for segment in segments)
        ),
        structured_export_sha256=segments[0].source_sha256,
        document_manifest_sha256=canonical_json_sha256(
            tuple(segment.source_sha256 for segment in segments[1:])
        ),
        final_partial_segment_allowed=False,
        separator_sha256=hashlib.sha256(b"\n\n").hexdigest(),
        tokenizer_key="tokenizer:synthetic:utf8-byte-v1",
        model_context_limit_tokens=context_limit,
        reserved_output_tokens=output_limit,
    )


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        operating_system="Synthetic OS",
        machine_architecture="synthetic",
        cpu="Synthetic CPU",
        ram_bytes=1,
        accelerator="none",
        python_version="3.12.0",
        uv_version="synthetic",
        postgresql_version="not-used",
        pgvector_version="not-used",
        dependency_lock_sha256="5" * 64,
        dependency_versions={"pydantic": "synthetic", "sqlalchemy": "not-used"},
        thread_settings={"synthetic_threads": "1"},
    )


__all__ = [
    "SyntheticExecutionArtifact",
    "SyntheticExecutionEvent",
    "SyntheticHarnessExecution",
    "execute_synthetic_harness",
    "run_synthetic_benchmark",
]
