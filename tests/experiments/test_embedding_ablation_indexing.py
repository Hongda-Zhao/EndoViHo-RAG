from __future__ import annotations

import itertools
from collections.abc import Sequence

import pytest

from eve_relation_rag.experiments.embedding_ablation.baseline import (
    baseline_bge_representation_contract,
)
from eve_relation_rag.experiments.embedding_ablation.indexing import (
    SidecarBuildError,
    build_exact_sidecar_index,
)
from eve_relation_rag.literature.providers import DeterministicFakeEmbeddingProvider
from tests.experiments.test_embedding_ablation_retrieval import _snapshot


def test_sidecar_build_embeds_frozen_chunks_in_order_and_records_batch_latency() -> None:
    clock = itertools.count(start=0, step=10)

    result = build_exact_sidecar_index(
        _snapshot(),
        DeterministicFakeEmbeddingProvider(),
        baseline_bge_representation_contract(),
        batch_size=2,
        clock_ns=lambda: next(clock),
    )

    assert result.index.chunk_keys == _snapshot().chunk_keys
    assert result.telemetry.passage_count == 3
    assert result.telemetry.batch_count == 2
    assert result.telemetry.batch_latency_ns == (10, 10)
    assert result.telemetry.total_latency_ns == 50
    assert result.telemetry.truncated_passage_count == 0


def test_sidecar_build_rejects_provider_output_length_mismatch() -> None:
    class MissingVectorProvider(DeterministicFakeEmbeddingProvider):
        def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            return super().embed_documents(texts[:-1])

    with pytest.raises(SidecarBuildError, match="wrong number"):
        build_exact_sidecar_index(
            _snapshot(),
            MissingVectorProvider(),
            baseline_bge_representation_contract(),
            batch_size=3,
        )


def test_sidecar_build_uses_explicit_passage_serializer_without_rechunking() -> None:
    observed: list[str] = []

    class RecordingProvider(DeterministicFakeEmbeddingProvider):
        def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            observed.extend(texts)
            return super().embed_documents(texts)

    snapshot = _snapshot()
    result = build_exact_sidecar_index(
        snapshot,
        RecordingProvider(),
        baseline_bge_representation_contract(),
        batch_size=2,
        passage_serializer=lambda chunk: f"{chunk.document_key}\n{chunk.text}",
    )

    assert result.index.chunk_keys == snapshot.chunk_keys
    assert observed == [f"{chunk.document_key}\n{chunk.text}" for chunk in snapshot.chunks]
