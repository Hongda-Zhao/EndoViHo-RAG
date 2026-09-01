from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import pytest

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    verify_model_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ArtifactFileRecord,
    ModelArtifactManifest,
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.systems import (
    SystemDefinitionError,
    build_bge_medcpt_reranker_system,
    build_medcpt_retrieval_system,
    build_qwen3_retrieval_system,
    medcpt_encoder_bundle_manifest_sha256,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes


def test_system_b_preserves_baseline_generation_and_binds_verified_reranker(
    tmp_path: Path,
) -> None:
    reranker = _verified_artifact(
        tmp_path / "reranker",
        model_key="reranker:test:medcpt",
        model_id="ncbi/MedCPT-Cross-Encoder",
        task_kind="reranker",
        dimension=None,
        revision_character="a",
    )

    system = build_bge_medcpt_reranker_system(
        bge_artifact_manifest_sha256="b" * 64,
        reranker=reranker,
        expected_reranker_model_id="ncbi/MedCPT-Cross-Encoder",
        candidate_depth=20,
        reranker_batch_size=8,
    )

    assert system.embedding_dimension == 384
    assert system.reranker_model_key == reranker.manifest.model_key
    assert system.rerank_candidate_depth == 20
    assert system.reranker_batch_size == 8
    assert system.dense_branches == ("full", "title_abstract")


def test_medcpt_system_keeps_query_and_article_artifact_identities_distinct(
    tmp_path: Path,
) -> None:
    query = _verified_artifact(
        tmp_path / "query",
        model_key="embedding:test:medcpt-query",
        model_id="ncbi/MedCPT-Query-Encoder",
        task_kind="embedding",
        dimension=768,
        revision_character="a",
    )
    article = _verified_artifact(
        tmp_path / "article",
        model_key="embedding:test:medcpt-article",
        model_id="ncbi/MedCPT-Article-Encoder",
        task_kind="embedding",
        dimension=768,
        revision_character="b",
    )

    bundle_sha256 = medcpt_encoder_bundle_manifest_sha256(query, article)
    system = build_medcpt_retrieval_system(
        query_encoder=query,
        article_encoder=article,
        expected_query_model_id="ncbi/MedCPT-Query-Encoder",
        expected_article_model_id="ncbi/MedCPT-Article-Encoder",
        encoder_bundle_manifest_sha256=bundle_sha256,
    )

    assert system.embedding_model_key == article.manifest.model_key
    assert system.query_encoder_model_key == query.manifest.model_key
    assert system.effective_query_encoder_model_key == query.manifest.model_key
    assert system.encoder_bundle_manifest_sha256 == bundle_sha256
    assert system.embedding_dimension == 768
    assert system.system_key.startswith("medcpt_biencoder_768d__")

    with pytest.raises(SystemDefinitionError, match="bundle checksum"):
        build_medcpt_retrieval_system(
            query_encoder=query,
            article_encoder=article,
            expected_query_model_id="ncbi/MedCPT-Query-Encoder",
            expected_article_model_id="ncbi/MedCPT-Article-Encoder",
            encoder_bundle_manifest_sha256="c" * 64,
        )


def test_first_round_factories_reject_unverified_dimensions_and_incomplete_reranker(
    tmp_path: Path,
) -> None:
    qwen_768 = _verified_artifact(
        tmp_path / "qwen-768",
        model_key="embedding:test:qwen-768",
        model_id="Qwen3-Embedding-0.6B",
        task_kind="embedding",
        dimension=768,
        revision_character="c",
    )
    with pytest.raises(SystemDefinitionError, match="schema proposal"):
        build_qwen3_retrieval_system(
            embedding=qwen_768,
            expected_embedding_model_id="Qwen3-Embedding-0.6B",
        )

    qwen_384 = _verified_artifact(
        tmp_path / "qwen-384",
        model_key="embedding:test:qwen-384",
        model_id="Qwen3-Embedding-0.6B",
        task_kind="embedding",
        dimension=384,
        revision_character="d",
    )
    with pytest.raises(SystemDefinitionError, match="supplied together"):
        build_qwen3_retrieval_system(
            embedding=qwen_384,
            expected_embedding_model_id="Qwen3-Embedding-0.6B",
            candidate_depth=20,
        )


def test_factories_reject_wrong_approved_model_identity_and_candidate_depth(
    tmp_path: Path,
) -> None:
    reranker = _verified_artifact(
        tmp_path / "reranker",
        model_key="reranker:test:medcpt",
        model_id="ncbi/MedCPT-Cross-Encoder",
        task_kind="reranker",
        dimension=None,
        revision_character="e",
    )
    with pytest.raises(SystemDefinitionError, match="model ID"):
        build_bge_medcpt_reranker_system(
            bge_artifact_manifest_sha256="f" * 64,
            reranker=reranker,
            expected_reranker_model_id="wrong/model",
            candidate_depth=20,
            reranker_batch_size=8,
        )
    with pytest.raises(SystemDefinitionError, match="20 or 50"):
        build_bge_medcpt_reranker_system(
            bge_artifact_manifest_sha256="f" * 64,
            reranker=reranker,
            expected_reranker_model_id="ncbi/MedCPT-Cross-Encoder",
            candidate_depth=10,
            reranker_batch_size=8,
        )
    with pytest.raises(SystemDefinitionError, match="batch size"):
        build_bge_medcpt_reranker_system(
            bge_artifact_manifest_sha256="f" * 64,
            reranker=reranker,
            expected_reranker_model_id="ncbi/MedCPT-Cross-Encoder",
            candidate_depth=20,
            reranker_batch_size=0,
        )


def _verified_artifact(
    root: Path,
    *,
    model_key: str,
    model_id: str,
    task_kind: Literal["embedding", "reranker"],
    dimension: int | None,
    revision_character: str,
) -> VerifiedModelArtifact:
    model_directory = root / "model"
    model_directory.mkdir(parents=True)
    artifact = model_directory / "config.json"
    artifact.write_bytes(b"{}\n")
    is_embedding = task_kind == "embedding"
    representation = ModelRepresentationContract(
        task_kind=task_kind,
        dimension=dimension,
        pooling="cls" if is_embedding else "not_applicable",
        normalization="l2" if is_embedding else "none",
        similarity="cosine" if is_embedding else "not_applicable",
        query_format="{query}",
        passage_format="{passage}",
        max_sequence_length=512,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )
    manifest = ModelArtifactManifest(
        manifest_schema_version="embedding-ablation-model-artifact-v1",
        model_key=model_key,
        model_id=model_id,
        exact_revision=revision_character * 40,
        license="test-license",
        license_review_status="approved",
        representation=representation,
        runtime_key="runtime:test",
        local_files_only=True,
        trust_remote_code=False,
        files=(
            ArtifactFileRecord(
                relative_path="config.json",
                byte_size=artifact.stat().st_size,
                sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            ),
        ),
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path = root / "artifact-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    return verify_model_artifact(
        model_directory,
        manifest_path,
        manifest_sha256,
        expected_model_id=model_id,
        expected_revision=revision_character * 40,
        expected_task_kind=task_kind,
        expected_dimension=dimension,
    )
