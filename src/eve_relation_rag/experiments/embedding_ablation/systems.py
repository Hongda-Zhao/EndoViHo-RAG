"""Safe A/B/C/D system factories over already verified local artifacts."""

from __future__ import annotations

import re

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    VerifiedModelArtifact,
    is_verified_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.baseline import baseline_system
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    ModelTaskKind,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SystemDefinitionError(ValueError):
    """Raised when verified artifacts cannot satisfy a safe first-round system."""


def build_bge_medcpt_reranker_system(
    *,
    bge_artifact_manifest_sha256: str,
    reranker: VerifiedModelArtifact,
    expected_reranker_model_id: str,
    candidate_depth: int,
    reranker_batch_size: int,
) -> AblationSystem:
    """System B: exact baseline candidate generation plus a verified cross-encoder."""

    _require_artifact(
        reranker,
        expected_model_id=expected_reranker_model_id,
        task_kind="reranker",
    )
    baseline = baseline_system(bge_artifact_manifest_sha256)
    return AblationSystem.model_validate(
        {
            **baseline.model_dump(mode="python"),
            "system_key": f"bge_small__rrf60__medcpt_ce__d{candidate_depth}",
            "reranker_model_key": reranker.manifest.model_key,
            "reranker_artifact_manifest_sha256": reranker.artifact_manifest_sha256,
            "rerank_candidate_depth": _candidate_depth(candidate_depth),
            "reranker_batch_size": _reranker_batch_size(reranker_batch_size),
        }
    )


def build_medcpt_retrieval_system(
    *,
    query_encoder: VerifiedModelArtifact,
    article_encoder: VerifiedModelArtifact,
    expected_query_model_id: str,
    expected_article_model_id: str,
    encoder_bundle_manifest_sha256: str,
    reranker: VerifiedModelArtifact | None = None,
    expected_reranker_model_id: str | None = None,
    candidate_depth: int | None = None,
    reranker_batch_size: int | None = None,
) -> AblationSystem:
    """System C: distinct verified query/article encoders and optional cross-encoder."""

    _require_artifact(
        query_encoder,
        expected_model_id=expected_query_model_id,
        task_kind="embedding",
    )
    _require_artifact(
        article_encoder,
        expected_model_id=expected_article_model_id,
        task_kind="embedding",
    )
    _require_768_compatible_pair(query_encoder, article_encoder)
    _require_sha256(encoder_bundle_manifest_sha256, "encoder bundle manifest")
    observed_bundle_sha256 = medcpt_encoder_bundle_manifest_sha256(
        query_encoder,
        article_encoder,
    )
    if encoder_bundle_manifest_sha256 != observed_bundle_sha256:
        raise SystemDefinitionError(
            "MedCPT encoder bundle checksum does not match the verified artifact pair"
        )
    reranker_values = _optional_reranker_values(
        reranker=reranker,
        expected_model_id=expected_reranker_model_id,
        candidate_depth=candidate_depth,
        reranker_batch_size=reranker_batch_size,
    )
    suffix = "" if candidate_depth is None else f"__medcpt_ce__d{candidate_depth}"
    return AblationSystem.model_validate(
        {
            "system_key": f"medcpt_biencoder_768d__fts_dense_summary__rrf60{suffix}",
            "embedding_model_key": article_encoder.manifest.model_key,
            "embedding_artifact_manifest_sha256": (
                article_encoder.artifact_manifest_sha256
            ),
            "embedding_dimension": 768,
            "query_encoder_model_key": query_encoder.manifest.model_key,
            "query_encoder_artifact_manifest_sha256": (
                query_encoder.artifact_manifest_sha256
            ),
            "encoder_bundle_manifest_sha256": encoder_bundle_manifest_sha256,
            **reranker_values,
        }
    )


def build_qwen3_retrieval_system(
    *,
    embedding: VerifiedModelArtifact,
    expected_embedding_model_id: str,
    reranker: VerifiedModelArtifact | None = None,
    expected_reranker_model_id: str | None = None,
    candidate_depth: int | None = None,
    reranker_batch_size: int | None = None,
) -> AblationSystem:
    """System D: verified native 384-dimensional Qwen3 embedding and optional reranker."""

    _require_artifact(
        embedding,
        expected_model_id=expected_embedding_model_id,
        task_kind="embedding",
    )
    if embedding.manifest.representation.dimension != 384:
        raise SystemDefinitionError(
            "Qwen3 artifact lacks a verified native 384-dimensional output; "
            "schema proposal required"
        )
    reranker_values = _optional_reranker_values(
        reranker=reranker,
        expected_model_id=expected_reranker_model_id,
        candidate_depth=candidate_depth,
        reranker_batch_size=reranker_batch_size,
    )
    suffix = "" if candidate_depth is None else f"__qwen3_reranker__d{candidate_depth}"
    return AblationSystem.model_validate(
        {
            "system_key": f"qwen3_embedding_0_6b__fts_dense_summary__rrf60{suffix}",
            "embedding_model_key": embedding.manifest.model_key,
            "embedding_artifact_manifest_sha256": embedding.artifact_manifest_sha256,
            "embedding_dimension": 384,
            **reranker_values,
        }
    )


def _require_artifact(
    artifact: VerifiedModelArtifact,
    *,
    expected_model_id: str,
    task_kind: ModelTaskKind,
) -> None:
    if not is_verified_artifact(artifact):
        raise SystemDefinitionError("system component was not issued by the artifact verifier")
    if artifact.manifest.model_id != expected_model_id:
        raise SystemDefinitionError("system component model ID does not match approval")
    if artifact.manifest.representation.task_kind != task_kind:
        raise SystemDefinitionError("system component task kind is incompatible")


def medcpt_encoder_bundle_manifest_sha256(
    query_encoder: VerifiedModelArtifact,
    article_encoder: VerifiedModelArtifact,
) -> str:
    """Hash the exact compatible MedCPT query/article artifact pairing."""

    _require_artifact(
        query_encoder,
        expected_model_id=query_encoder.manifest.model_id,
        task_kind="embedding",
    )
    _require_artifact(
        article_encoder,
        expected_model_id=article_encoder.manifest.model_id,
        task_kind="embedding",
    )
    return canonical_json_sha256(
        {
            "bundle_schema_version": "embedding-ablation-encoder-bundle-v1",
            "query_encoder": {
                "model_id": query_encoder.manifest.model_id,
                "model_key": query_encoder.manifest.model_key,
                "exact_revision": query_encoder.manifest.exact_revision,
                "artifact_manifest_sha256": query_encoder.artifact_manifest_sha256,
                "representation_sha256": canonical_json_sha256(
                    query_encoder.manifest.representation
                ),
            },
            "article_encoder": {
                "model_id": article_encoder.manifest.model_id,
                "model_key": article_encoder.manifest.model_key,
                "exact_revision": article_encoder.manifest.exact_revision,
                "artifact_manifest_sha256": article_encoder.artifact_manifest_sha256,
                "representation_sha256": canonical_json_sha256(
                    article_encoder.manifest.representation
                ),
            },
        }
    )


def _require_768_compatible_pair(
    query_encoder: VerifiedModelArtifact,
    article_encoder: VerifiedModelArtifact,
) -> None:
    query = query_encoder.manifest.representation
    article = article_encoder.manifest.representation
    if query.dimension != 768 or article.dimension != 768:
        raise SystemDefinitionError(
            "MedCPT pair must use the approved experiment-only 768-dimensional sidecar"
        )
    if query.normalization != article.normalization or query.similarity != article.similarity:
        raise SystemDefinitionError("MedCPT query/article vector semantics are incompatible")


def _optional_reranker_values(
    *,
    reranker: VerifiedModelArtifact | None,
    expected_model_id: str | None,
    candidate_depth: int | None,
    reranker_batch_size: int | None,
) -> dict[str, object]:
    values = (reranker, expected_model_id, candidate_depth, reranker_batch_size)
    if all(value is None for value in values):
        return {}
    if any(value is None for value in values):
        raise SystemDefinitionError(
            "optional reranker identity, depth, and batch size must be supplied together"
        )
    assert reranker is not None
    assert expected_model_id is not None
    assert candidate_depth is not None
    assert reranker_batch_size is not None
    _require_artifact(reranker, expected_model_id=expected_model_id, task_kind="reranker")
    return {
        "reranker_model_key": reranker.manifest.model_key,
        "reranker_artifact_manifest_sha256": reranker.artifact_manifest_sha256,
        "rerank_candidate_depth": _candidate_depth(candidate_depth),
        "reranker_batch_size": _reranker_batch_size(reranker_batch_size),
    }


def _candidate_depth(value: int) -> int:
    if value not in {20, 50}:
        raise SystemDefinitionError("reranker candidate depth must be 20 or 50")
    return value


def _reranker_batch_size(value: int) -> int:
    if not 1 <= value <= 512:
        raise SystemDefinitionError("reranker batch size must be in 1..512")
    return value


def _require_sha256(value: str, description: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise SystemDefinitionError(f"{description} checksum is invalid")
