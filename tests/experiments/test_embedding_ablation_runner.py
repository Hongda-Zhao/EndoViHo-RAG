from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import cast

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    AnnotationQuestion,
    EvidenceGroup,
    ModelRepresentationContract,
    RankedCandidate,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import (
    AblationRetrievalResult,
    AblationRetriever,
    CandidatePassage,
)
from eve_relation_rag.experiments.embedding_ablation.runner import run_system_questions

KEY_A = f"chunk:sha256:{'a' * 64}"
KEY_B = f"chunk:sha256:{'b' * 64}"


class StaticEmbeddingProvider:
    model_key = "embedding:test:runner"
    artifact_manifest_sha256 = "a" * 64
    dimension = 2

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class StaticRetriever:
    def retrieve(
        self,
        *,
        question: str,
        query_vector: tuple[float, ...],
        anchor_keys: tuple[str, ...],
    ) -> AblationRetrievalResult:
        assert question == "Which chunk is required?"
        assert query_vector == (1.0, 0.0)
        assert anchor_keys == ()
        return AblationRetrievalResult(
            candidates=(
                _candidate(KEY_A, 1),
                _candidate(KEY_B, 2),
            ),
            warnings=(),
        )


def test_runner_warms_up_then_records_stable_exact_stage_samples() -> None:
    clock = itertools.count(start=0, step=10)

    results = run_system_questions(
        system=_system(),
        questions=(_question(),),
        embedding_provider=StaticEmbeddingProvider(),
        embedding_representation=_representation(),
        retriever=cast(AblationRetriever, StaticRetriever()),
        reranker_provider=None,
        reranker_batch_size=None,
        warmup_count=1,
        measured_iteration_count=3,
        clock_ns=lambda: next(clock),
    )

    result = results[0]
    assert result.pre_rerank_chunk_keys == result.ranked_candidate_chunk_keys == (
        KEY_A,
        KEY_B,
    )
    assert result.returned_chunk_keys == (KEY_A, KEY_B)
    assert result.metrics.recall_at_1 == "1.000000000000"
    assert result.latency.embedding_ns == (10, 10, 10)
    assert result.latency.retrieval_ns == (10, 10, 10)
    assert result.latency.end_to_end_ns == (50, 50, 50)
    assert result.truncation.embedding_query_count == 0


def _candidate(key: str, rank: int) -> CandidatePassage:
    return CandidatePassage(
        candidate=RankedCandidate(
            chunk_key=key,
            retrieval_tier="corpus_fill",
            pre_rerank_rank=rank,
            vector_rank=rank,
            rrf_score="0.010000000000",
            final_rank=rank,
        ),
        passage=f"passage {rank}",
    )


def _system() -> AblationSystem:
    return AblationSystem(
        system_key="runner_system",
        embedding_model_key="embedding:test:runner",
        embedding_artifact_manifest_sha256="a" * 64,
        embedding_dimension=2,
    )


def _question() -> AnnotationQuestion:
    return AnnotationQuestion(
        question_id="q-runner",
        question="Which chunk is required?",
        category="evidence",
        required_chunk_keys=(KEY_A,),
        evidence_groups=(EvidenceGroup(group_id="e1", required_chunk_key=KEY_A),),
        review_status="approved",
        reviewer_id="expert-1",
        reviewed_at="2099-01-01T00:00:00Z",
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
