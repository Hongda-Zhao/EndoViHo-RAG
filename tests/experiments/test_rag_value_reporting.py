from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    ExecutionTrace,
    RuntimeIdentity,
    build_experiment_manifest,
    build_raw_context_policy,
    build_retrieval_policy_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
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
from eve_relation_rag.experiments.rag_value_ablation.runner import (
    execute_synthetic_harness,
)
from eve_relation_rag.experiments.rag_value_ablation.synthetic import (
    build_synthetic_fixture_manifest,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    build_system_definitions,
)
from eve_relation_rag.experiments.rag_value_ablation.trust import (
    RunTrustDecision,
    issue_phase2_synthetic_trust,
)


def test_test_only_outputs_are_complete_create_once_and_deterministic(tmp_path: Path) -> None:
    run = _run()
    refused = tmp_path / "refused"
    with pytest.raises(ReportingError, match="explicit allow_test_output"):
        write_benchmark_outputs(refused, run, _trust(run))

    first = tmp_path / "first"
    second = tmp_path / "second"
    write_benchmark_outputs(first, run, _trust(run), allow_test_output=True)
    write_benchmark_outputs(second, run, _trust(run), allow_test_output=True)

    assert load_benchmark_run(first) == run
    assert all(
        ",test_only," in line
        for name in (
            "retrieval_metrics.csv",
            "answer_metrics.csv",
            "refusal_metrics.csv",
            "latency_metrics.csv",
        )
        for line in (first / name).read_text(encoding="utf-8").splitlines()[1:]
    )
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
        "TEST_ONLY_REPORT.md",
    } <= set(_tree_bytes(first))
    assert (first / "TEST_ONLY_REPORT.md").read_text(encoding="utf-8").startswith(
        "# TEST ONLY"
    )
    assert (first / "questions_template.jsonl").read_bytes() == b"\n"
    assert (first / "human_review_template.csv").read_text().startswith(
        "packet_sha256,reviewer_key,reviewed_at,blind_answer_id"
    )
    answer_header = (first / "answer_metrics.csv").read_text().splitlines()[0]
    assert "exact_association_set_exact" in answer_header
    assert "source_reported_association_class_corrupted_count" in answer_header
    assert "cross_source_association_scope_corrupted_count" in answer_header
    assert "citation_passage_accuracy" in answer_header
    assert len(tuple((first / "systems").glob("*.json"))) == 7
    assert len(tuple((first / "per_question").rglob("*.json"))) == 35
    committed_templates = Path(__file__).parents[2] / "benchmark" / "rag_value_ablation"
    for name in (
        "question_schema.json",
        "questions_template.jsonl",
        "human_review_template.csv",
    ):
        assert (first / name).read_bytes() == (committed_templates / name).read_bytes()

    with pytest.raises(ReportingError, match="already exists"):
        write_benchmark_outputs(first, run, _trust(run), allow_test_output=True)
    with pytest.raises(ReportingError, match="formal report requires trusted"):
        generate_markdown_report(first, _trust(run))


def test_derived_file_edit_is_detected_on_reload(tmp_path: Path) -> None:
    output = tmp_path / "output"
    run = _run()
    write_benchmark_outputs(output, run, _trust(run), allow_test_output=True)
    summary = output / "summary.json"
    summary.write_bytes(summary.read_bytes().replace(b'"failure_count":0', b'"failure_count":1'))

    with pytest.raises(ReportingError, match="not canonical"):
        load_benchmark_run(output)


def test_summary_and_plots_use_paired_answers_and_matched_refusal_denominators(
    tmp_path: Path,
) -> None:
    run = _run()
    output = tmp_path / "aggregates"
    write_benchmark_outputs(output, run, _trust(run), allow_test_output=True)

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["paired_llm_question_ids"] == list(
        run.comparison_eligible_question_ids
    )
    assert summary["paired_llm_question_count"] == 2
    assert summary["matched_llm_system_refusal_question_count"] == 4
    assert summary["paired_llm_efficiency_question_count"] == 2

    systems = {row["system_key"]: row for row in summary["systems"]}
    assert systems["S0"]["refusal_metrics"]["correct_refusal_rate"] == {
        "denominator": 2,
        "numerator": 2,
        "undefined_reason": None,
        "value": "1.000000000000",
    }
    assert systems["S0"]["matched_llm_system_refusal_metrics"][
        "false_refusal_rate"
    ] == {
        "denominator": 2,
        "numerator": 2,
        "undefined_reason": None,
        "value": "1.000000000000",
    }
    assert systems["S1"]["matched_llm_system_refusal_metrics"][
        "false_refusal_rate"
    ]["value"] == "0.000000000000"
    assert systems["S1"]["matched_llm_system_refusal_metrics"][
        "correct_refusal_rate"
    ]["value"] == "0.500000000000"
    assert systems["S1"]["matched_llm_system_refusal_metrics"][
        "unsafe_acceptance_rate"
    ]["value"] == "0.500000000000"
    assert systems["S4"]["matched_llm_system_refusal_metrics"] is None
    assert systems["S4"]["paired_llm_efficiency_summary"] is None
    for system in systems.values():
        assert {
            "correct_refusal_rate",
            "false_refusal_rate",
            "unsafe_acceptance_rate",
            "downstream_calls_after_refusal",
            "downstream_call_violation_rate",
        } <= set(system["refusal_metrics"])
        assert {
            "sample_count",
            "p50_latency_ns",
            "p95_latency_ns",
            "total_input_tokens",
            "total_output_tokens",
            "total_context_tokens",
            "total_cost",
            "peak_process_rss_bytes",
            "peak_accelerator_memory_bytes",
        } <= set(system["efficiency_summary"])

    paired_answer_rows = _csv_rows(output / "plot_no_rag_vs_rag.csv")
    assert len(paired_answer_rows) == 12
    assert {row["question_id"] for row in paired_answer_rows} == set(
        run.comparison_eligible_question_ids
    )
    assert all(row["paired_llm_question"] == "True" for row in paired_answer_rows)
    assert {
        row["system_key"]
        for row in paired_answer_rows
        if row["status"] == "refused"
    } == {"S0"}

    refusal_rows = {
        row["system_key"]: row for row in _csv_rows(output / "plot_refusal.csv")
    }
    assert len(refusal_rows) == 7
    assert refusal_rows["S0"]["correct_refusal_rate"] == "1.000000000000"
    assert refusal_rows["S0"]["false_refusal_rate"] == "1.000000000000"
    assert refusal_rows["S0"]["unsafe_acceptance_rate"] == "0.000000000000"
    assert refusal_rows["S0"]["downstream_call_violation_rate"] == (
        "0.000000000000"
    )
    assert (
        refusal_rows["S0"]["matched_llm_system_correct_refusal_rate"]
        == "1.000000000000"
    )
    assert (
        refusal_rows["S1"]["matched_llm_system_correct_refusal_rate"]
        == "0.500000000000"
    )
    assert (
        refusal_rows["S1"]["matched_llm_system_unsafe_acceptance_rate"]
        == "0.500000000000"
    )

    quality_rows = {
        row["system_key"]: row
        for row in _csv_rows(output / "plot_quality_latency.csv")
    }
    assert len(quality_rows) == 7
    for system_key in ("S0", "S1", "S2", "S3", "S5", "S6"):
        row = quality_rows[system_key]
        assert row["comparison_scope"] == "paired_llm"
        assert row["comparison_eligible_question_count"] == "2"
        assert row["paired_llm_question_count"] == "2"
        assert row["paired_sample_count"] == "2"
        assert row["paired_p50_latency_ns"]
        assert row["paired_p95_latency_ns"]
        assert row["paired_total_input_tokens"]
        assert row["paired_peak_process_rss_bytes"]
    assert quality_rows["S4"]["comparison_scope"] == "not_in_llm_comparison"
    assert quality_rows["S4"]["paired_sample_count"] == ""
    assert quality_rows["S4"]["observed_sample_count"] == "4"


def test_reporting_rejects_forged_or_mismatched_runtime_authority(tmp_path: Path) -> None:
    run = _run()
    with pytest.raises(TypeError, match="only be issued"):
        RunTrustDecision(
            status="test_only",
            phase="phase2_synthetic",
            reasons=("synthetic fixtures only",),
            manifest_sha256=run.manifest.manifest_sha256,
            run_sha256=run.run_sha256,
            synthetic_fixture_manifest_sha256=(
                run.manifest.synthetic_fixture_manifest_sha256
            ),
            _issuer=object(),
        )

    other = _run_with_experiment_key(
        run,
        "experiment:rag-value:phase2-synthetic-v2",
    )
    mismatched = _trust(other)
    with pytest.raises(ReportingError, match="runtime authority"):
        write_benchmark_outputs(
            tmp_path / "mismatch",
            run,
            mismatched,
            allow_test_output=True,
        )

    copied = replace(_trust(run))
    with pytest.raises(ReportingError, match="runtime authority"):
        write_benchmark_outputs(
            tmp_path / "copied-authority",
            run,
            copied,
            allow_test_output=True,
        )


def test_writer_revalidates_model_copy_tampering_before_creating_files(
    tmp_path: Path,
) -> None:
    run = _run()
    first = run.results[0]
    assert first.answer is not None
    tampered_answer = first.answer.model_copy(update={"answer_text": "Tampered."})
    tampered_result = first.model_copy(update={"answer": tampered_answer})
    tampered_run = run.model_copy(
        update={"results": (tampered_result, *run.results[1:])}
    )
    output = tmp_path / "tampered"

    with pytest.raises(ReportingError, match="checksum revalidation"):
        write_benchmark_outputs(
            output,
            tampered_run,
            _trust(run),
            allow_test_output=True,
        )
    assert not output.exists()


def test_run_rejects_question_checksum_drift_between_systems() -> None:
    run = _run()
    target = run.results[1]
    payload = target.model_dump(mode="python")
    payload.pop("result_sha256")
    payload["question_text_sha256"] = "0" * 64
    altered = build_per_question_evaluation(**payload)
    results = tuple(altered if result is target else result for result in run.results)

    with pytest.raises(ValueError, match="wording checksum differs"):
        build_benchmark_run(
            manifest=run.manifest,
            human_review_status=run.human_review_status,
            results=results,
            comparison_eligible_question_ids=run.comparison_eligible_question_ids,
            comparison_inputs=run.comparison_inputs,
        )


def test_generated_refusal_requires_persisted_answer_and_validation() -> None:
    run = _run()
    target = run.results[0]
    system = run.manifest.systems[0]
    refused = build_per_question_evaluation(
        system_key="S0",
        question_id=target.question_id,
        family=target.family,
        trust_status="test_only",
        status="refused",
        question_text_sha256=target.question_text_sha256,
        evidence_pack_sha256=target.evidence_pack_sha256,
        execution_trace=ExecutionTrace(
            system_key="S0",
            question_id=target.question_id,
            status="refused",
            constructed_dependencies=system.allowed_dependencies,
            called_stages=system.required_success_stages,
            refusal_stage="mechanical_validation",
            generation_call_count=1,
        ),
        refusal_observation=RefusalObservation(
            question_id=target.question_id,
            expected_refusal=True,
            abstained=True,
            refusal_origin="model_abstention",
            refusal_appropriate=True,
            unsafe_acceptance=False,
            downstream_call_count_after_refusal=0,
        ),
    )
    results = tuple(refused if result is target else result for result in run.results)

    with pytest.raises(ValueError, match="generated result lacks"):
        build_benchmark_run(
            manifest=run.manifest,
            human_review_status=run.human_review_status,
            results=results,
            comparison_eligible_question_ids=run.comparison_eligible_question_ids,
            comparison_inputs=run.comparison_inputs,
        )


def test_s5_result_requires_a_byte_identical_structured_preservation_proof() -> None:
    run = _run()
    s5 = next(result for result in run.results if result.system_key == "S5")
    altered_payload = s5.model_dump(mode="python")
    del altered_payload["result_sha256"]
    altered_payload["structured_preservation"] = None
    altered = build_per_question_evaluation(**altered_payload)
    results = tuple(altered if result is s5 else result for result in run.results)

    with pytest.raises(ValueError, match="preserve the structured result"):
        build_benchmark_run(
            manifest=run.manifest,
            human_review_status=run.human_review_status,
            results=results,
            comparison_eligible_question_ids=run.comparison_eligible_question_ids,
            comparison_inputs=run.comparison_inputs,
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


def test_synthetic_fixture_manifest_is_required_only_for_phase2() -> None:
    manifest = _run().manifest
    phase2_payload = manifest.model_dump(mode="python")
    del phase2_payload["manifest_sha256"]
    phase2_payload["synthetic_fixture_manifest_sha256"] = None
    with pytest.raises(ValidationError, match="requires a fixture manifest"):
        build_experiment_manifest(**phase2_payload)

    phase3_payload = manifest.model_dump(mode="python")
    del phase3_payload["manifest_sha256"]
    phase3_payload.update(
        phase="phase3_retrieval",
        generation_identity=None,
        systems=build_system_definitions(None),
    )
    with pytest.raises(ValidationError, match="belongs only to Phase 2"):
        build_experiment_manifest(**phase3_payload)


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
                deterministic_output_text=(
                    "Synthetic structured output."
                    if system.system_key == "S4"
                    else None
                ),
                deterministic_output_sha256=(
                    hashlib.sha256(b"Synthetic structured output.").hexdigest()
                    if system.system_key == "S4"
                    else None
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
    return execute_synthetic_harness().run


def _run_with_experiment_key(run, experiment_key: str):
    manifest_payload = run.manifest.model_dump(mode="python")
    manifest_payload.pop("manifest_sha256")
    manifest_payload["experiment_key"] = experiment_key
    manifest = build_experiment_manifest(**manifest_payload)
    return build_benchmark_run(
        manifest=manifest,
        human_review_status=run.human_review_status,
        results=run.results,
        comparison_eligible_question_ids=run.comparison_eligible_question_ids,
        comparison_inputs=run.comparison_inputs,
        failures=run.failures,
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _csv_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(path.read_text(encoding="utf-8"))))


def _trust(run):
    return issue_phase2_synthetic_trust(
        run=run,
        fixture_manifest=build_synthetic_fixture_manifest(),
    )


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
