"""Exact baseline parity assertions between production and experiment retrieval."""

from __future__ import annotations

from eve_relation_rag.experiments.embedding_ablation.retrieval import AblationRetrievalResult
from eve_relation_rag.literature.contracts import RetrievedChunks


class BaselineParityError(AssertionError):
    """Raised when the isolated baseline diverges from production retrieval."""


def assert_baseline_parity(
    production: RetrievedChunks,
    experiment: AblationRetrievalResult,
) -> None:
    """Require exact top-k keys, tiers, component ranks, and quantized RRF scores."""

    experimental_candidates = tuple(
        item.candidate for item in experiment.candidates[: production.requested_top_k]
    )
    if len(experimental_candidates) != len(production.chunks):
        raise BaselineParityError("baseline returned a different number of chunks")
    for observed, expected in zip(experimental_candidates, production.chunks, strict=True):
        identity = (
            observed.chunk_key,
            observed.retrieval_tier,
            observed.fts_rank,
            observed.vector_rank,
            observed.summary_vector_rank,
            observed.rrf_score,
        )
        production_identity = (
            expected.chunk_key,
            expected.retrieval_tier,
            expected.fts_rank,
            expected.vector_rank,
            expected.summary_vector_rank,
            expected.rrf_score,
        )
        if identity != production_identity:
            raise BaselineParityError(
                f"baseline retrieval diverged at chunk {expected.chunk_key}"
            )
