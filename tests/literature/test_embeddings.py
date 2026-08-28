from __future__ import annotations

import math

import pytest

from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY
from eve_relation_rag.literature.embeddings import (
    EmbeddingValidationError,
    canonical_embedding_sha256,
    validate_embedding,
)
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider

CHUNK_KEY = f"chunk:sha256:{'a' * 64}"


def test_deterministic_fake_provider_is_stable_finite_and_unit_normalized() -> None:
    provider = DeterministicFakeEmbeddingProvider()

    first = provider.embed_documents(("alpha beta", "gamma"))
    replay = provider.embed_documents(("alpha beta", "gamma"))

    assert first == replay
    assert provider.model_key == EMBEDDING_MODEL_KEY
    assert provider.dimension == 384
    assert len(first) == 2
    assert all(len(vector) == 384 for vector in first)
    assert all(all(math.isfinite(value) for value in vector) for vector in first)
    assert all(math.isclose(math.sqrt(math.fsum(v * v for v in vector)), 1.0) for vector in first)


def test_embedding_checksum_binds_float32_vector_model_chunk_and_mode() -> None:
    vector = [0.0] * 384
    vector[0] = 1.0

    first = canonical_embedding_sha256(
        vector,
        model_key=EMBEDDING_MODEL_KEY,
        subject_key=CHUNK_KEY,
        mode="passage",
    )
    replay = canonical_embedding_sha256(
        tuple(vector),
        model_key=EMBEDDING_MODEL_KEY,
        subject_key=CHUNK_KEY,
        mode="passage",
    )
    changed = canonical_embedding_sha256(
        vector,
        model_key=EMBEDDING_MODEL_KEY,
        subject_key=f"chunk:sha256:{'b' * 64}",
        mode="passage",
    )

    assert first == replay
    assert first != changed


@pytest.mark.parametrize(
    "vector, message",
    [
        ([1.0, 0.0], "dimension"),
        ([float("nan")] + [0.0] * 383, "finite"),
        ([0.5] + [0.0] * 383, "unit-normalized"),
    ],
)
def test_embedding_validation_fails_closed(vector: list[float], message: str) -> None:
    with pytest.raises(EmbeddingValidationError, match=message):
        validate_embedding(
            vector,
            expected_dimension=384,
            model_key=EMBEDDING_MODEL_KEY,
            subject_key=CHUNK_KEY,
            mode="passage",
        )
