"""Position-preserving, tier-aware reranking with required telemetry."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from pydantic import Field

from eve_relation_rag.experiments.embedding_ablation.contracts import RankedCandidate
from eve_relation_rag.experiments.embedding_ablation.offline import offline_model_call
from eve_relation_rag.experiments.embedding_ablation.providers import (
    ProviderOutputError,
    RerankerBatchTelemetry,
    RerankerProvider,
    RerankerTelemetryProvider,
    validate_reranker_identity,
    validate_reranker_scores,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import CandidatePassage
from eve_relation_rag.literature.contracts import ChunkKey, StrictFrozenSchema


class RerankingError(RuntimeError):
    """Raised when a reranker mutates, filters, or mis-scores a candidate pool."""


class RerankingTelemetry(StrictFrozenSchema):
    """Complete measured reranking latency, batching, and truncation counts."""

    candidate_count: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    batch_count: int = Field(ge=1)
    batch_latency_ns: tuple[int, ...] = Field(min_length=1)
    total_latency_ns: int = Field(ge=0)
    truncated_query_count: int = Field(ge=0, le=1)
    truncated_passage_count: int = Field(ge=0)
    truncated_query_tokens: int = Field(ge=0)
    truncated_passage_tokens: int = Field(ge=0)


class RerankingResult(StrictFrozenSchema):
    """Auditable positional scores and the final deterministic candidate order."""

    input_chunk_keys: tuple[ChunkKey, ...]
    positional_scores: tuple[float, ...]
    ranked_candidates: tuple[RankedCandidate, ...]
    telemetry: RerankingTelemetry


def rerank_candidates(
    provider: RerankerProvider,
    *,
    query: str,
    candidates: Sequence[CandidatePassage],
    batch_size: int,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> RerankingResult:
    """Score all candidates positionally, then sort only within retrieval tiers."""

    materialized = tuple(candidates)
    if not materialized:
        raise RerankingError("reranking requires at least one candidate")
    if not 1 <= batch_size <= 512:
        raise RerankingError("reranker batch_size must be in 1..512")
    keys = tuple(item.candidate.chunk_key for item in materialized)
    if len(keys) != len(set(keys)):
        raise RerankingError("reranking candidates must be unique")
    try:
        validate_reranker_identity(provider)
    except ProviderOutputError as exc:
        raise RerankingError(str(exc)) from exc
    if not isinstance(provider, RerankerTelemetryProvider):
        raise RerankingError("benchmarkable reranker must report truncation telemetry")

    scores: list[float] = []
    latencies: list[int] = []
    telemetry_rows: list[RerankerBatchTelemetry] = []
    total_start = clock_ns()
    for offset in range(0, len(materialized), batch_size):
        batch = materialized[offset : offset + batch_size]
        passages = tuple(item.passage for item in batch)
        original_passages = passages
        started = clock_ns()
        try:
            with offline_model_call():
                raw_scores = provider.score(query, passages)
        except Exception as exc:
            raise RerankingError("reranker provider failed") from exc
        ended = clock_ns()
        if passages != original_passages:
            raise RerankingError("reranker modified candidate passage order")
        try:
            validated = validate_reranker_scores(raw_scores, expected_count=len(batch))
        except ProviderOutputError as exc:
            raise RerankingError(str(exc)) from exc
        try:
            batch_telemetry = provider.consume_last_batch_telemetry()
        except Exception as exc:
            raise RerankingError("reranker telemetry is unavailable") from exc
        if (
            batch_telemetry.passage_count != len(batch)
            or batch_telemetry.truncated_passage_count > len(batch)
        ):
            raise RerankingError("reranker telemetry does not match the scored batch")
        scores.extend(validated)
        latencies.append(ended - started)
        telemetry_rows.append(batch_telemetry)
    total_end = clock_ns()
    positional_scores = tuple(scores)
    if len(positional_scores) != len(materialized):
        raise RerankingError("reranker deleted one or more candidates")

    scored = tuple(zip(materialized, positional_scores, strict=True))
    tier_order = {"anchored": 0, "corpus_fill": 1}
    ordered = tuple(
        sorted(
            scored,
            key=lambda item: (
                tier_order[item[0].candidate.retrieval_tier],
                -item[1],
                item[0].candidate.pre_rerank_rank,
                item[0].candidate.chunk_key,
            ),
        )
    )
    ranked = tuple(
        candidate.candidate.model_copy(
            update={"reranker_score": score, "final_rank": final_rank}
        )
        for final_rank, (candidate, score) in enumerate(ordered, start=1)
    )
    telemetry = RerankingTelemetry(
        candidate_count=len(materialized),
        batch_size=batch_size,
        batch_count=len(telemetry_rows),
        batch_latency_ns=tuple(latencies),
        total_latency_ns=total_end - total_start,
        truncated_query_count=max(
            row.truncated_query_count for row in telemetry_rows
        ),
        truncated_passage_count=sum(
            row.truncated_passage_count for row in telemetry_rows
        ),
        truncated_query_tokens=max(
            row.truncated_query_tokens for row in telemetry_rows
        ),
        truncated_passage_tokens=sum(
            row.truncated_passage_tokens for row in telemetry_rows
        ),
    )
    return RerankingResult(
        input_chunk_keys=keys,
        positional_scores=positional_scores,
        ranked_candidates=ranked,
        telemetry=telemetry,
    )
