#!/usr/bin/env python3
"""Run the approved 13-question MedCPT/Qwen3 comparison as preliminary evidence."""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from eve_relation_rag.config.settings import Settings
from eve_relation_rag.experiments.embedding_ablation.annotations import load_legacy_benchmark
from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    verify_model_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.baseline import (
    baseline_bge_representation_contract,
    baseline_system,
)
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    ModelRepresentationContract,
    anchor_keys,
)
from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    assert_corpus_unchanged,
    read_published_corpus_snapshot,
)
from eve_relation_rag.experiments.embedding_ablation.indexing import (
    build_exact_sidecar_index,
)
from eve_relation_rag.experiments.embedding_ablation.metrics import (
    rank_shift,
    summarize_latency,
)
from eve_relation_rag.experiments.embedding_ablation.model_adapters import (
    MEDCPT_ARTICLE_MODEL_ID,
    MEDCPT_CROSS_ENCODER_MODEL_ID,
    MEDCPT_QUERY_MODEL_ID,
    QWEN3_EMBEDDING_MODEL_ID,
    QWEN3_RERANKER_MODEL_ID,
    MedCptArticleEmbeddingProvider,
    MedCptCrossEncoderProvider,
    MedCptQueryEmbeddingProvider,
    Qwen3EmbeddingProvider,
    Qwen3RerankerProvider,
    serialize_medcpt_article_passage,
)
from eve_relation_rag.experiments.embedding_ablation.offline import offline_model_call
from eve_relation_rag.experiments.embedding_ablation.preliminary import (
    compute_legacy_question_metrics,
    summarize_legacy_quality,
)
from eve_relation_rag.experiments.embedding_ablation.providers import (
    EmbeddingTelemetryProvider,
    RerankerProvider,
    validate_embedding_vector,
)
from eve_relation_rag.experiments.embedding_ablation.reranking import rerank_candidates
from eve_relation_rag.experiments.embedding_ablation.retrieval import (
    AblationRetriever,
    PostgresFtsCandidateProvider,
)
from eve_relation_rag.experiments.embedding_ablation.sidecar import (
    load_sidecar_index,
    sidecar_size_bytes,
    write_sidecar_index,
)
from eve_relation_rag.experiments.embedding_ablation.source_guard import (
    ProductionSourceFingerprint,
    assert_production_sources_unchanged,
    capture_production_source_fingerprint,
)
from eve_relation_rag.experiments.embedding_ablation.systems import (
    build_bge_medcpt_reranker_system,
    build_medcpt_retrieval_system,
    build_qwen3_retrieval_system,
    medcpt_encoder_bundle_manifest_sha256,
)
from eve_relation_rag.experiments.embedding_ablation.telemetry import (
    collect_hardware_record,
    peak_process_rss_bytes,
)
from eve_relation_rag.literature.benchmarking import BenchmarkDefinition, BenchmarkQuestion
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.providers import EmbeddingProvider

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = REPOSITORY_ROOT / (
    ".artifacts/v0_activation/candidates/corpus-validation-20260829T071330Z/"
    "v0_literature_pilot_benchmark.json"
)
DEFAULT_BENCHMARK_SHA256 = (
    "2f8dd91d407bf043e421f614ae1131cf845abcd45f208ac36f05a21791318a90"
)
DEFAULT_ANNOTATIONS = (
    REPOSITORY_ROOT
    / ".artifacts/embedding_ablation/annotations/eve_13_questions_pending.json"
)
MODEL_ROOT = REPOSITORY_ROOT / ".artifacts/embedding_ablation/models"
MODEL_MANIFEST_ROOT = REPOSITORY_ROOT / ".artifacts/embedding_ablation/model_manifests"
MODEL_APPROVAL = MODEL_MANIFEST_ROOT / "approved_model_artifacts.json"
BGE_MODEL_DIRECTORY = (
    REPOSITORY_ROOT / ".artifacts/milestone3/model/BAAI-bge-small-en-v1.5"
)
BGE_ARTIFACT_MANIFEST = (
    REPOSITORY_ROOT / ".artifacts/milestone3/model/bge-small-en-v1.5-artifact-manifest.json"
)
BGE_ARTIFACT_MANIFEST_SHA256 = (
    "0dc66d301fc8305bae93aa197200a176a61be13a302c3fee430cd2efc744241a"
)
SYSTEM_LABELS = {
    "bge_small__fts_dense_summary__rrf60": "A · BGE-small baseline",
    "bge_small__rrf60__medcpt_ce__d20": "B · BGE + MedCPT CE (top 20)",
    "medcpt_biencoder_768d__fts_dense_summary__rrf60": "C · MedCPT retrieval (768d)",
    "medcpt_biencoder_768d__fts_dense_summary__rrf60__medcpt_ce__d20": (
        "C+ · MedCPT retrieval + MedCPT CE (top 20)"
    ),
    "qwen3_embedding_0_6b__fts_dense_summary__rrf60": (
        "D · Qwen3 embedding (384d)"
    ),
    "qwen3_embedding_0_6b__fts_dense_summary__rrf60__qwen3_reranker__d20": (
        "D+ · Qwen3 embedding + Qwen3 reranker (top 20)"
    ),
}
SYSTEM_ORDER = tuple(SYSTEM_LABELS)
FAMILY_BY_SYSTEM = {
    SYSTEM_ORDER[0]: "bge",
    SYSTEM_ORDER[1]: "bge",
    SYSTEM_ORDER[2]: "medcpt",
    SYSTEM_ORDER[3]: "medcpt",
    SYSTEM_ORDER[4]: "qwen3",
    SYSTEM_ORDER[5]: "qwen3",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite {path}")
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return value


def _engine() -> Engine:
    return create_engine(Settings().database_url, pool_pre_ping=True)


def _load_benchmark() -> BenchmarkDefinition:
    return load_legacy_benchmark(DEFAULT_BENCHMARK, DEFAULT_BENCHMARK_SHA256)


def _load_verified_artifacts() -> dict[str, VerifiedModelArtifact]:
    approval = _read_json(MODEL_APPROVAL)
    rows = approval.get("models")
    if not isinstance(rows, list):
        raise RuntimeError("model approval manifest has no model records")
    artifacts: dict[str, VerifiedModelArtifact] = {}
    for untyped_row in rows:
        if not isinstance(untyped_row, dict):
            raise RuntimeError("model approval row is invalid")
        row: dict[str, Any] = untyped_row
        model_id = str(row["model_id"])
        artifacts[model_id] = verify_model_artifact(
            MODEL_ROOT.parent / str(row["model_directory"]),
            MODEL_MANIFEST_ROOT / str(row["manifest_file"]),
            str(row["artifact_manifest_sha256"]),
            expected_model_id=model_id,
            expected_revision=str(row["exact_revision"]),
        )
    expected = {
        MEDCPT_QUERY_MODEL_ID,
        MEDCPT_ARTICLE_MODEL_ID,
        MEDCPT_CROSS_ENCODER_MODEL_ID,
        QWEN3_EMBEDDING_MODEL_ID,
        QWEN3_RERANKER_MODEL_ID,
    }
    if set(artifacts) != expected:
        raise RuntimeError("verified ablation artifact set is incomplete")
    return artifacts


def _build_systems(
    artifacts: dict[str, VerifiedModelArtifact],
) -> dict[str, AblationSystem]:
    query = artifacts[MEDCPT_QUERY_MODEL_ID]
    article = artifacts[MEDCPT_ARTICLE_MODEL_ID]
    medcpt_reranker = artifacts[MEDCPT_CROSS_ENCODER_MODEL_ID]
    qwen_embedding = artifacts[QWEN3_EMBEDDING_MODEL_ID]
    qwen_reranker = artifacts[QWEN3_RERANKER_MODEL_ID]
    bundle = medcpt_encoder_bundle_manifest_sha256(query, article)
    systems = (
        baseline_system(BGE_ARTIFACT_MANIFEST_SHA256),
        build_bge_medcpt_reranker_system(
            bge_artifact_manifest_sha256=BGE_ARTIFACT_MANIFEST_SHA256,
            reranker=medcpt_reranker,
            expected_reranker_model_id=MEDCPT_CROSS_ENCODER_MODEL_ID,
            candidate_depth=20,
            reranker_batch_size=4,
        ),
        build_medcpt_retrieval_system(
            query_encoder=query,
            article_encoder=article,
            expected_query_model_id=MEDCPT_QUERY_MODEL_ID,
            expected_article_model_id=MEDCPT_ARTICLE_MODEL_ID,
            encoder_bundle_manifest_sha256=bundle,
        ),
        build_medcpt_retrieval_system(
            query_encoder=query,
            article_encoder=article,
            expected_query_model_id=MEDCPT_QUERY_MODEL_ID,
            expected_article_model_id=MEDCPT_ARTICLE_MODEL_ID,
            encoder_bundle_manifest_sha256=bundle,
            reranker=medcpt_reranker,
            expected_reranker_model_id=MEDCPT_CROSS_ENCODER_MODEL_ID,
            candidate_depth=20,
            reranker_batch_size=4,
        ),
        build_qwen3_retrieval_system(
            embedding=qwen_embedding,
            expected_embedding_model_id=QWEN3_EMBEDDING_MODEL_ID,
        ),
        build_qwen3_retrieval_system(
            embedding=qwen_embedding,
            expected_embedding_model_id=QWEN3_EMBEDDING_MODEL_ID,
            reranker=qwen_reranker,
            expected_reranker_model_id=QWEN3_RERANKER_MODEL_ID,
            candidate_depth=20,
            reranker_batch_size=1,
        ),
    )
    by_key = {system.system_key: system for system in systems}
    if tuple(by_key) != SYSTEM_ORDER:
        raise RuntimeError("constructed system identities do not match the run protocol")
    return by_key


def _initialize_context(output_directory: Path) -> dict[str, Any]:
    benchmark = _load_benchmark()
    artifacts = _load_verified_artifacts()
    systems = _build_systems(artifacts)
    engine = _engine()
    published = read_published_corpus_snapshot(engine, benchmark.corpus_release_key)
    snapshot = published.snapshot
    if (
        snapshot.corpus_manifest_sha256 != benchmark.corpus_manifest_sha256
        or snapshot.document_count != 11
        or snapshot.chunk_count != 1464
    ):
        raise RuntimeError("published corpus no longer matches the approved preliminary input")
    known_chunks = frozenset(snapshot.chunk_keys)
    known_anchors = frozenset(anchor.anchor_key for anchor in snapshot.anchors)
    for question in benchmark.questions:
        if not set(question.relevant_chunk_keys) <= known_chunks:
            raise RuntimeError("legacy gold contains a chunk outside the published corpus")
        if not set(anchor_keys(question.anchors)) <= known_anchors:
            raise RuntimeError("legacy question contains an unresolved anchor")
    hardware = collect_hardware_record(
        engine,
        uv_lock_path=REPOSITORY_ROOT / "uv.lock",
        accelerator="none; CPU-only run (torch MPS unavailable)",
        accelerator_runtime="not_applicable",
        numerical_backend="PyTorch 2.13.0 CPU eager",
    )
    source_guard = capture_production_source_fingerprint(REPOSITORY_ROOT)
    source_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_tree_clean = not subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pending_annotation = _read_json(DEFAULT_ANNOTATIONS)
    if pending_annotation.get("approved_question_count") != 0:
        raise RuntimeError("preliminary run expected the 13-question annotation set to be pending")
    model_records = [
        {
            "artifact_manifest_sha256": artifact.artifact_manifest_sha256,
            "exact_revision": artifact.manifest.exact_revision,
            "license": artifact.manifest.license,
            "model_id": artifact.manifest.model_id,
            "model_key": artifact.manifest.model_key,
            "model_size_bytes": artifact.model_size_bytes,
            "representation": artifact.manifest.representation.model_dump(mode="json"),
            "runtime_key": artifact.manifest.runtime_key,
        }
        for artifact in artifacts.values()
    ]
    bge_manifest = _read_json(BGE_ARTIFACT_MANIFEST)
    model_records.insert(
        0,
        {
            "artifact_manifest_sha256": BGE_ARTIFACT_MANIFEST_SHA256,
            "exact_revision": bge_manifest["revision"],
            "license": bge_manifest["license_key"],
            "model_id": bge_manifest["repository_id"],
            "model_key": bge_manifest["model_key"],
            "model_size_bytes": sum(int(row["byte_size"]) for row in bge_manifest["files"]),
            "representation": baseline_bge_representation_contract().model_dump(mode="json"),
            "runtime_key": "runtime:sentence-transformers-6.0.0:torch-2.13.0:cpu-v1",
        },
    )
    payload: dict[str, Any] = {
        "run_context_schema_version": "embedding-ablation-preliminary-context-v1",
        "created_at": _now(),
        "result_status": "preliminary_unreviewed_legacy_gold",
        "formal_benchmark_eligible": False,
        "trust_reasons": [
            "the migrated 13-question annotation manifest has zero approved questions",
            "legacy gold is used only to obtain preliminary comparative evidence",
            "the source tree contains the uncommitted experiment implementation",
        ],
        "source_commit": source_commit,
        "source_tree_clean": source_tree_clean,
        "production_source_fingerprint": source_guard.model_dump(mode="json"),
        "corpus": {
            "corpus_release_key": snapshot.corpus_release_key,
            "corpus_manifest_sha256": snapshot.corpus_manifest_sha256,
            "corpus_fingerprint_sha256": snapshot.corpus_fingerprint_sha256,
            "document_count": snapshot.document_count,
            "chunk_count": snapshot.chunk_count,
            "anchor_count": snapshot.anchor_count,
            "summary_chunk_count": len(snapshot.summary_chunk_keys),
        },
        "gold": {
            "legacy_benchmark_file_sha256": _file_sha256(DEFAULT_BENCHMARK),
            "legacy_benchmark_manifest_sha256": benchmark.benchmark_manifest_sha256,
            "legacy_gold_sha256": benchmark.gold_sha256,
            "question_count": benchmark.question_count,
            "pending_annotation_file_sha256": _file_sha256(DEFAULT_ANNOTATIONS),
            "pending_annotation_manifest_sha256": pending_annotation[
                "annotation_manifest_sha256"
            ],
            "approved_question_count": 0,
            "category_analysis_available": False,
        },
        "hardware": hardware.model_dump(mode="json"),
        "models": model_records,
        "systems": [systems[key].model_dump(mode="json") for key in SYSTEM_ORDER],
        "warmup_count": 1,
        "measured_iteration_count": 1,
        "offline_model_runtime_enforced": True,
        "model_loading_latency_included": False,
    }
    context = {**payload, "run_context_sha256": canonical_json_sha256(payload)}
    _write_json(output_directory / ".run_context.json", context)
    return context


def _validate_context(output_directory: Path) -> dict[str, Any]:
    context = _read_json(output_directory / ".run_context.json")
    observed = context.pop("run_context_sha256", None)
    expected = canonical_json_sha256(context)
    context["run_context_sha256"] = observed
    if observed != expected:
        raise RuntimeError("preliminary run context checksum is invalid")
    return context


def _prepare_index(
    family: str,
    output_directory: Path,
    sidecar_root: Path,
) -> None:
    context = _validate_context(output_directory)
    benchmark = _load_benchmark()
    artifacts = _load_verified_artifacts()
    systems = _build_systems(artifacts)
    engine = _engine()
    published = read_published_corpus_snapshot(engine, benchmark.corpus_release_key)
    snapshot = published.snapshot
    if snapshot.corpus_fingerprint_sha256 != context["corpus"]["corpus_fingerprint_sha256"]:
        raise RuntimeError("published corpus changed before sidecar construction")
    sidecar_root.mkdir(parents=True, exist_ok=True)
    sidecar_directory = sidecar_root / family
    telemetry_path = sidecar_root / f"{family}.build.json"

    serializer: Any = None
    if family == "bge":
        system = systems[SYSTEM_ORDER[0]]
        representation = baseline_bge_representation_contract()
        provider: EmbeddingProvider = LocalBgeProvider(
            BGE_MODEL_DIRECTORY,
            artifact_manifest_path=BGE_ARTIFACT_MANIFEST,
            approved_artifact_manifest_sha256=BGE_ARTIFACT_MANIFEST_SHA256,
        )
        batch_size = 16
    elif family == "medcpt":
        system = systems[SYSTEM_ORDER[2]]
        artifact = artifacts[MEDCPT_ARTICLE_MODEL_ID]
        representation = artifact.manifest.representation
        provider = MedCptArticleEmbeddingProvider(artifact)
        title_by_document = {
            document.document_key: document.title for document in snapshot.documents
        }
        serializer = lambda chunk: serialize_medcpt_article_passage(  # noqa: E731
            title_by_document[chunk.document_key], chunk.text
        )
        batch_size = 8
    elif family == "qwen3":
        system = systems[SYSTEM_ORDER[4]]
        artifact = artifacts[QWEN3_EMBEDDING_MODEL_ID]
        representation = artifact.manifest.representation
        provider = Qwen3EmbeddingProvider(artifact)
        batch_size = 2
    else:
        raise RuntimeError("unknown sidecar family")

    if sidecar_directory.exists() or telemetry_path.exists():
        if not sidecar_directory.is_dir() or not telemetry_path.is_file():
            raise RuntimeError("sidecar and build telemetry must either both exist or be absent")
        index, manifest = load_sidecar_index(
            sidecar_directory,
            expected_model_key=system.embedding_model_key,
            expected_artifact_manifest_sha256=system.embedding_artifact_manifest_sha256,
            expected_dimension=system.embedding_dimension,
        )
        telemetry = _read_json(telemetry_path)
        if (
            index.chunk_keys != snapshot.chunk_keys
            or telemetry.get("corpus_fingerprint_sha256")
            != snapshot.corpus_fingerprint_sha256
            or telemetry.get("sidecar_manifest_sha256")
            != manifest.sidecar_manifest_sha256
        ):
            raise RuntimeError("existing sidecar is not reusable for this frozen corpus")
        print(f"reused sidecar {family}: {manifest.row_count} rows", flush=True)
        return

    print(f"building sidecar {family}: {snapshot.chunk_count} frozen chunks", flush=True)
    started = _now()
    result = build_exact_sidecar_index(
        snapshot,
        provider,
        representation,
        batch_size=batch_size,
        passage_serializer=serializer,
    )
    manifest = write_sidecar_index(sidecar_directory, result.index)
    record_payload: dict[str, Any] = {
        "build_schema_version": "embedding-ablation-sidecar-build-v1",
        "family": family,
        "started_at": started,
        "completed_at": _now(),
        "corpus_fingerprint_sha256": snapshot.corpus_fingerprint_sha256,
        "model_key": provider.model_key,
        "artifact_manifest_sha256": provider.artifact_manifest_sha256,
        "representation": representation.model_dump(mode="json"),
        "sidecar_manifest_sha256": manifest.sidecar_manifest_sha256,
        "sidecar_size_bytes": sidecar_size_bytes(sidecar_directory),
        "telemetry": result.telemetry.model_dump(mode="json"),
        "peak_build_process_rss_bytes": peak_process_rss_bytes(),
        "offline_model_runtime_enforced": True,
    }
    _write_json(
        telemetry_path,
        {**record_payload, "build_record_sha256": canonical_json_sha256(record_payload)},
    )
    print(
        f"built sidecar {family}: {manifest.row_count} rows, "
        f"{sidecar_size_bytes(sidecar_directory)} bytes",
        flush=True,
    )


def _query_provider(
    family: str,
    artifacts: dict[str, VerifiedModelArtifact],
) -> tuple[EmbeddingProvider, ModelRepresentationContract]:
    if family == "bge":
        return (
            LocalBgeProvider(
                BGE_MODEL_DIRECTORY,
                artifact_manifest_path=BGE_ARTIFACT_MANIFEST,
                approved_artifact_manifest_sha256=BGE_ARTIFACT_MANIFEST_SHA256,
            ),
            baseline_bge_representation_contract(),
        )
    if family == "medcpt":
        artifact = artifacts[MEDCPT_QUERY_MODEL_ID]
        return MedCptQueryEmbeddingProvider(artifact), artifact.manifest.representation
    if family == "qwen3":
        artifact = artifacts[QWEN3_EMBEDDING_MODEL_ID]
        return Qwen3EmbeddingProvider(artifact), artifact.manifest.representation
    raise RuntimeError("unknown query provider family")


def _reranker_provider(
    system: AblationSystem,
    artifacts: dict[str, VerifiedModelArtifact],
) -> RerankerProvider | None:
    if system.reranker_model_key is None:
        return None
    if "qwen3_reranker" in system.system_key:
        return Qwen3RerankerProvider(artifacts[QWEN3_RERANKER_MODEL_ID])
    return MedCptCrossEncoderProvider(artifacts[MEDCPT_CROSS_ENCODER_MODEL_ID])


def _execute_question(
    *,
    system: AblationSystem,
    question: BenchmarkQuestion,
    embedding_provider: EmbeddingProvider,
    embedding_representation: ModelRepresentationContract,
    retriever: AblationRetriever,
    reranker_provider: RerankerProvider | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total_started = time.perf_counter_ns()
    embedding_started = time.perf_counter_ns()
    with offline_model_call():
        query_vector = validate_embedding_vector(
            embedding_provider.embed_query(question.question),
            representation=embedding_representation,
        )
    embedding_ended = time.perf_counter_ns()
    embedding_truncated_count = 0
    embedding_truncated_tokens = 0
    if embedding_representation.truncation_policy != "reject":
        if not isinstance(embedding_provider, EmbeddingTelemetryProvider):
            raise RuntimeError("truncating query provider has no telemetry")
        embedding_telemetry = embedding_provider.consume_last_query_telemetry()
        embedding_truncated_count = embedding_telemetry.truncated_query_count
        embedding_truncated_tokens = embedding_telemetry.truncated_query_tokens

    retrieval_started = time.perf_counter_ns()
    retrieval = retriever.retrieve(
        question=question.question,
        query_vector=query_vector,
        anchor_keys=anchor_keys(question.anchors),
    )
    retrieval_ended = time.perf_counter_ns()
    pre_keys = tuple(item.candidate.chunk_key for item in retrieval.candidates)
    reranking_latency_ns: int | None = None
    reranker_query_count = 0
    reranker_query_tokens = 0
    reranker_passage_count = 0
    reranker_passage_tokens = 0
    rank_rows: list[dict[str, Any]] = []
    if reranker_provider is not None:
        if system.reranker_batch_size is None:
            raise RuntimeError("reranker system has no batch size")
        reranked = rerank_candidates(
            reranker_provider,
            query=question.question,
            candidates=retrieval.candidates,
            batch_size=system.reranker_batch_size,
        )
        ranked_keys = tuple(item.chunk_key for item in reranked.ranked_candidates)
        returned_keys = ranked_keys[: system.top_k]
        reranking_latency_ns = reranked.telemetry.total_latency_ns
        reranker_query_count = reranked.telemetry.truncated_query_count
        reranker_query_tokens = reranked.telemetry.truncated_query_tokens
        reranker_passage_count = reranked.telemetry.truncated_passage_count
        reranker_passage_tokens = reranked.telemetry.truncated_passage_tokens
        shifts = rank_shift(pre_keys, ranked_keys)
        score_by_key = dict(
            zip(reranked.input_chunk_keys, reranked.positional_scores, strict=True)
        )
        post_rank = {key: rank for rank, key in enumerate(ranked_keys, start=1)}
        relevant = frozenset(question.relevant_chunk_keys)
        rank_rows = [
            {
                "system_key": system.system_key,
                "question_key": question.question_key,
                "chunk_key": key,
                "pre_rerank_rank": rank,
                "post_rerank_rank": post_rank[key],
                "rank_shift": shifts[key],
                "reranker_score": score_by_key[key],
                "is_legacy_relevant": key in relevant,
            }
            for rank, key in enumerate(pre_keys, start=1)
        ]
    else:
        ranked_keys = pre_keys
        returned_keys = ranked_keys[: system.top_k]
    total_ended = time.perf_counter_ns()
    metrics = compute_legacy_question_metrics(question, returned_keys)
    return (
        {
            "system_key": system.system_key,
            "question_key": question.question_key,
            "query_sha256": canonical_json_sha256(
                {
                    "system_key": system.system_key,
                    "question_key": question.question_key,
                    "question": question.question,
                    "anchor_keys": anchor_keys(question.anchors),
                    "top_k": system.top_k,
                }
            ),
            "pre_rerank_chunk_keys": pre_keys,
            "ranked_candidate_chunk_keys": ranked_keys,
            "returned_chunk_keys": returned_keys,
            "legacy_relevant_chunk_keys": question.relevant_chunk_keys,
            "metrics": metrics,
            "latency_ns": {
                "embedding": embedding_ended - embedding_started,
                "retrieval": retrieval_ended - retrieval_started,
                "reranking": reranking_latency_ns,
                "end_to_end": total_ended - total_started,
            },
            "truncation": {
                "embedding_query_count": embedding_truncated_count,
                "embedding_query_tokens": embedding_truncated_tokens,
                "reranker_query_count": reranker_query_count,
                "reranker_query_tokens": reranker_query_tokens,
                "reranker_passage_count": reranker_passage_count,
                "reranker_passage_tokens": reranker_passage_tokens,
            },
            "warnings": retrieval.warnings,
        },
        rank_rows,
    )


def _model_sizes(
    system: AblationSystem,
    context: dict[str, Any],
) -> dict[str, int | None]:
    records = {
        str(row["model_key"]): row for row in context["models"]
    }
    passage = int(records[system.embedding_model_key]["model_size_bytes"])
    query = int(
        records[system.effective_query_encoder_model_key]["model_size_bytes"]
    )
    reranker = (
        None
        if system.reranker_model_key is None
        else int(records[system.reranker_model_key]["model_size_bytes"])
    )
    unique_keys = {
        system.embedding_model_key,
        system.effective_query_encoder_model_key,
    }
    if system.reranker_model_key is not None:
        unique_keys.add(system.reranker_model_key)
    total = sum(int(records[key]["model_size_bytes"]) for key in unique_keys)
    return {
        "passage_embedding_model_size_bytes": passage,
        "query_embedding_model_size_bytes": query,
        "reranker_model_size_bytes": reranker,
        "total_unique_model_size_bytes": total,
    }


def _run_system(
    system_key: str,
    output_directory: Path,
    sidecar_root: Path,
) -> None:
    context = _validate_context(output_directory)
    benchmark = _load_benchmark()
    artifacts = _load_verified_artifacts()
    systems = _build_systems(artifacts)
    system = systems[system_key]
    family = FAMILY_BY_SYSTEM[system_key]
    engine = _engine()
    published = read_published_corpus_snapshot(engine, benchmark.corpus_release_key)
    before = published.snapshot
    if before.corpus_fingerprint_sha256 != context["corpus"]["corpus_fingerprint_sha256"]:
        raise RuntimeError("published corpus changed before system execution")
    source_before = ProductionSourceFingerprint.model_validate(
        context["production_source_fingerprint"]
    )
    dense_index, sidecar_manifest = load_sidecar_index(
        sidecar_root / family,
        expected_model_key=system.embedding_model_key,
        expected_artifact_manifest_sha256=system.embedding_artifact_manifest_sha256,
        expected_dimension=system.embedding_dimension,
    )
    if dense_index.chunk_keys != before.chunk_keys:
        raise RuntimeError("sidecar does not cover the frozen corpus")
    embedding_provider, representation = _query_provider(family, artifacts)
    reranker_provider = _reranker_provider(system, artifacts)
    retriever = AblationRetriever(
        system=system,
        snapshot=before,
        dense_index=dense_index,
        fts_provider=PostgresFtsCandidateProvider(engine, published),
    )
    print(f"warming {system_key}", flush=True)
    _execute_question(
        system=system,
        question=benchmark.questions[0],
        embedding_provider=embedding_provider,
        embedding_representation=representation,
        retriever=retriever,
        reranker_provider=reranker_provider,
    )
    print(f"measuring {system_key}: {benchmark.question_count} questions", flush=True)
    question_results: list[dict[str, Any]] = []
    rank_rows: list[dict[str, Any]] = []
    for index, question in enumerate(benchmark.questions, start=1):
        result, shifts = _execute_question(
            system=system,
            question=question,
            embedding_provider=embedding_provider,
            embedding_representation=representation,
            retriever=retriever,
            reranker_provider=reranker_provider,
        )
        question_results.append(result)
        rank_rows.extend(shifts)
        print(f"  {index:02d}/{benchmark.question_count} {question.question_key}", flush=True)
    build_record = _read_json(sidecar_root / f"{family}.build.json")
    latency: dict[str, Any] = {
        stage: summarize_latency(
            int(row["latency_ns"][stage])
            for row in question_results
            if row["latency_ns"][stage] is not None
        ).model_dump(mode="json")
        for stage in ("embedding", "retrieval", "end_to_end")
    }
    if reranker_provider is not None:
        latency["reranking"] = summarize_latency(
            int(row["latency_ns"]["reranking"]) for row in question_results
        ).model_dump(mode="json")
    else:
        latency["reranking"] = None
    resources: dict[str, Any] = {
        "peak_runtime_process_rss_bytes": peak_process_rss_bytes(),
        "peak_accelerator_memory_bytes": None,
        **_model_sizes(system, context),
        "index_size_bytes": sidecar_size_bytes(sidecar_root / family),
        "passage_embedding_latency_ns": build_record["telemetry"]["total_latency_ns"],
        "passage_embedding_truncation_count": build_record["telemetry"][
            "truncated_passage_count"
        ],
        "passage_embedding_truncated_tokens": build_record["telemetry"][
            "truncated_passage_tokens"
        ],
    }
    after = read_published_corpus_snapshot(engine, benchmark.corpus_release_key).snapshot
    assert_corpus_unchanged(before, after)
    assert_production_sources_unchanged(
        source_before,
        capture_production_source_fingerprint(REPOSITORY_ROOT),
    )
    payload: dict[str, Any] = {
        "system_result_schema_version": "embedding-ablation-preliminary-system-v1",
        "result_status": "preliminary_unreviewed_legacy_gold",
        "formal_benchmark_eligible": False,
        "system_label": SYSTEM_LABELS[system_key],
        "system": system.model_dump(mode="json"),
        "sidecar_manifest_sha256": sidecar_manifest.sidecar_manifest_sha256,
        "question_results": question_results,
        "quality": summarize_legacy_quality(
            tuple(row["metrics"] for row in question_results)
        ),
        "latency": latency,
        "resources": resources,
        "rank_shift_rows": rank_rows,
        "offline_model_runtime_enforced": True,
        "warmup_count": 1,
        "measured_iteration_count": 1,
    }
    result = {**payload, "system_result_sha256": canonical_json_sha256(payload)}
    _write_json(output_directory / "systems" / f"{system_key}.json", result)
    per_question_payload = {
        "per_question_schema_version": "embedding-ablation-preliminary-questions-v1",
        "system_key": system_key,
        "question_results": question_results,
    }
    _write_json(
        output_directory / "per_question" / f"{system_key}.json",
        {
            **per_question_payload,
            "per_question_sha256": canonical_json_sha256(per_question_payload),
        },
    )
    print(f"completed {system_key}", flush=True)


def _csv_bytes(fieldnames: Sequence[str], rows: Sequence[dict[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        missing = tuple(field for field in fieldnames if field not in row)
        if missing:
            raise RuntimeError(f"CSV row is missing declared fields: {missing}")
        writer.writerow({field: row[field] for field in fieldnames})
    return output.getvalue().encode("utf-8")


def _write_bytes(path: Path, value: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite {path}")
    path.write_bytes(value)


def _finalize(output_directory: Path, target_directory: Path) -> None:
    context = _validate_context(output_directory)
    system_results = [
        _read_json(output_directory / "systems" / f"{key}.json")
        for key in SYSTEM_ORDER
    ]
    benchmark = _load_benchmark()
    current_snapshot = read_published_corpus_snapshot(
        _engine(), benchmark.corpus_release_key
    ).snapshot
    if current_snapshot.corpus_fingerprint_sha256 != context["corpus"][
        "corpus_fingerprint_sha256"
    ]:
        raise RuntimeError("published corpus changed before result finalization")
    assert_production_sources_unchanged(
        ProductionSourceFingerprint.model_validate(
            context["production_source_fingerprint"]
        ),
        capture_production_source_fingerprint(REPOSITORY_ROOT),
    )
    manifest_payload: dict[str, Any] = {
        "experiment_schema_version": "embedding-ablation-preliminary-experiment-v1",
        "experiment_key": "embedding-reranker-ablation-preliminary-eve13-20260901",
        "created_at": context["created_at"],
        "completed_at": _now(),
        "result_status": context["result_status"],
        "formal_benchmark_eligible": False,
        "trust_reasons": context["trust_reasons"],
        "source_commit": context["source_commit"],
        "source_tree_clean": context["source_tree_clean"],
        "production_source_fingerprint": context["production_source_fingerprint"],
        "corpus": context["corpus"],
        "gold": context["gold"],
        "hardware": context["hardware"],
        "models": context["models"],
        "systems": context["systems"],
        "warmup_count": context["warmup_count"],
        "measured_iteration_count": context["measured_iteration_count"],
        "offline_model_runtime_enforced": True,
        "model_loading_latency_included": False,
        "system_result_sha256": [row["system_result_sha256"] for row in system_results],
    }
    manifest = {
        **manifest_payload,
        "experiment_manifest_sha256": canonical_json_sha256(manifest_payload),
    }
    _write_json(output_directory / "experiment_manifest.json", manifest)

    summary_rows: list[dict[str, Any]] = []
    for result in system_results:
        quality = result["quality"]
        latency = result["latency"]
        resources = result["resources"]
        summary_rows.append(
            {
                "system_key": result["system"]["system_key"],
                "system_label": result["system_label"],
                **quality,
                "embedding_latency_p50_ms": latency["embedding"]["p50_ns"] / 1_000_000,
                "embedding_latency_p95_ms": latency["embedding"]["p95_ns"] / 1_000_000,
                "retrieval_latency_p50_ms": latency["retrieval"]["p50_ns"] / 1_000_000,
                "retrieval_latency_p95_ms": latency["retrieval"]["p95_ns"] / 1_000_000,
                "reranking_latency_p50_ms": (
                    None
                    if latency["reranking"] is None
                    else latency["reranking"]["p50_ns"] / 1_000_000
                ),
                "reranking_latency_p95_ms": (
                    None
                    if latency["reranking"] is None
                    else latency["reranking"]["p95_ns"] / 1_000_000
                ),
                "end_to_end_latency_p50_ms": latency["end_to_end"]["p50_ns"]
                / 1_000_000,
                "end_to_end_latency_p95_ms": latency["end_to_end"]["p95_ns"]
                / 1_000_000,
                **resources,
                "query_truncation_count": sum(
                    int(row["truncation"]["embedding_query_count"])
                    for row in result["question_results"]
                ),
                "reranker_query_truncation_count": sum(
                    int(row["truncation"]["reranker_query_count"])
                    for row in result["question_results"]
                ),
                "reranker_passage_truncation_count": sum(
                    int(row["truncation"]["reranker_passage_count"])
                    for row in result["question_results"]
                ),
            }
        )
    summary_payload: dict[str, Any] = {
        "summary_schema_version": "embedding-ablation-preliminary-summary-v1",
        "result_status": "preliminary_unreviewed_legacy_gold",
        "formal_benchmark_eligible": False,
        "question_count": 13,
        "category_analysis_available": False,
        "systems": summary_rows,
    }
    _write_json(
        output_directory / "summary.json",
        {**summary_payload, "summary_sha256": canonical_json_sha256(summary_payload)},
    )

    quality_fields = (
        "system_key",
        "system_label",
        "question_count",
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
    )
    _write_bytes(
        output_directory / "summary.csv",
        _csv_bytes(quality_fields, summary_rows),
    )
    _write_bytes(
        output_directory / "retrieval_quality.csv",
        _csv_bytes(quality_fields, summary_rows),
    )
    latency_fields = (
        "system_key",
        "system_label",
        "embedding_latency_p50_ms",
        "embedding_latency_p95_ms",
        "retrieval_latency_p50_ms",
        "retrieval_latency_p95_ms",
        "reranking_latency_p50_ms",
        "reranking_latency_p95_ms",
        "end_to_end_latency_p50_ms",
        "end_to_end_latency_p95_ms",
    )
    _write_bytes(
        output_directory / "latency.csv",
        _csv_bytes(latency_fields, summary_rows),
    )
    _write_bytes(
        output_directory / "latency_comparison.csv",
        _csv_bytes(latency_fields, summary_rows),
    )
    resource_fields = (
        "system_key",
        "system_label",
        "peak_runtime_process_rss_bytes",
        "peak_accelerator_memory_bytes",
        "passage_embedding_model_size_bytes",
        "query_embedding_model_size_bytes",
        "reranker_model_size_bytes",
        "total_unique_model_size_bytes",
        "index_size_bytes",
        "passage_embedding_latency_ns",
        "passage_embedding_truncation_count",
        "passage_embedding_truncated_tokens",
        "query_truncation_count",
        "reranker_query_truncation_count",
        "reranker_passage_truncation_count",
    )
    _write_bytes(
        output_directory / "resource_usage.csv",
        _csv_bytes(resource_fields, summary_rows),
    )
    _write_bytes(
        output_directory / "resource_comparison.csv",
        _csv_bytes(resource_fields, summary_rows),
    )
    _write_bytes(
        output_directory / "retrieval_by_category.csv",
        _csv_bytes(
            (
                "system_key",
                "system_label",
                "category",
                "question_count",
                "recall_at_5",
                "mrr_at_10",
                "ndcg_at_10",
            ),
            (),
        ),
    )
    rank_rows = [row for result in system_results for row in result["rank_shift_rows"]]
    _write_bytes(
        output_directory / "rank_shift_after_reranking.csv",
        _csv_bytes(
            (
                "system_key",
                "question_key",
                "chunk_key",
                "pre_rerank_rank",
                "post_rerank_rank",
                "rank_shift",
                "reranker_score",
                "is_legacy_relevant",
            ),
            rank_rows,
        ),
    )
    (output_directory / ".run_context.json").unlink()
    os.rename(output_directory, target_directory)


def _format_metric(value: object) -> str:
    return f"{float(str(value)):.3f}"


def _format_ms(value: object) -> str:
    return f"{float(str(value)):,.1f}"


def _generate_report(output_directory: Path, report_path: Path) -> None:
    summary = _read_json(output_directory / "summary.json")
    manifest = _read_json(output_directory / "experiment_manifest.json")
    rows = summary["systems"]
    lines = [
        "# Embedding 与 Reranker 对照实验（preliminary）",
        "",
        "> **状态：preliminary / 不可作为正式模型选择结论。** 当前 13 个 legacy gold "
        "问题尚未完成专家 category、alternative/excluded evidence 与 approved review；因此本报告"
        "不进入 trusted benchmark，也不提供按类别结论。",
        "",
        "## 冻结输入",
        "",
        f"- Corpus：`{manifest['corpus']['corpus_release_key']}`；11 documents，1,464 chunks。",
        "- Gold：13 个现有真实问题；legacy gold SHA-256 "
        f"`{manifest['gold']['legacy_gold_sha256']}`。",
        "- 检索：相同 PostgreSQL FTS、anchors、full/title-abstract branches、RRF k=60、top_k=10。",
        "- 硬件：Apple M2，16 GiB，CPU-only；每系统 warmup 1 次、测量 1 次/问题。",
        "- 模型加载时间不计入请求延迟；sidecar 构建耗时另列于 resource CSV。",
        "",
        "## 质量与请求延迟",
        "",
        "| 系统 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | "
        "nDCG@10 | E2E p50 (ms) | E2E p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row["system_label"]),
                    _format_metric(row["recall_at_1"]),
                    _format_metric(row["recall_at_3"]),
                    _format_metric(row["recall_at_5"]),
                    _format_metric(row["recall_at_10"]),
                    _format_metric(row["mrr_at_10"]),
                    _format_metric(row["ndcg_at_10"]),
                    _format_ms(row["end_to_end_latency_p50_ms"]),
                    _format_ms(row["end_to_end_latency_p95_ms"]),
                )
            )
            + " |"
        )
    best_recall = max(rows, key=lambda row: float(row["recall_at_5"]))
    best_mrr = max(rows, key=lambda row: float(row["mrr_at_10"]))
    fastest = min(rows, key=lambda row: float(row["end_to_end_latency_p50_ms"]))
    lines.extend(
        (
            "",
            "## Preliminary 观察",
            "",
            f"- Recall@5 最高：{best_recall['system_label']}"
            f"（{_format_metric(best_recall['recall_at_5'])}）。",
            f"- MRR@10 最高：{best_mrr['system_label']}"
            f"（{_format_metric(best_mrr['mrr_at_10'])}）。",
            f"- 请求 p50 最低：{fastest['system_label']}"
            f"（{_format_ms(fastest['end_to_end_latency_p50_ms'])} ms）。",
            "- 这些观察只描述当前 13 题；不能替代 30–50 题专家 approved benchmark。",
            "",
            "## 可复现输出",
            "",
            "机器结果位于 `benchmark/embedding_ablation/`。报告由 `summary.json` 与 "
            "`experiment_manifest.json` 确定性生成；plot-ready CSV 包含 quality、latency、resource "
            "与 reranking rank shift。`retrieval_by_category.csv` 仅含表头，因为当前没有获批类别。",
            "",
        )
    )
    _write_bytes(report_path, "\n".join(lines).encode("utf-8"))


def _run_child(
    *arguments: str,
    environment: dict[str, str],
) -> None:
    subprocess.run(
        (sys.executable, str(Path(__file__).resolve()), *arguments),
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


def _run_all(target_directory: Path, sidecar_root: Path, report_path: Path) -> None:
    if target_directory.exists() or target_directory.is_symlink():
        raise RuntimeError("benchmark output directory already exists")
    if report_path.exists() or report_path.is_symlink():
        raise RuntimeError("preliminary report already exists")
    target_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".embedding-ablation-preliminary.", dir=target_directory.parent)
    )
    (temporary / "systems").mkdir()
    (temporary / "per_question").mkdir()
    (temporary / "failures.jsonl").write_bytes(b"")
    environment = os.environ.copy()
    environment.update(
        {
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "4",
            "VECLIB_MAXIMUM_THREADS": "4",
        }
    )
    try:
        _initialize_context(temporary)
        for family in ("bge", "medcpt", "qwen3"):
            print(f"=== prepare index: {family} ===", flush=True)
            _run_child(
                "_prepare-index",
                "--family",
                family,
                "--working-output",
                str(temporary),
                "--sidecar-root",
                str(sidecar_root),
                environment=environment,
            )
        for system_key in SYSTEM_ORDER:
            print(f"=== run system: {system_key} ===", flush=True)
            _run_child(
                "_run-system",
                "--system-key",
                system_key,
                "--working-output",
                str(temporary),
                "--sidecar-root",
                str(sidecar_root),
                environment=environment,
            )
        _finalize(temporary, target_directory)
        _generate_report(target_directory, report_path)
    except Exception as exc:
        failure = {
            "failed_at": _now(),
            "error_type": type(exc).__name__,
            "message": str(exc)[:1000],
        }
        with (temporary / "failures.jsonl").open("ab") as handle:
            handle.write(canonical_json_bytes(failure) + b"\n")
        print(f"partial output retained at {temporary}", file=sys.stderr, flush=True)
        raise
    print(target_directory, flush=True)
    print(report_path, flush=True)


def _finalize_staging(
    working_output: Path,
    target_directory: Path,
    report_path: Path,
) -> None:
    """Finalize a complete retained staging directory without rerunning models."""

    if target_directory.exists() or target_directory.is_symlink():
        raise RuntimeError("benchmark output directory already exists")
    if report_path.exists() or report_path.is_symlink():
        raise RuntimeError("preliminary report already exists")
    if not working_output.is_dir() or working_output.is_symlink():
        raise RuntimeError("working output must be a retained staging directory")
    expected = {
        working_output / "systems" / f"{key}.json" for key in SYSTEM_ORDER
    } | {
        working_output / "per_question" / f"{key}.json" for key in SYSTEM_ORDER
    }
    missing = tuple(sorted(str(path) for path in expected if not path.is_file()))
    if missing:
        raise RuntimeError(f"retained staging is incomplete: {missing}")
    _finalize(working_output, target_directory)
    _generate_report(target_directory, report_path)
    print(target_directory, flush=True)
    print(report_path, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    all_parser = subparsers.add_parser("run-all")
    all_parser.add_argument("--output-directory", required=True, type=Path)
    all_parser.add_argument("--sidecar-root", required=True, type=Path)
    all_parser.add_argument("--report-path", required=True, type=Path)
    prepare = subparsers.add_parser("_prepare-index")
    prepare.add_argument("--family", required=True, choices=("bge", "medcpt", "qwen3"))
    prepare.add_argument("--working-output", required=True, type=Path)
    prepare.add_argument("--sidecar-root", required=True, type=Path)
    run_system = subparsers.add_parser("_run-system")
    run_system.add_argument("--system-key", required=True, choices=SYSTEM_ORDER)
    run_system.add_argument("--working-output", required=True, type=Path)
    run_system.add_argument("--sidecar-root", required=True, type=Path)
    report = subparsers.add_parser("report-only")
    report.add_argument("--output-directory", required=True, type=Path)
    report.add_argument("--report-path", required=True, type=Path)
    finalize = subparsers.add_parser("finalize-staging")
    finalize.add_argument("--working-output", required=True, type=Path)
    finalize.add_argument("--output-directory", required=True, type=Path)
    finalize.add_argument("--report-path", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _build_parser().parse_args()
    if arguments.command == "run-all":
        _run_all(arguments.output_directory, arguments.sidecar_root, arguments.report_path)
    elif arguments.command == "_prepare-index":
        _prepare_index(arguments.family, arguments.working_output, arguments.sidecar_root)
    elif arguments.command == "_run-system":
        _run_system(arguments.system_key, arguments.working_output, arguments.sidecar_root)
    elif arguments.command == "report-only":
        _generate_report(arguments.output_directory, arguments.report_path)
    elif arguments.command == "finalize-staging":
        _finalize_staging(
            arguments.working_output,
            arguments.output_directory,
            arguments.report_path,
        )
    else:
        raise RuntimeError("unknown command")
    gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
