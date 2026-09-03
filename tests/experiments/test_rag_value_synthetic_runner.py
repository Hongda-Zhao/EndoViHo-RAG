from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.contracts import ExecutionTrace
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    RefusalObservation,
    score_structured,
    structured_prediction_from_answer,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import SYSTEM_INSTRUCTION
from eve_relation_rag.experiments.rag_value_ablation.reporting import (
    PerQuestionEvaluation,
    ReportingError,
    build_benchmark_run,
    build_per_question_evaluation,
    load_benchmark_run,
)
from eve_relation_rag.experiments.rag_value_ablation.runner import (
    SyntheticExecutionArtifact,
    SyntheticHarnessExecution,
    execute_synthetic_harness,
    run_synthetic_benchmark,
)
from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
    CHUNK_A,
    CHUNK_B,
    SYNTHETIC_FIXTURE_STATUS,
    build_synthetic_fixture_manifest,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import LLM_SYSTEM_KEYS
from eve_relation_rag.experiments.rag_value_ablation.trust import (
    TrustDecisionError,
    issue_phase2_synthetic_trust,
)


def test_phase2_runs_five_cases_across_s0_to_s6_as_test_only() -> None:
    execution = execute_synthetic_harness()
    run = execution.run

    assert len(run.results) == 35
    assert tuple(sorted({result.system_key for result in run.results})) == (
        "S0",
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
    )
    assert all(
        sum(result.system_key == system_key for result in run.results) == 5
        for system_key in ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
    )
    assert run.manifest.phase == "phase2_synthetic"
    assert run.manifest.trust_status == "test_only"
    assert run.manifest.trust_reasons == execution.trust_decision.reasons
    assert (
        run.manifest.synthetic_fixture_manifest_sha256 == execution.fixture_manifest.fixture_sha256
    )
    assert execution.fixture_manifest.fixture_status == SYNTHETIC_FIXTURE_STATUS
    assert run.human_review_status == "not_required"
    assert all(result.trust_status == "test_only" for result in run.results)
    assert all(result.grounding_metrics is None for result in run.results)


def test_synthetic_fixture_checksum_cannot_be_replaced_by_caller() -> None:
    fixture = build_synthetic_fixture_manifest()

    with pytest.raises(ValueError, match="fixture checksum"):
        replace(fixture, fixture_sha256="f" * 64)


def test_system_evidence_routes_and_s5_preservation_are_isolated() -> None:
    execution = execute_synthetic_harness()
    artifacts = _artifacts(execution.artifacts)
    question_id = "synthetic-hybrid-001"

    s0 = artifacts[("S0", question_id)]
    assert s0.evidence is not None
    assert s0.evidence.structured_success is None
    assert s0.evidence.citations == ()
    assert s0.evidence.raw_context_segments == ()

    s1 = artifacts[("S1", question_id)]
    assert s1.evidence is not None
    assert s1.evidence.structured_success is None
    assert s1.evidence.citations == ()
    assert tuple(segment.source_kind for segment in s1.evidence.raw_context_segments) == (
        "structured_export",
        "document",
        "document",
    )
    assert not s1.evidence.construction.truncated
    assert s1.evidence.construction.omitted_source_keys == ()

    s2 = artifacts[("S2", question_id)]
    assert s2.returned_chunk_keys == (CHUNK_A,)
    assert s2.evidence is not None
    assert s2.evidence.structured_success is None

    s3 = artifacts[("S3", question_id)]
    assert s3.returned_chunk_keys == (CHUNK_B, CHUNK_A)
    assert s3.evidence is not None
    assert s3.evidence.structured_success is None

    s4 = artifacts[("S4", question_id)]
    assert s4.evidence is None
    assert s4.answer is None
    assert s4.structured_success is not None
    assert s4.trace.generation_call_count == 0
    assert s4.deterministic_output is not None
    assert s4.deterministic_output.mode == "structured"
    assert s4.deterministic_output.output_text
    assert tuple((event.event_kind, event.name) for event in s4.events) == (
        ("stage_completed", "request_validation"),
        ("dependency_constructed", "database"),
        ("dependency_constructed", "structured_retrieval"),
        ("stage_completed", "structured_planning"),
        ("stage_completed", "structured_retrieval"),
        ("stage_completed", "deterministic_render"),
    )

    s5 = artifacts[("S5", question_id)]
    assert s5.evidence is not None
    assert s5.evidence.structured_success is not None
    assert s5.returned_chunk_keys == (CHUNK_B, CHUNK_A)
    s5_result = _result(execution, "S5", question_id)
    assert s5_result.structured_preservation is not None
    assert s5_result.structured_preservation.preserved
    assert s5.deterministic_output is not None
    assert s5.deterministic_output.mode == "structured_first_hybrid"
    assert s5.deterministic_output.generated_answer == s5.answer
    assert s5.binding_manifest_sha256 == execution.run.manifest.binding_manifest_sha256
    assert s5.structured_anchor_targets == ()
    assert s5.anchor_resolution_adapter == "production_target_extraction_without_anchor_store"
    assert tuple((event.event_kind, event.name) for event in s5.events) == (
        ("stage_completed", "request_validation"),
        ("dependency_constructed", "database"),
        ("dependency_constructed", "structured_retrieval"),
        ("stage_completed", "structured_planning"),
        ("stage_completed", "release_binding"),
        ("stage_completed", "structured_retrieval"),
        ("stage_completed", "anchor_resolution"),
        ("dependency_constructed", "corpus"),
        ("dependency_constructed", "fts"),
        ("dependency_constructed", "embedding_provider"),
        ("dependency_constructed", "dense_index"),
        ("dependency_constructed", "summary_index"),
        ("dependency_constructed", "rrf"),
        ("stage_completed", "fts_retrieval"),
        ("stage_completed", "dense_retrieval"),
        ("stage_completed", "summary_retrieval"),
        ("stage_completed", "rrf_fusion"),
        ("stage_completed", "chunk_hydration"),
        ("stage_completed", "context_construction"),
        ("dependency_constructed", "llm_provider"),
        ("stage_completed", "generation"),
        ("stage_completed", "mechanical_validation"),
        ("stage_completed", "deterministic_render"),
    )

    s6 = artifacts[("S6", question_id)]
    assert s6.evidence is not None
    assert s6.evidence.structured_success is not None
    assert s6.evidence.oracle_entry_sha256 is not None
    assert s6.returned_chunk_keys == (CHUNK_A, CHUNK_B)

    for artifact in execution.artifacts:
        has_oracle = (
            artifact.evidence is not None and artifact.evidence.oracle_entry_sha256 is not None
        )
        if has_oracle:
            assert artifact.system_key == "S6"
            assert "oracle_loader" in artifact.trace.constructed_dependencies
            assert "oracle_load" in artifact.trace.called_stages

    for system in execution.run.manifest.systems:
        artifact = artifacts[(system.system_key, question_id)]
        assert artifact.trace.constructed_dependencies == system.allowed_dependencies
        assert artifact.trace.called_stages == system.required_success_stages
        assert tuple(event.sequence for event in artifact.events) == tuple(
            range(1, len(artifact.events) + 1)
        )
        assert (
            tuple(
                event.name
                for event in artifact.events
                if event.event_kind == "dependency_constructed"
            )
            == artifact.trace.constructed_dependencies
        )
        assert (
            tuple(event.name for event in artifact.events if event.event_kind == "stage_completed")
            == artifact.trace.called_stages
        )


def test_scope_policy_refusal_precedes_every_downstream_capability() -> None:
    execution = execute_synthetic_harness()
    unsupported_id = "synthetic-unsupported-policy-001"
    unsupported = tuple(
        artifact for artifact in execution.artifacts if artifact.question_id == unsupported_id
    )

    assert len(unsupported) == 7
    assert all(artifact.trace.status == "refused" for artifact in unsupported)
    assert all(artifact.trace.called_stages == ("request_validation",) for artifact in unsupported)
    assert all(artifact.trace.refusal_stage == "request_validation" for artifact in unsupported)
    assert all(artifact.trace.constructed_dependencies == () for artifact in unsupported)
    assert all(artifact.trace.generation_call_count == 0 for artifact in unsupported)
    assert all(artifact.evidence is None for artifact in unsupported)
    assert all(artifact.answer is None for artifact in unsupported)
    assert all(artifact.structured_success is None for artifact in unsupported)
    assert all(
        artifact.events[0].event_kind == "stage_completed"
        and artifact.events[0].name == "request_validation"
        and len(artifact.events) == 1
        for artifact in unsupported
    )
    assert all(
        unsupported_id not in request.user_payload_json for request in execution.provider_requests
    )
    assert all(
        observation is not None
        and observation.refusal_appropriate
        and observation.refusal_origin == "shared_scope_policy"
        and observation.downstream_call_count_after_refusal == 0
        for observation in (
            _result(execution, artifact.system_key, unsupported_id).refusal_observation
            for artifact in unsupported
        )
    )


def test_unsupported_evidence_is_scored_from_system_output_not_gold_routing() -> None:
    execution = execute_synthetic_harness()
    question_id = "synthetic-unsupported-evidence-001"
    results = {
        system_key: _result(execution, system_key, question_id)
        for system_key in ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
    }

    assert {key for key, result in results.items() if result.status == "refused"} == {
        "S0",
        "S4",
        "S5",
        "S6",
    }
    assert {key for key, result in results.items() if result.status == "completed"} == {
        "S1",
        "S2",
        "S3",
    }
    assert {
        key
        for key, result in results.items()
        if result.refusal_observation is not None
        and result.refusal_observation.unsafe_acceptance
    } == {"S1", "S2", "S3"}
    assert results["S0"].refusal_observation is not None
    assert results["S0"].refusal_observation.refusal_origin == "model_abstention"
    assert results["S6"].refusal_observation is not None
    assert results["S6"].refusal_observation.refusal_origin == "model_abstention"
    for system_key in ("S4", "S5"):
        result = results[system_key]
        assert result.refusal_observation is not None
        assert result.refusal_observation.refusal_origin == "system_route_policy"
        assert result.execution_trace.called_stages == ("request_validation",)
        assert result.execution_trace.constructed_dependencies == ()
    requests = tuple(
        request
        for request in execution.provider_requests
        if "Which source passage supports the missing synthetic association?"
        in request.user_payload_json
    )
    assert len(requests) == 5
    assert all("expected_refusal" not in request.user_payload_json for request in requests)


def test_fair_comparison_denominator_and_fake_provider_are_explicit() -> None:
    execution = execute_synthetic_harness()

    assert execution.comparison_eligible_question_ids == (
        "synthetic-hybrid-001",
        "synthetic-structured-001",
    )
    assert len(execution.comparison_inputs) == 12
    for question_id in execution.comparison_eligible_question_ids:
        records = tuple(
            record for record in execution.comparison_inputs if record.question_id == question_id
        )
        assert tuple(record.system_key for record in records) == LLM_SYSTEM_KEYS
        assert len({record.question_text for record in records}) == 1
        assert len({record.question_text_sha256 for record in records}) == 1
        assert len({record.generation_identity_sha256 for record in records}) == 1

    # S4 never generates; pure literature S4/S5 are explicitly inapplicable.
    # The evidence-insufficient unsupported case reaches five generation paths;
    # the external-tool policy case reaches none.
    assert len(execution.provider_requests) == 22
    identity = execution.run.manifest.generation_identity
    assert identity is not None
    assert all(
        set(json.loads(request.user_payload_json)) == {"evidence", "instruction"}
        for request in execution.provider_requests
    )
    assert all(
        "system_key" not in json.loads(request.user_payload_json)
        for request in execution.provider_requests
    )
    assert all("gold" not in request.user_payload_json for request in execution.provider_requests)
    assert all(
        request.system_instruction == SYSTEM_INSTRUCTION for request in execution.provider_requests
    )
    assert all(
        request.generation_identity == identity
        and request.temperature == 0
        and request.max_output_tokens == identity.max_output_tokens
        and request.max_output_bytes == identity.max_output_bytes
        for request in execution.provider_requests
    )
    assert (
        len(
            {request.generation_identity.identity_sha256 for request in execution.provider_requests}
        )
        == 1
    )
    assert identity.provider_kind == "deterministic_fake"
    assert identity.temperature == 0
    assert identity.retry_count == 0
    assert not identity.tools_enabled
    assert not identity.web_enabled
    assert not identity.conversation_memory_enabled


def test_phase2_trust_requires_refusal_observations_for_every_applicable_result() -> None:
    execution = execute_synthetic_harness()
    target = _result(execution, "S0", "synthetic-hybrid-001")
    payload = target.model_dump(mode="python")
    payload.pop("result_sha256")
    payload["refusal_observation"] = None
    altered = build_per_question_evaluation(**payload)
    run = build_benchmark_run(
        manifest=execution.run.manifest,
        human_review_status=execution.run.human_review_status,
        results=tuple(
            altered if result is target else result for result in execution.run.results
        ),
        comparison_eligible_question_ids=(
            execution.run.comparison_eligible_question_ids
        ),
        comparison_inputs=execution.run.comparison_inputs,
        failures=execution.run.failures,
    )

    with pytest.raises(TrustDecisionError, match="every applicable result"):
        issue_phase2_synthetic_trust(
            run=run,
            fixture_manifest=execution.fixture_manifest,
        )

    policy = _result(execution, "S5", "synthetic-unsupported-policy-001")
    inapplicable = build_per_question_evaluation(
        system_key=policy.system_key,
        question_id=policy.question_id,
        family=policy.family,
        trust_status=policy.trust_status,
        status="not_applicable",
        question_text_sha256=policy.question_text_sha256,
        execution_trace=ExecutionTrace(
            system_key=policy.system_key,
            question_id=policy.question_id,
            status="not_applicable",
            generation_call_count=0,
        ),
    )
    applicability_drift = build_benchmark_run(
        manifest=execution.run.manifest,
        human_review_status=execution.run.human_review_status,
        results=tuple(
            inapplicable if result is policy else result
            for result in execution.run.results
        ),
        comparison_eligible_question_ids=(
            execution.run.comparison_eligible_question_ids
        ),
        comparison_inputs=execution.run.comparison_inputs,
        failures=execution.run.failures,
    )
    with pytest.raises(TrustDecisionError, match="applicability matrix"):
        issue_phase2_synthetic_trust(
            run=applicability_drift,
            fixture_manifest=execution.fixture_manifest,
        )


def test_refusal_origin_is_bound_to_generation_and_frozen_policy_path() -> None:
    execution = execute_synthetic_harness()
    generated = _result(execution, "S0", "synthetic-hybrid-001")
    assert generated.refusal_observation is not None
    status_payload = generated.model_dump(mode="python")
    status_payload.pop("result_sha256")
    status_payload["refusal_observation"] = RefusalObservation(
        question_id=generated.question_id,
        expected_refusal=False,
        abstained=False,
        refusal_origin="none",
        refusal_appropriate=False,
        unsafe_acceptance=False,
        downstream_call_count_after_refusal=0,
    )
    with pytest.raises(ValueError, match="abstention does not match"):
        build_per_question_evaluation(**status_payload)

    generated_payload = generated.model_dump(mode="python")
    generated_payload.pop("result_sha256")
    generated_payload["refusal_observation"] = RefusalObservation(
        **{
            **generated.refusal_observation.model_dump(mode="python"),
            "refusal_origin": "shared_scope_policy",
        }
    )
    with pytest.raises(ValueError, match="policy-refusal origin"):
        build_per_question_evaluation(**generated_payload)

    policy = _result(execution, "S5", "synthetic-unsupported-policy-001")
    assert policy.refusal_observation is not None
    policy_payload = policy.model_dump(mode="python")
    policy_payload.pop("result_sha256")
    policy_payload["refusal_observation"] = RefusalObservation(
        **{
            **policy.refusal_observation.model_dump(mode="python"),
            "refusal_origin": "system_route_policy",
        }
    )
    altered = build_per_question_evaluation(**policy_payload)
    run = build_benchmark_run(
        manifest=execution.run.manifest,
        human_review_status=execution.run.human_review_status,
        results=tuple(
            altered if result is policy else result for result in execution.run.results
        ),
        comparison_eligible_question_ids=(
            execution.run.comparison_eligible_question_ids
        ),
        comparison_inputs=execution.run.comparison_inputs,
        failures=execution.run.failures,
    )
    with pytest.raises(TrustDecisionError, match="scope/route execution"):
        issue_phase2_synthetic_trust(
            run=run,
            fixture_manifest=execution.fixture_manifest,
        )


def test_phase2_refusal_metric_fields_cannot_be_rehashed_to_change_scores() -> None:
    execution = execute_synthetic_harness()
    unsupported = _result(execution, "S0", "synthetic-unsupported-evidence-001")
    assert unsupported.refusal_observation is not None

    metric_payload = unsupported.model_dump(mode="python")
    metric_payload.pop("result_sha256")
    metric_payload["refusal_observation"] = RefusalObservation(
        **{
            **unsupported.refusal_observation.model_dump(mode="python"),
            "refusal_appropriate": False,
        }
    )
    altered_metric = build_per_question_evaluation(**metric_payload)
    altered_run = build_benchmark_run(
        manifest=execution.run.manifest,
        human_review_status=execution.run.human_review_status,
        results=tuple(
            altered_metric if result is unsupported else result
            for result in execution.run.results
        ),
        comparison_eligible_question_ids=(
            execution.run.comparison_eligible_question_ids
        ),
        comparison_inputs=execution.run.comparison_inputs,
        failures=execution.run.failures,
    )
    with pytest.raises(TrustDecisionError, match="refusal metrics differ"):
        issue_phase2_synthetic_trust(
            run=altered_run,
            fixture_manifest=execution.fixture_manifest,
        )

    refused = _result(execution, "S0", "synthetic-unsupported-policy-001")
    assert refused.refusal_observation is not None
    downstream_payload = refused.model_dump(mode="python")
    downstream_payload.pop("result_sha256")
    downstream_payload["refusal_observation"] = RefusalObservation(
        **{
            **refused.refusal_observation.model_dump(mode="python"),
            "downstream_call_count_after_refusal": 1,
        }
    )
    altered_downstream = build_per_question_evaluation(**downstream_payload)
    downstream_run = build_benchmark_run(
        manifest=execution.run.manifest,
        human_review_status=execution.run.human_review_status,
        results=tuple(
            altered_downstream if result is refused else result
            for result in execution.run.results
        ),
        comparison_eligible_question_ids=(
            execution.run.comparison_eligible_question_ids
        ),
        comparison_inputs=execution.run.comparison_inputs,
        failures=execution.run.failures,
    )
    with pytest.raises(TrustDecisionError, match="post-refusal call count"):
        issue_phase2_synthetic_trust(
            run=downstream_run,
            fixture_manifest=execution.fixture_manifest,
        )


def test_fake_provider_request_settings_are_checksum_bound() -> None:
    execution = execute_synthetic_harness()
    request = execution.provider_requests[0]

    with pytest.raises(ValueError, match="temperature differs"):
        replace(request, temperature=1)
    with pytest.raises(ValueError, match="checksum"):
        replace(
            request,
            user_payload_json=request.user_payload_json.replace(
                "Count distinct included loci in this release.",
                "Count distinct assemblies in this release.",
            ),
        )


def test_exact_synthetic_metrics_are_calculated_without_human_grounding() -> None:
    execution = execute_synthetic_harness()
    hybrid_id = "synthetic-hybrid-001"

    s2 = _result(execution, "S2", hybrid_id)
    assert s2.retrieval_metrics is not None
    assert s2.retrieval_metrics.recall_at_1 == "0.500000000000"
    assert s2.retrieval_metrics.recall_at_10 == "0.500000000000"

    for system_key in ("S3", "S5"):
        result = _result(execution, system_key, hybrid_id)
        assert result.retrieval_metrics is not None
        assert result.retrieval_metrics.recall_at_1 == "0.500000000000"
        assert result.retrieval_metrics.recall_at_10 == "1.000000000000"

    for system_key in ("S4", "S5", "S6"):
        result = _result(execution, system_key, hybrid_id)
        assert result.structured_metrics is not None
        assert result.structured_metrics.numeric_exact_match
        assert result.structured_metrics.metric_key_exact_match
        assert result.structured_metrics.release_provenance_exact
        assert result.grounding_metrics is None

    s0 = _result(execution, "S0", hybrid_id)
    assert s0.structured_metrics is not None
    assert s0.structured_metrics.numeric_exact_match is False
    assert s0.structured_metrics.release_provenance_exact is False

    s1 = _result(execution, "S1", hybrid_id)
    assert s1.structured_metrics is not None
    assert s1.structured_metrics.numeric_exact_match is True
    assert s1.structured_metrics.release_provenance_exact is False

    for system_key in ("S2", "S3"):
        result = _result(execution, system_key, hybrid_id)
        assert result.structured_metrics is not None
        assert result.structured_metrics.numeric_exact_match is False

    hybrid_case = next(
        case for case in execution.fixture_manifest.cases if case.question_id == hybrid_id
    )
    assert hybrid_case.structured_gold is not None
    s6 = _result(execution, "S6", hybrid_id)
    assert s6.answer is not None
    assert s6.structured_metrics == score_structured(
        hybrid_case.structured_gold,
        structured_prediction_from_answer(s6.answer),
    )

    s5_artifact = _artifacts(execution.artifacts)[("S5", hybrid_id)]
    assert s5_artifact.answer is not None
    assert s5_artifact.deterministic_output is not None
    assert s5_artifact.deterministic_output.generated_answer is not None
    assert (
        s5_artifact.answer.structured_facts
        == s5_artifact.deterministic_output.generated_answer.structured_facts
    )


def test_explicit_output_is_create_once_and_deterministic(tmp_path: Path) -> None:
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = run_synthetic_benchmark(first_path)
    second = run_synthetic_benchmark(second_path)

    assert first.run == second.run
    assert first.provider_requests == second.provider_requests
    assert _tree_bytes(first_path) == _tree_bytes(second_path)
    assert load_benchmark_run(first_path) == first.run
    assert (first_path / "experiment_manifest.json").is_file()
    assert (first_path / "per_question").is_dir()
    assert (first_path / "summary.json").is_file()
    assert not (tmp_path / "rag_value_ablation.md").exists()

    for artifact in first.artifacts:
        if artifact.deterministic_output is None:
            continue
        result = _result(first, artifact.system_key, artifact.question_id)
        assert result.deterministic_output_text == artifact.deterministic_output.output_text
        assert result.deterministic_output_sha256 == artifact.deterministic_output.output_sha256

    with pytest.raises(ReportingError, match="already exists"):
        run_synthetic_benchmark(first_path)


def _result(
    execution: SyntheticHarnessExecution,
    system_key: str,
    question_id: str,
) -> PerQuestionEvaluation:
    return next(
        result
        for result in execution.run.results
        if result.system_key == system_key and result.question_id == question_id
    )


def _artifacts(
    artifacts: tuple[SyntheticExecutionArtifact, ...],
) -> dict[tuple[str, str], SyntheticExecutionArtifact]:
    return {(artifact.system_key, artifact.question_id): artifact for artifact in artifacts}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
