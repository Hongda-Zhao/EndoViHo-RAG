from __future__ import annotations

from collections.abc import Sequence

import pytest

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    RankedCandidate,
    RetrievalTier,
)
from eve_relation_rag.experiments.embedding_ablation.providers import RerankerBatchTelemetry
from eve_relation_rag.experiments.embedding_ablation.reranking import (
    RerankingError,
    rerank_candidates,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import CandidatePassage

KEY_A = f"chunk:sha256:{'a' * 64}"
KEY_B = f"chunk:sha256:{'b' * 64}"
KEY_C = f"chunk:sha256:{'c' * 64}"


class RecordingReranker:
    model_key = "reranker:test:recording"
    artifact_manifest_sha256 = "a" * 64

    def __init__(self) -> None:
        self.seen_batches: list[tuple[str, ...]] = []
        self._last_count = 0

    def score(self, query: str, passages: Sequence[str]) -> Sequence[float]:
        assert query == "query"
        batch = tuple(passages)
        self.seen_batches.append(batch)
        self._last_count = len(batch)
        return tuple({"a": 0.1, "b": 0.9, "c": 1.0}[passage] for passage in batch)

    def consume_last_batch_telemetry(self) -> RerankerBatchTelemetry:
        return RerankerBatchTelemetry(
            passage_count=self._last_count,
            truncated_query_count=0,
            truncated_passage_count=0,
            truncated_query_tokens=0,
            truncated_passage_tokens=0,
        )


def test_reranker_preserves_positional_scores_and_never_crosses_anchor_tier() -> None:
    provider = RecordingReranker()
    clock_values = iter((0, 1, 11, 12, 22, 23))

    result = rerank_candidates(
        provider,
        query="query",
        candidates=(
            _candidate(KEY_A, "a", tier="anchored", rank=1),
            _candidate(KEY_B, "b", tier="corpus_fill", rank=2),
            _candidate(KEY_C, "c", tier="corpus_fill", rank=3),
        ),
        batch_size=2,
        clock_ns=lambda: next(clock_values),
    )

    assert provider.seen_batches == [("a", "b"), ("c",)]
    assert result.input_chunk_keys == (KEY_A, KEY_B, KEY_C)
    assert result.positional_scores == (0.1, 0.9, 1.0)
    assert tuple(candidate.chunk_key for candidate in result.ranked_candidates) == (
        KEY_A,
        KEY_C,
        KEY_B,
    )
    assert result.telemetry.batch_size == 2
    assert result.telemetry.batch_latency_ns == (10, 10)
    assert result.telemetry.total_latency_ns == 23


@pytest.mark.parametrize(
    "bad_scores",
    [
        (0.1,),
        (0.1, 0.2, 0.3),
        (float("nan"), 0.2),
        (float("inf"), 0.2),
        (float("-inf"), 0.2),
    ],
)
def test_reranker_rejects_wrong_length_nan_and_inf(bad_scores: tuple[float, ...]) -> None:
    class BadReranker(RecordingReranker):
        def score(self, query: str, passages: Sequence[str]) -> Sequence[float]:
            self._last_count = len(passages)
            return bad_scores

    with pytest.raises(RerankingError, match="wrong number|finite"):
        rerank_candidates(
            BadReranker(),
            query="query",
            candidates=(
                _candidate(KEY_A, "a", tier="corpus_fill", rank=1),
                _candidate(KEY_B, "b", tier="corpus_fill", rank=2),
            ),
            batch_size=2,
        )


def test_reranker_without_truncation_telemetry_is_rejected() -> None:
    class NoTelemetryReranker:
        model_key = "reranker:test:no-telemetry"
        artifact_manifest_sha256 = "b" * 64

        def score(self, query: str, passages: Sequence[str]) -> Sequence[float]:
            return (0.0,) * len(passages)

    with pytest.raises(RerankingError, match="telemetry"):
        rerank_candidates(
            NoTelemetryReranker(),
            query="query",
            candidates=(_candidate(KEY_A, "a", tier="corpus_fill", rank=1),),
            batch_size=1,
        )


def _candidate(
    chunk_key: str,
    passage: str,
    *,
    tier: RetrievalTier,
    rank: int,
) -> CandidatePassage:
    return CandidatePassage(
        candidate=RankedCandidate(
            chunk_key=chunk_key,
            retrieval_tier=tier,
            pre_rerank_rank=rank,
            fts_rank=rank,
            rrf_score="0.010000000000",
        ),
        passage=passage,
    )
