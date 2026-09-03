from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    ExecutionTrace,
    MechanicalValidation,
    RuntimeIdentity,
    StructuredPreservationProof,
    build_experiment_manifest,
    build_generation_identity,
    build_raw_context_policy,
    build_retrieval_policy_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    EfficiencyObservation,
    RefusalObservation,
)
from eve_relation_rag.experiments.rag_value_ablation.reporting import (
    ReportingError,
    build_benchmark_run,
    build_per_question_evaluation,
    generate_markdown_report,
    load_benchmark_run,
    write_benchmark_outputs,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    build_system_definitions,
)


def test_test_only_outputs_are_complete_create_once_and_deterministic(tmp_path: Path) -> None:
    run = _run()
    refused = tmp_path / "refused"
    with pytest.raises(ReportingError, match="explicit allow_test_output"):
        write_benchmark_outputs(refused, run)

    first = tmp_path / "first"
    second = tmp_path / "second"
    write_benchmark_outputs(first, run, allow_test_output=True)
    write_benchmark_outputs(second, run, allow_test_output=True)

    assert load_benchmark_run(first) == run
    assert _tree_bytes(first) == _tree_bytes(second)
    assert {
        "experiment_manifest.json",
        "question_schema.json",
        "questions_template.jsonl",
        "human_review_template.csv",
        "retrieval_metrics.csv",
        "answer_metrics.csv",
        "refusal_metrics.csv",
        "latency_metrics.csv",
        "plot_no_rag_vs_rag.csv",
        "plot_structured_correctness.csv",
        "plot_claim_support.csv",
        "plot_refusal.csv",
        "plot_retrieval_quality.csv",
        "plot_quality_latency.csv",
        "summary.json",
        "failures.jsonl",
    } <= set(_tree_bytes(first))
    assert (first / "questions_template.jsonl").read_bytes() == b"\n"
    assert (first / "human_review_template.csv").read_text().startswith(
        "packet_sha256,reviewer_key,reviewed_at,blind_answer_id"
    )
    assert len(tuple((first / "systems").glob("*.json"))) == 7
    assert len(tuple((first / "per_question").rglob("*.json"))) == 7
    committed_templates = Path(__file__).parents[2] / "benchmark" / "rag_value_ablation"
    for name in (
        "question_schema.json",
        "questions_template.jsonl",
        "human_review_template.csv",
    ):
        assert (first / name).read_bytes() == (committed_templates / name).read_bytes()

    with pytest.raises(ReportingError, match="already exists"):
        write_benchmark_outputs(first, run, allow_test_output=True)
    with pytest.raises(ReportingError, match="formal report requires trusted"):
        generate_markdown_report(first)


def test_derived_file_edit_is_detected_on_reload(tmp_path: Path) -> None:
    output = tmp_path / "output"
    write_benchmark_outputs(output, _run(), allow_test_output=True)
    summary = output / "summary.json"
    summary.write_bytes(summary.read_bytes().replace(b'"failure_count":0', b'"failure_count":1'))

    with pytest.raises(ReportingError, match="not canonical"):
        load_benchmark_run(output)


def test_s5_result_requires_a_byte_identical_structured_preservation_proof() -> None:
    run = _run()
    s5 = next(result for result in run.results if result.system_key == "S5")
    altered_payload = s5.model_dump(mode="python")
    del altered_payload["result_sha256"]
    altered_payload["structured_preservation"] = None
    altered = build_per_question_evaluation(**altered_payload)
    results = tuple(altered if result.system_key == "S5" else result for result in run.results)

    with pytest.raises(ValueError, match="preserve the structured result"):
        build_benchmark_run(
            manifest=run.manifest,
            human_review_status=run.human_review_status,
            results=results,
            failures=(),
        )


def test_deterministic_fake_generation_identity_can_never_be_marked_trusted() -> None:
    run = _run()
    manifest = run.manifest

    with pytest.raises(ValidationError, match="verified local provider"):
        build_experiment_manifest(
            experiment_key=manifest.experiment_key,
            phase="phase4_llm",
            trust_status="trusted",
            trust_reasons=("incorrect caller-authored trust claim",),
            source_commit=manifest.source_commit,
            source_tree_clean=True,
            production_source_fingerprint_sha256=(
                manifest.production_source_fingerprint_sha256
            ),
            question_manifest_sha256=manifest.question_manifest_sha256,
            oracle_manifest_sha256=manifest.oracle_manifest_sha256,
            dataset_release_key="release:test:v0:20990101:001",
            dataset_manifest_sha256="d" * 64,
            corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
            corpus_manifest_sha256="e" * 64,
            binding_manifest_sha256="f" * 64,
            generation_identity=manifest.generation_identity,
            retrieval_policy=build_retrieval_policy_identity(
                embedding_artifact_manifest_sha256="a" * 64
            ),
            raw_context_policy=manifest.raw_context_policy,
            pricing_manifest_sha256=None,
            runtime_identity=manifest.runtime_identity,
            systems=manifest.systems,
        )


def test_trusted_phase3_run_records_retrieval_without_binding_an_llm() -> None:
    systems = build_system_definitions(None)
    manifest = build_experiment_manifest(
        experiment_key="experiment:rag-value:retrieval-only-001",
        phase="phase3_retrieval",
        trust_status="trusted",
        trust_reasons=("approved retrieval-only inputs",),
        source_commit="f" * 40,
        source_tree_clean=True,
        production_source_fingerprint_sha256="0" * 64,
        question_manifest_sha256="1" * 64,
        dataset_release_key="release:test:v0:20990101:001",
        dataset_manifest_sha256="d" * 64,
        corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
        corpus_manifest_sha256="e" * 64,
        generation_identity=None,
        retrieval_policy=build_retrieval_policy_identity(
            embedding_artifact_manifest_sha256="a" * 64
        ),
        raw_context_policy=build_raw_context_policy(
            source_manifest_sha256="9" * 64,
            structured_export_sha256="a" * 64,
            document_manifest_sha256="b" * 64,
            final_partial_segment_allowed=False,
            separator_sha256="c" * 64,
            tokenizer_key="tokenizer:approved",
            model_context_limit_tokens=4096,
            reserved_output_tokens=512,
        ),
        runtime_identity=_runtime_identity(),
        systems=systems,
    )
    statuses = {
        "S0": "not_applicable",
        "S1": "retrieval_only",
        "S2": "retrieval_only",
        "S3": "retrieval_only",
        "S4": "completed",
        "S5": "not_applicable",
        "S6": "not_applicable",
    }
    results = []
    for system in systems:
        status = statuses[system.system_key]
        if status == "retrieval_only":
            called_stages = tuple(
                stage
                for stage in system.required_success_stages
                if stage
                not in {"generation", "mechanical_validation", "deterministic_render"}
            )
        elif status == "completed":
            called_stages = system.required_success_stages
        else:
            called_stages = ()
        results.append(
            build_per_question_evaluation(
                system_key=system.system_key,
                question_id="approved-001",
                family="hybrid",
                trust_status="trusted",
                status=status,
                question_text_sha256="3" * 64,
                evidence_pack_sha256=(
                    "4" * 64 if status == "retrieval_only" else None
                ),
                execution_trace=ExecutionTrace(
                    system_key=system.system_key,
                    question_id="approved-001",
                    status=status,
                    constructed_dependencies=(
                        tuple(
                            dependency
                            for dependency in system.allowed_dependencies
                            if dependency != "llm_provider"
                        )
                        if status in {"retrieval_only", "completed"}
                        else ()
                    ),
                    called_stages=called_stages,
                    generation_call_count=0,
                ),
            )
        )

    run = build_benchmark_run(
        manifest=manifest,
        human_review_status="not_required",
        results=tuple(results),
    )

    assert run.manifest.generation_identity is None
    assert sum(result.status == "retrieval_only" for result in run.results) == 3


def _run():
    identity = build_generation_identity(
        provider_key="provider:deterministic-fake",
        provider_kind="deterministic_fake",
        model_id="example/fake",
        exact_revision="a" * 40,
        model_artifact_manifest_sha256="6" * 64,
        tokenizer_id="example/fake-tokenizer",
        tokenizer_revision="b" * 40,
        tokenizer_artifact_manifest_sha256="7" * 64,
        system_instruction_sha256="c" * 64,
        request_template_sha256="d" * 64,
        output_schema_sha256="e" * 64,
        temperature=0,
        max_output_tokens=512,
        max_output_bytes=16384,
        context_limit_tokens=4096,
        timeout_seconds=30,
        retry_count=0,
        request_concurrency=1,
        seed=7,
        tools_enabled=False,
        web_enabled=False,
        conversation_memory_enabled=False,
    )
    systems = build_system_definitions(identity)
    manifest = build_experiment_manifest(
        experiment_key="experiment:rag-value:synthetic-001",
        phase="phase2_synthetic",
        trust_status="test_only",
        trust_reasons=("deterministic fake provider",),
        source_commit="f" * 40,
        source_tree_clean=False,
        production_source_fingerprint_sha256="0" * 64,
        question_manifest_sha256="1" * 64,
        oracle_manifest_sha256="8" * 64,
        dataset_release_key=None,
        dataset_manifest_sha256=None,
        corpus_release_key=None,
        corpus_manifest_sha256=None,
        binding_manifest_sha256=None,
        generation_identity=identity,
        retrieval_policy=build_retrieval_policy_identity(
            embedding_artifact_manifest_sha256=None
        ),
        raw_context_policy=build_raw_context_policy(
            source_manifest_sha256="9" * 64,
            structured_export_sha256="a" * 64,
            document_manifest_sha256="b" * 64,
            final_partial_segment_allowed=False,
            separator_sha256="c" * 64,
            tokenizer_key="tokenizer:synthetic",
            model_context_limit_tokens=4096,
            reserved_output_tokens=512,
        ),
        pricing_manifest_sha256=None,
        runtime_identity=_runtime_identity(),
        systems=systems,
    )
    results = []
    for system in systems:
        results.append(
            build_per_question_evaluation(
                system_key=system.system_key,
                question_id="synthetic-001",
                family="hybrid",
                trust_status="test_only",
                status="completed",
                question_text_sha256="3" * 64,
                evidence_pack_sha256=(
                    None if system.system_key == "S4" else f"{int(system.system_key[1:]) + 4:064x}"
                ),
                answer_sha256=None if system.system_key == "S4" else "4" * 64,
                execution_trace=ExecutionTrace(
                    system_key=system.system_key,
                    question_id="synthetic-001",
                    status="completed",
                    constructed_dependencies=system.allowed_dependencies,
                    called_stages=system.required_success_stages,
                    refusal_stage=None,
                    generation_call_count=int(system.uses_llm),
                ),
                mechanical_validation=(
                    None
                    if system.system_key == "S4"
                    else MechanicalValidation(passed=True, issue_codes=())
                ),
                structured_preservation=(
                    StructuredPreservationProof(
                        input_structured_result_sha256="5" * 64,
                        output_structured_result_sha256="5" * 64,
                        preserved=True,
                    )
                    if system.system_key == "S5"
                    else None
                ),
                structured_metrics=None,
                retrieval_metrics=None,
                grounding_metrics=None,
                refusal_observation=RefusalObservation(
                    question_id="synthetic-001",
                    expected_refusal=False,
                    abstained=False,
                    refusal_appropriate=False,
                    unsafe_acceptance=False,
                    downstream_call_count_after_refusal=0,
                ),
                efficiency=EfficiencyObservation(
                    system_key=system.system_key,
                    question_id="synthetic-001",
                    latency_ns=100 + int(system.system_key[1:]),
                    input_tokens=0 if system.system_key == "S4" else 10,
                    output_tokens=0 if system.system_key == "S4" else 2,
                    context_tokens=0 if system.system_key == "S4" else 3,
                    cost=None,
                    peak_process_rss_bytes=1000,
                    peak_accelerator_memory_bytes=None,
                ),
            )
        )
    return build_benchmark_run(
        manifest=manifest,
        human_review_status="not_required",
        results=tuple(results),
        failures=(),
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        operating_system="Synthetic OS",
        machine_architecture="arm64",
        cpu="Synthetic CPU",
        ram_bytes=1024,
        accelerator="none",
        python_version="3.12",
        uv_version="0.8.15",
        postgresql_version="synthetic",
        pgvector_version="synthetic",
        dependency_lock_sha256="2" * 64,
        dependency_versions={"pydantic": "2.13.0", "sqlalchemy": "2.0.43"},
        thread_settings={"OMP_NUM_THREADS": "1"},
    )
