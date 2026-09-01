from __future__ import annotations

from pathlib import Path

import pytest

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.sidecar import (
    ExactVectorIndex,
    SidecarIndexError,
    load_sidecar_index,
    sidecar_size_bytes,
    write_sidecar_index,
)

KEY_A = f"chunk:sha256:{'a' * 64}"
KEY_B = f"chunk:sha256:{'b' * 64}"


def test_exact_sidecar_round_trip_and_tie_break_are_checksum_bound(tmp_path: Path) -> None:
    index = _index()
    hits = index.rank((1.0, 0.0), limit=2)
    assert tuple(hit.chunk_key for hit in hits) == (KEY_A, KEY_B)

    target = tmp_path / "index"
    manifest = write_sidecar_index(target, index)
    restored, restored_manifest = load_sidecar_index(
        target,
        expected_model_key="embedding:test:two-dimensional",
        expected_artifact_manifest_sha256="a" * 64,
        expected_dimension=2,
    )

    assert restored == index
    assert restored_manifest == manifest
    assert sidecar_size_bytes(target) > manifest.vectors_file_size
    with pytest.raises(SidecarIndexError, match="already exists"):
        write_sidecar_index(target, index)


def test_sidecar_rejects_tampering_wrong_dimension_nan_and_bad_normalization(
    tmp_path: Path,
) -> None:
    index = _index()
    target = tmp_path / "index"
    write_sidecar_index(target, index)
    vector_path = target / "vectors.f32le"
    vector_path.write_bytes(vector_path.read_bytes()[:-1] + b"\x00")

    with pytest.raises(SidecarIndexError, match="checksum"):
        load_sidecar_index(
            target,
            expected_model_key=index.model_key,
            expected_artifact_manifest_sha256=index.artifact_manifest_sha256,
            expected_dimension=2,
        )
    with pytest.raises(SidecarIndexError, match="dimension"):
        ExactVectorIndex.build(
            model_key=index.model_key,
            artifact_manifest_sha256=index.artifact_manifest_sha256,
            representation=_representation(),
            chunk_keys=(KEY_A,),
            vectors=((1.0, 0.0, 0.0),),
        )
    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(SidecarIndexError, match="finite"):
            ExactVectorIndex.build(
                model_key=index.model_key,
                artifact_manifest_sha256=index.artifact_manifest_sha256,
                representation=_representation(),
                chunk_keys=(KEY_A,),
                vectors=((non_finite, 0.0),),
            )
    with pytest.raises(SidecarIndexError, match="normalization"):
        ExactVectorIndex.build(
            model_key=index.model_key,
            artifact_manifest_sha256=index.artifact_manifest_sha256,
            representation=_representation(),
            chunk_keys=(KEY_A,),
            vectors=((0.5, 0.0),),
        )


def _index() -> ExactVectorIndex:
    return ExactVectorIndex.build(
        model_key="embedding:test:two-dimensional",
        artifact_manifest_sha256="a" * 64,
        representation=_representation(),
        chunk_keys=(KEY_A, KEY_B),
        vectors=((1.0, 0.0), (0.0, 1.0)),
    )


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
