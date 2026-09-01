"""Exact experimental projection of the current production BGE baseline."""

from __future__ import annotations

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    ModelRepresentationContract,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_QUERY_PREFIX,
)

BASELINE_SYSTEM_KEY = "bge_small__fts_dense_summary__rrf60"


def baseline_bge_representation_contract() -> ModelRepresentationContract:
    """Return the audited CLS/L2/cosine/query-prefix contract without loading a model."""

    return ModelRepresentationContract(
        task_kind="embedding",
        dimension=384,
        pooling="cls",
        normalization="l2",
        similarity="cosine",
        query_format=f"{EMBEDDING_QUERY_PREFIX}{{query}}",
        passage_format="{chunk.text}",
        max_sequence_length=512,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )


def baseline_system(artifact_manifest_sha256: str) -> AblationSystem:
    """Bind the current retrieval branches to one verified baseline artifact checksum."""

    return AblationSystem(
        system_key=BASELINE_SYSTEM_KEY,
        embedding_model_key=EMBEDDING_MODEL_KEY,
        embedding_artifact_manifest_sha256=artifact_manifest_sha256,
        embedding_dimension=384,
    )
