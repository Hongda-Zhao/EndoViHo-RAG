"""Exact reciprocal-rank fusion for Milestone 3 literature retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

_RRF_K = 60
_SCORE_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One chunk with one-based component ranks and a serialized RRF score."""

    chunk_key: str
    fts_rank: int | None
    vector_rank: int | None
    summary_vector_rank: int | None
    rrf_score: str


def fuse_ranked_candidates(
    *,
    fts_chunk_keys: tuple[str, ...],
    vector_chunk_keys: tuple[str, ...],
    summary_vector_chunk_keys: tuple[str, ...],
) -> tuple[FusedCandidate, ...]:
    """Deduplicate three v2 branches at first rank, fuse with k=60, and sort exactly."""

    fts_ranks = _first_ranks(fts_chunk_keys)
    vector_ranks = _first_ranks(vector_chunk_keys)
    summary_vector_ranks = _first_ranks(summary_vector_chunk_keys)
    candidates: list[FusedCandidate] = []
    for chunk_key in fts_ranks.keys() | vector_ranks.keys() | summary_vector_ranks.keys():
        fts_rank = fts_ranks.get(chunk_key)
        vector_rank = vector_ranks.get(chunk_key)
        summary_vector_rank = summary_vector_ranks.get(chunk_key)
        score = sum(
            (
                Decimal(1) / Decimal(_RRF_K + rank)
                for rank in (fts_rank, vector_rank, summary_vector_rank)
                if rank is not None
            ),
            start=Decimal(0),
        ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
        candidates.append(
            FusedCandidate(
                chunk_key=chunk_key,
                fts_rank=fts_rank,
                vector_rank=vector_rank,
                summary_vector_rank=summary_vector_rank,
                rrf_score=f"{score:.12f}",
            )
        )

    return tuple(sorted(candidates, key=_order_key))


def _first_ranks(chunk_keys: tuple[str, ...]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for rank, chunk_key in enumerate(chunk_keys, start=1):
        ranks.setdefault(chunk_key, rank)
    return ranks


def _order_key(candidate: FusedCandidate) -> tuple[Decimal, int, int, str]:
    ranks = tuple(
        rank
        for rank in (
            candidate.fts_rank,
            candidate.vector_rank,
            candidate.summary_vector_rank,
        )
        if rank is not None
    )
    return (
        -Decimal(candidate.rrf_score),
        -len(ranks),
        min(ranks),
        candidate.chunk_key,
    )
