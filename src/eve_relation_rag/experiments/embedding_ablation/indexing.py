"""Build exact sidecar passage vectors without writing production corpus tables."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import Field

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ModelRepresentationContract,
)
from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    CorpusSnapshot,
    SnapshotChunk,
)
from eve_relation_rag.experiments.embedding_ablation.offline import offline_model_call
from eve_relation_rag.experiments.embedding_ablation.providers import (
    EmbeddingPassageTelemetryProvider,
    ProviderOutputError,
    validate_embedding_batch,
)
from eve_relation_rag.experiments.embedding_ablation.sidecar import ExactVectorIndex
from eve_relation_rag.literature.contracts import StrictFrozenSchema
from eve_relation_rag.literature.providers import EmbeddingProvider


class SidecarBuildError(RuntimeError):
    """Raised before persistence when passage embeddings violate the model contract."""


class PassageEmbeddingBuildTelemetry(StrictFrozenSchema):
    """Corpus-side embedding latency, batching, and truncation counts."""

    passage_count: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    batch_count: int = Field(ge=1)
    batch_latency_ns: tuple[int, ...] = Field(min_length=1)
    total_latency_ns: int = Field(ge=0)
    truncated_passage_count: int = Field(ge=0)
    truncated_passage_tokens: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class SidecarBuildResult:
    index: ExactVectorIndex
    telemetry: PassageEmbeddingBuildTelemetry


def build_exact_sidecar_index(
    snapshot: CorpusSnapshot,
    provider: EmbeddingProvider,
    representation: ModelRepresentationContract,
    *,
    batch_size: int,
    passage_serializer: Callable[[SnapshotChunk], str] | None = None,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> SidecarBuildResult:
    """Embed the frozen chunks in canonical order and retain no persisted text."""

    if not 1 <= batch_size <= 500:
        raise SidecarBuildError("sidecar embedding batch_size must be in 1..500")
    if (
        provider.dimension != representation.dimension
        or representation.task_kind != "embedding"
    ):
        raise SidecarBuildError("embedding provider dimension does not match representation")
    vectors: list[tuple[float, ...]] = []
    latencies: list[int] = []
    truncated_count = 0
    truncated_tokens = 0
    total_started = clock_ns()
    for offset in range(0, len(snapshot.chunks), batch_size):
        batch = snapshot.chunks[offset : offset + batch_size]
        passages = tuple(
            chunk.text if passage_serializer is None else passage_serializer(chunk)
            for chunk in batch
        )
        if any(not passage.strip() for passage in passages):
            raise SidecarBuildError("passage serializer returned empty text")
        started = clock_ns()
        try:
            with offline_model_call():
                raw_vectors = provider.embed_documents(passages)
        except Exception as exc:
            raise SidecarBuildError("passage embedding provider failed") from exc
        ended = clock_ns()
        try:
            validated = validate_embedding_batch(
                raw_vectors,
                expected_count=len(batch),
                representation=representation,
            )
        except ProviderOutputError as exc:
            raise SidecarBuildError(str(exc)) from exc
        if representation.truncation_policy != "reject":
            if not isinstance(provider, EmbeddingPassageTelemetryProvider):
                raise SidecarBuildError("truncating passage provider lacks telemetry")
            telemetry = provider.consume_last_passage_batch_telemetry()
            if (
                telemetry.passage_count != len(batch)
                or telemetry.truncated_passage_count > len(batch)
            ):
                raise SidecarBuildError("passage truncation telemetry does not match batch")
            truncated_count += telemetry.truncated_passage_count
            truncated_tokens += telemetry.truncated_passage_tokens
        vectors.extend(validated)
        latencies.append(ended - started)
    total_ended = clock_ns()
    index = ExactVectorIndex.build(
        model_key=provider.model_key,
        artifact_manifest_sha256=provider.artifact_manifest_sha256,
        representation=representation,
        chunk_keys=snapshot.chunk_keys,
        vectors=tuple(vectors),
    )
    return SidecarBuildResult(
        index=index,
        telemetry=PassageEmbeddingBuildTelemetry(
            passage_count=len(snapshot.chunks),
            batch_size=batch_size,
            batch_count=len(latencies),
            batch_latency_ns=tuple(latencies),
            total_latency_ns=total_ended - total_started,
            truncated_passage_count=truncated_count,
            truncated_passage_tokens=truncated_tokens,
        ),
    )
