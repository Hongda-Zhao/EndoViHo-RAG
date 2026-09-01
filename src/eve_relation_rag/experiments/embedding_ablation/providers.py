"""Provider protocols and fail-closed output validation for the ablation."""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import Field

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ModelRepresentationContract,
)
from eve_relation_rag.literature.contracts import StrictFrozenSchema

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIT_NORM_TOLERANCE = 0.00001


class ProviderOutputError(ValueError):
    """Raised when an embedding or reranker violates its declared output contract."""


@runtime_checkable
class RerankerProvider(Protocol):
    """Minimal positional reranker boundary required by the experiment."""

    @property
    def model_key(self) -> str: ...

    @property
    def artifact_manifest_sha256(self) -> str: ...

    def score(
        self,
        query: str,
        passages: Sequence[str],
    ) -> Sequence[float]: ...


class RerankerBatchTelemetry(StrictFrozenSchema):
    """Provider-reported truncation for the most recently scored batch."""

    passage_count: int = Field(ge=0)
    truncated_query_count: int = Field(ge=0, le=1)
    truncated_passage_count: int = Field(ge=0)
    truncated_query_tokens: int = Field(ge=0)
    truncated_passage_tokens: int = Field(ge=0)


class EmbeddingQueryTelemetry(StrictFrozenSchema):
    """Provider-reported truncation for the most recently embedded query."""

    truncated_query_count: int = Field(ge=0, le=1)
    truncated_query_tokens: int = Field(ge=0)


class EmbeddingPassageBatchTelemetry(StrictFrozenSchema):
    """Provider-reported truncation for the most recently embedded passage batch."""

    passage_count: int = Field(ge=0)
    truncated_passage_count: int = Field(ge=0)
    truncated_passage_tokens: int = Field(ge=0)


@runtime_checkable
class EmbeddingTelemetryProvider(Protocol):
    """Additional boundary required when a query representation allows truncation."""

    def consume_last_query_telemetry(self) -> EmbeddingQueryTelemetry: ...


@runtime_checkable
class EmbeddingPassageTelemetryProvider(Protocol):
    """Additional boundary required when passage embedding allows truncation."""

    def consume_last_passage_batch_telemetry(self) -> EmbeddingPassageBatchTelemetry: ...


@runtime_checkable
class RerankerTelemetryProvider(RerankerProvider, Protocol):
    """Additional telemetry boundary required for benchmarkable reranking."""

    def consume_last_batch_telemetry(self) -> RerankerBatchTelemetry: ...


class DeterministicFakeRerankerProvider:
    """Offline deterministic reranker for tests only; never trusted for a real report."""

    model_key = "reranker:deterministic-fake:v1"
    artifact_manifest_sha256 = "f" * 64

    def __init__(self) -> None:
        self._last_count = 0

    def score(self, query: str, passages: Sequence[str]) -> tuple[float, ...]:
        self._last_count = len(passages)
        return tuple(self._score(query, passage) for passage in passages)

    def consume_last_batch_telemetry(self) -> RerankerBatchTelemetry:
        return RerankerBatchTelemetry(
            passage_count=self._last_count,
            truncated_query_count=0,
            truncated_passage_count=0,
            truncated_query_tokens=0,
            truncated_passage_tokens=0,
        )

    @staticmethod
    def _score(query: str, passage: str) -> float:
        digest = hashlib.sha256(f"{query}\x00{passage}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)


def validate_embedding_vector(
    vector: Sequence[float],
    *,
    representation: ModelRepresentationContract,
) -> tuple[float, ...]:
    """Canonicalize float32 values and enforce dimension/finite/normalization semantics."""

    if representation.task_kind != "embedding" or representation.dimension is None:
        raise ProviderOutputError("embedding output requires an embedding representation")
    if len(vector) != representation.dimension:
        raise ProviderOutputError(
            f"embedding dimension {len(vector)} does not match {representation.dimension}"
        )
    values: list[float] = []
    for raw_value in vector:
        if isinstance(raw_value, bool):
            raise ProviderOutputError("embedding values must be finite float32 numbers")
        try:
            value = float(raw_value)
            canonical = struct.unpack("<f", struct.pack("<f", value))[0]
        except (OverflowError, TypeError, ValueError, struct.error) as exc:
            raise ProviderOutputError("embedding values must be finite float32 numbers") from exc
        if not math.isfinite(canonical):
            raise ProviderOutputError("embedding values must be finite float32 numbers")
        values.append(canonical)
    if representation.normalization == "l2":
        norm = math.sqrt(math.fsum(value * value for value in values))
        if abs(norm - 1.0) > _UNIT_NORM_TOLERANCE:
            raise ProviderOutputError("embedding does not satisfy the L2 normalization contract")
    return tuple(values)


def validate_embedding_batch(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    representation: ModelRepresentationContract,
) -> tuple[tuple[float, ...], ...]:
    """Require exactly one valid vector for every input passage."""

    if len(vectors) != expected_count:
        raise ProviderOutputError("embedding provider returned the wrong number of vectors")
    return tuple(
        validate_embedding_vector(vector, representation=representation) for vector in vectors
    )


def validate_reranker_identity(provider: RerankerProvider) -> None:
    """Reject empty model identities and malformed artifact manifest hashes."""

    if not provider.model_key or any(character.isspace() for character in provider.model_key):
        raise ProviderOutputError("reranker model_key is invalid")
    if _SHA256_RE.fullmatch(provider.artifact_manifest_sha256) is None:
        raise ProviderOutputError("reranker artifact manifest checksum is invalid")


def validate_reranker_scores(
    scores: Sequence[float],
    *,
    expected_count: int,
) -> tuple[float, ...]:
    """Require one finite positional score per passage without filtering."""

    if len(scores) != expected_count:
        raise ProviderOutputError("reranker returned the wrong number of scores")
    validated: list[float] = []
    for raw_score in scores:
        if isinstance(raw_score, bool):
            raise ProviderOutputError("reranker scores must be finite numbers")
        try:
            score = float(raw_score)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderOutputError("reranker scores must be finite numbers") from exc
        if not math.isfinite(score):
            raise ProviderOutputError("reranker scores must be finite numbers")
        validated.append(score)
    return tuple(validated)
