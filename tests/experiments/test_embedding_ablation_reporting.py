from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    verify_model_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    AnnotationManifest,
    AnnotationQuestion,
    ArtifactFileRecord,
    EvidenceGroup,
    HardwareRecord,
    ModelArtifactManifest,
    ModelRepresentationContract,
    build_annotation_manifest,
)
from eve_relation_rag.experiments.embedding_ablation.metrics import compute_question_metrics
from eve_relation_rag.experiments.embedding_ablation.reporting import (
    DeterministicReportError,
    generate_markdown_report_bytes,
    load_experiment_run,
    write_experiment_outputs,
)
from eve_relation_rag.experiments.embedding_ablation.results import (
    ExperimentRun,
    LatencySamples,
    QuestionExecutionResult,
    ResourceUsage,
    TruncationCounts,
    build_experiment_manifest,
    build_system_execution_result,
)
from eve_relation_rag.experiments.embedding_ablation.source_guard import (
    ProductionSourceFingerprint,
)
from eve_relation_rag.experiments.embedding_ablation.trust import (
    RunTrustDecision,
    collect_provider_evidence,
    evaluate_run_trust,
)
from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY, EMBEDDING_REVISION
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
CHUNK = f"chunk:sha256:{'a' * 64}"


def test_fake_provider_cannot_create_trusted_report_but_can_emit_explicit_test_output(
    tmp_path: Path,
) -> None:
    fake = DeterministicFakeEmbeddingProvider()
    annotation = _annotation()
    decision = evaluate_run_trust(
        annotation_manifest=annotation,
        providers=(collect_provider_evidence(fake, component="embedding"),),
        corpus_unchanged=True,
        production_sources_unchanged=True,
        failure_count=0,
    )
    run = _run(decision, annotation, model_key=fake.model_key, artifact_sha="f" * 64)

    with pytest.raises(DeterministicReportError, match="fake/test"):
        write_experiment_outputs(tmp_path / "refused", run, decision)

    first = tmp_path / "first"
    second = tmp_path / "second"
    write_experiment_outputs(first, run, decision, allow_test_output=True)
    write_experiment_outputs(second, run, decision, allow_test_output=True)

    assert load_experiment_run(first) == run
    assert _tree_bytes(first) == _tree_bytes(second)
    question_file = hashlib.sha256(b"q-1").hexdigest()
    assert set(_tree_bytes(first)) == {
        "experiment_manifest.json",
        "failures.jsonl",
        "latency.csv",
        f"per_question/system_a/{question_file}.json",
        "rank_shift_after_reranking.csv",
        "resource_comparison.csv",
        "resource_usage.csv",
        "retrieval_by_category.csv",
        "retrieval_quality.csv",
        "summary.csv",
        "summary.json",
        "systems/system_a.json",
        "latency_comparison.csv",
    }
    with pytest.raises(DeterministicReportError, match="trusted"):
        generate_markdown_report_bytes(first)


def test_trusted_markdown_is_generated_only_from_revalidated_machine_results(
    tmp_path: Path,
) -> None:
    provider = object.__new__(LocalBgeProvider)
    provider._artifact_manifest_sha256 = "a" * 64

    annotation = _annotation()
    decision = evaluate_run_trust(
        annotation_manifest=annotation,
        providers=(
            collect_provider_evidence(provider, component="embedding"),
        ),
        corpus_unchanged=True,
        production_sources_unchanged=True,
        failure_count=0,
    )
    run = _run(
        decision,
        annotation,
        model_key=EMBEDDING_MODEL_KEY,
        artifact_sha="a" * 64,
        embedding_dimension=384,
        source_tree_clean=True,
    )
    output = tmp_path / "trusted-output"
    report = tmp_path / "embedding_reranker_ablation.md"

    write_experiment_outputs(output, run, decision, markdown_report_path=report)

    identity = run.manifest.providers[0].model_identity
    assert identity is not None
    assert identity.exact_revision == EMBEDDING_REVISION
    assert identity.license == "MIT"
    assert identity.representation.dimension == 384
    assert identity.representation.pooling == "cls"
    assert identity.representation.normalization == "l2"
    assert report.read_bytes() == generate_markdown_report_bytes(output)
    assert b"Recall@5" in report.read_bytes()
    summary = output / "summary.json"
    summary.write_bytes(summary.read_bytes().replace(b"1.000000000000", b"0.000000000000", 1))
    with pytest.raises(DeterministicReportError, match="canonical"):
        generate_markdown_report_bytes(output)


def test_structural_provider_is_not_trusted_even_with_a_verified_artifact(
    tmp_path: Path,
) -> None:
    artifact = _verified_artifact(tmp_path / "artifact")

    class StructuralProvider:
        model_key = artifact.manifest.model_key
        artifact_manifest_sha256 = artifact.artifact_manifest_sha256

    annotation = _annotation()
    evidence = collect_provider_evidence(
        StructuralProvider(),
        component="embedding",
        verified_artifact=artifact,
    )
    decision = evaluate_run_trust(
        annotation_manifest=annotation,
        providers=(evidence,),
        corpus_unchanged=True,
        production_sources_unchanged=True,
        failure_count=0,
    )

    assert evidence.model_identity is not None
    assert evidence.provider_kind == "unverified"
    assert decision.status == "failed"


def _run(
    decision: RunTrustDecision,
    annotation: AnnotationManifest,
    *,
    model_key: str,
    artifact_sha: str,
    embedding_dimension: int = 2,
    source_tree_clean: bool = False,
) -> ExperimentRun:
    system = AblationSystem(
        system_key="system_a",
        embedding_model_key=model_key,
        embedding_artifact_manifest_sha256=artifact_sha,
        embedding_dimension=embedding_dimension,
    )
    question = annotation.approved_questions[0]
    metrics = compute_question_metrics(question, (CHUNK,))
    question_result = QuestionExecutionResult(
        system_key=system.system_key,
        question_id=question.question_id,
        category=metrics.category,
        query_sha256="b" * 64,
        pre_rerank_chunk_keys=(CHUNK,),
        ranked_candidate_chunk_keys=(CHUNK,),
        returned_chunk_keys=(CHUNK,),
        metrics=metrics,
        latency=LatencySamples(
            embedding_ns=(10, 20),
            retrieval_ns=(30, 40),
            reranking_ns=None,
            end_to_end_ns=(40, 60),
        ),
        truncation=TruncationCounts(
            embedding_query_count=0,
            embedding_query_tokens=0,
            reranker_query_count=0,
            reranker_query_tokens=0,
            reranker_passage_count=0,
            reranker_passage_tokens=0,
        ),
    )
    system_result = build_system_execution_result(
        system=system,
        question_results=(question_result,),
        resources=ResourceUsage(
            peak_process_rss_bytes=100,
            peak_accelerator_memory_bytes=None,
            embedding_model_size_bytes=200,
            reranker_model_size_bytes=None,
            index_size_bytes=300,
            passage_embedding_truncation_count=0,
            passage_embedding_truncated_tokens=0,
        ),
    )
    source = ProductionSourceFingerprint(
        source_guard_schema_version="production-source-guard-v1",
        file_sha256={"protected.py": "c" * 64},
        fingerprint_sha256=canonical_json_sha256({"protected.py": "c" * 64}),
    )
    manifest = build_experiment_manifest(
        experiment_key="experiment:test:embedding-ablation",
        source_commit="d" * 40,
        source_tree_clean=source_tree_clean,
        production_source_fingerprint=source,
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="e" * 64,
        corpus_fingerprint_sha256="f" * 64,
        annotation_manifest_sha256=annotation.annotation_manifest_sha256,
        gold_sha256=annotation.gold_sha256,
        approved_question_count=1,
        hardware_record=_hardware(),
        warmup_count=1,
        measured_iteration_count=2,
        systems=(system,),
        trust_decision=decision,
    )
    return ExperimentRun(manifest=manifest, system_results=(system_result,), failures=())


def _annotation() -> AnnotationManifest:
    question = AnnotationQuestion(
        question_id="q-1",
        question="Which chunk is relevant?",
        category="evidence",
        required_chunk_keys=(CHUNK,),
        evidence_groups=(EvidenceGroup(group_id="e1", required_chunk_key=CHUNK),),
        review_status="approved",
        reviewer_id="expert-1",
        reviewed_at="2099-01-01T00:00:00Z",
    )
    return build_annotation_manifest(
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="e" * 64,
        questions=(question,),
    )


def _hardware() -> HardwareRecord:
    return HardwareRecord(
        hardware_schema_version="embedding-ablation-hardware-v1",
        cpu_model="fixture CPU",
        physical_core_count=4,
        logical_core_count=8,
        ram_bytes=16_000_000_000,
        operating_system="fixture OS",
        kernel_release="fixture kernel",
        machine_architecture="fixture arch",
        accelerator="none",
        accelerator_runtime="none",
        numerical_backend="fixture backend",
        python_version="3.12",
        uv_lock_sha256="1" * 64,
        postgresql_version="PostgreSQL fixture",
        pgvector_version="0.8.6",
        thread_settings={"OMP_NUM_THREADS": "1"},
    )


def _verified_artifact(tmp_path: Path) -> VerifiedModelArtifact:
    tmp_path.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    config = model / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    representation = _representation()
    manifest = ModelArtifactManifest(
        manifest_schema_version="embedding-ablation-model-artifact-v1",
        model_key="embedding:test:verified",
        model_id="example/verified",
        exact_revision="a" * 40,
        license="MIT",
        license_review_status="approved",
        representation=representation,
        runtime_key="runtime:test",
        local_files_only=True,
        trust_remote_code=False,
        files=(
            ArtifactFileRecord(
                relative_path="config.json",
                byte_size=config.stat().st_size,
                sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
            ),
        ),
    )
    raw = canonical_json_bytes(manifest)
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    return verify_model_artifact(model, path, hashlib.sha256(raw).hexdigest())


def _representation() -> ModelRepresentationContract:
    return ModelRepresentationContract(
        task_kind="embedding",
        dimension=2,
        pooling="cls",
        normalization="l2",
        similarity="cosine",
        query_format="{query}",
        passage_format="{passage}",
        max_sequence_length=8,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
