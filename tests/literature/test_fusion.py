from __future__ import annotations

from eve_relation_rag.retrieval.literature.fusion import fuse_ranked_candidates


def test_rrf60_uses_one_based_component_ranks_and_exact_tie_breaks() -> None:
    fused = fuse_ranked_candidates(
        fts_chunk_keys=("chunk-b", "chunk-a", "chunk-c"),
        vector_chunk_keys=("chunk-a", "chunk-d", "chunk-c"),
        summary_vector_chunk_keys=("chunk-a", "chunk-e"),
    )

    assert [item.chunk_key for item in fused] == [
        "chunk-a",
        "chunk-c",
        "chunk-b",
        "chunk-d",
        "chunk-e",
    ]
    assert fused[0].fts_rank == 2
    assert fused[0].vector_rank == 1
    assert fused[0].summary_vector_rank == 1
    assert fused[0].rrf_score == "0.048915917504"
    assert fused[2].rrf_score == "0.016393442623"
    assert fused[3].rrf_score == "0.016129032258"


def test_rrf_collapses_duplicate_branch_keys_at_their_first_rank() -> None:
    fused = fuse_ranked_candidates(
        fts_chunk_keys=("chunk-a", "chunk-a", "chunk-b"),
        vector_chunk_keys=(),
        summary_vector_chunk_keys=(),
    )

    assert [(item.chunk_key, item.fts_rank) for item in fused] == [
        ("chunk-a", 1),
        ("chunk-b", 3),
    ]


def test_rrf60_summary_branch_is_equal_and_deduplicated() -> None:
    fused = fuse_ranked_candidates(
        fts_chunk_keys=(),
        vector_chunk_keys=("chunk-b", "chunk-a"),
        summary_vector_chunk_keys=("chunk-a", "chunk-a", "chunk-c"),
    )

    assert [item.chunk_key for item in fused] == ["chunk-a", "chunk-b", "chunk-c"]
    assert fused[0].vector_rank == 2
    assert fused[0].summary_vector_rank == 1
    assert fused[2].summary_vector_rank == 3
