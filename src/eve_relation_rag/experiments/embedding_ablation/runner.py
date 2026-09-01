"""Measured multi-iteration execution for one isolated ablation system."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    AnnotationQuestion,
    ModelRepresentationContract,
    anchor_keys,
)
from eve_relation_rag.experiments.embedding_ablation.metrics import compute_question_metrics
from eve_relation_rag.experiments.embedding_ablation.offline import offline_model_call
from eve_relation_rag.experiments.embedding_ablation.providers import (
    EmbeddingTelemetryProvider,
    RerankerProvider,
    validate_embedding_vector,
)
from eve_relation_rag.experiments.embedding_ablation.reranking import rerank_candidates
from eve_relation_rag.experiments.embedding_ablation.results import (
    LatencySamples,
    QuestionExecutionResult,
    TruncationCounts,
)
from eve_relation_rag.experiments.embedding_ablation.retrieval import AblationRetriever
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.providers import EmbeddingProvider


class ExperimentRunError(RuntimeError):
    """Raised when a system cannot produce stable, contract-valid measured results."""


def run_system_questions(
    *,
    system: AblationSystem,
    questions: Sequence[AnnotationQuestion],
    embedding_provider: EmbeddingProvider,
    embedding_representation: ModelRepresentationContract,
    retriever: AblationRetriever,
    reranker_provider: RerankerProvider | None,
    reranker_batch_size: int | None,
    warmup_count: int,
    measured_iteration_count: int,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> tuple[QuestionExecutionResult, ...]:
    """Run approved questions and require stable ranks across measured iterations."""

    if warmup_count < 0 or measured_iteration_count < 1:
        raise ExperimentRunError("warmup and measured iteration counts are invalid")
    if (
        embedding_provider.model_key != system.effective_query_encoder_model_key
        or embedding_provider.artifact_manifest_sha256
        != system.effective_query_encoder_artifact_manifest_sha256
        or embedding_provider.dimension != system.embedding_dimension
        or embedding_representation.dimension != system.embedding_dimension
    ):
        raise ExperimentRunError("embedding provider does not match the system identity")
    has_reranker = system.rerank_candidate_depth is not None
    if has_reranker != (reranker_provider is not None):
        raise ExperimentRunError("reranker provider does not match the system definition")
    if has_reranker != (reranker_batch_size is not None) or (
        has_reranker and reranker_batch_size != system.reranker_batch_size
    ):
        raise ExperimentRunError("reranker batch size does not match the system definition")

    approved = tuple(
        sorted(
            (question for question in questions if question.review_status == "approved"),
            key=lambda question: question.question_id,
        )
    )
    if not approved:
        raise ExperimentRunError("system run requires approved expert questions")
    results: list[QuestionExecutionResult] = []
    for question in approved:
        for _ in range(warmup_count):
            _execute_once(
                system=system,
                question=question,
                embedding_provider=embedding_provider,
                embedding_representation=embedding_representation,
                retriever=retriever,
                reranker_provider=reranker_provider,
                reranker_batch_size=reranker_batch_size,
                clock_ns=clock_ns,
            )
        measured = tuple(
            _execute_once(
                system=system,
                question=question,
                embedding_provider=embedding_provider,
                embedding_representation=embedding_representation,
                retriever=retriever,
                reranker_provider=reranker_provider,
                reranker_batch_size=reranker_batch_size,
                clock_ns=clock_ns,
            )
            for _ in range(measured_iteration_count)
        )
        pre_rerank_keys = measured[0].pre_rerank_chunk_keys
        ranked_candidate_keys = measured[0].ranked_candidate_chunk_keys
        returned_keys = measured[0].returned_chunk_keys
        if any(
            row.pre_rerank_chunk_keys != pre_rerank_keys
            or row.ranked_candidate_chunk_keys != ranked_candidate_keys
            or row.returned_chunk_keys != returned_keys
            for row in measured[1:]
        ):
            raise ExperimentRunError("retrieval ranks changed across measured iterations")
        metrics = compute_question_metrics(question, returned_keys)
        results.append(
            QuestionExecutionResult(
                system_key=system.system_key,
                question_id=question.question_id,
                category=metrics.category,
                query_sha256=canonical_json_sha256(
                    {
                        "system_key": system.system_key,
                        "question_id": question.question_id,
                        "question": question.question,
                        "anchor_keys": anchor_keys(question.anchors),
                        "top_k": system.top_k,
                    }
                ),
                pre_rerank_chunk_keys=pre_rerank_keys,
                ranked_candidate_chunk_keys=ranked_candidate_keys,
                returned_chunk_keys=returned_keys,
                metrics=metrics,
                latency=LatencySamples(
                    embedding_ns=tuple(row.embedding_latency_ns for row in measured),
                    retrieval_ns=tuple(row.retrieval_latency_ns for row in measured),
                    reranking_ns=(
                        tuple(row.reranking_latency_ns or 0 for row in measured)
                        if has_reranker
                        else None
                    ),
                    end_to_end_ns=tuple(row.end_to_end_latency_ns for row in measured),
                ),
                truncation=TruncationCounts(
                    embedding_query_count=sum(
                        row.embedding_query_truncation_count for row in measured
                    ),
                    embedding_query_tokens=sum(
                        row.embedding_query_truncated_tokens for row in measured
                    ),
                    reranker_query_count=sum(
                        row.reranker_query_truncation_count for row in measured
                    ),
                    reranker_query_tokens=sum(
                        row.reranker_query_truncated_tokens for row in measured
                    ),
                    reranker_passage_count=sum(
                        row.reranker_passage_truncation_count for row in measured
                    ),
                    reranker_passage_tokens=sum(
                        row.reranker_passage_truncated_tokens for row in measured
                    ),
                ),
            )
        )
    return tuple(results)


class _IterationResult:
    __slots__ = (
        "embedding_latency_ns",
        "embedding_query_truncated_tokens",
        "embedding_query_truncation_count",
        "end_to_end_latency_ns",
        "pre_rerank_chunk_keys",
        "ranked_candidate_chunk_keys",
        "reranker_passage_truncated_tokens",
        "reranker_passage_truncation_count",
        "reranker_query_truncated_tokens",
        "reranker_query_truncation_count",
        "reranking_latency_ns",
        "retrieval_latency_ns",
        "returned_chunk_keys",
    )

    def __init__(
        self,
        *,
        embedding_latency_ns: int,
        retrieval_latency_ns: int,
        reranking_latency_ns: int | None,
        end_to_end_latency_ns: int,
        pre_rerank_chunk_keys: tuple[str, ...],
        ranked_candidate_chunk_keys: tuple[str, ...],
        returned_chunk_keys: tuple[str, ...],
        embedding_query_truncation_count: int,
        embedding_query_truncated_tokens: int,
        reranker_query_truncation_count: int,
        reranker_query_truncated_tokens: int,
        reranker_passage_truncation_count: int,
        reranker_passage_truncated_tokens: int,
    ) -> None:
        self.embedding_latency_ns = embedding_latency_ns
        self.retrieval_latency_ns = retrieval_latency_ns
        self.reranking_latency_ns = reranking_latency_ns
        self.end_to_end_latency_ns = end_to_end_latency_ns
        self.pre_rerank_chunk_keys = pre_rerank_chunk_keys
        self.ranked_candidate_chunk_keys = ranked_candidate_chunk_keys
        self.returned_chunk_keys = returned_chunk_keys
        self.embedding_query_truncation_count = embedding_query_truncation_count
        self.embedding_query_truncated_tokens = embedding_query_truncated_tokens
        self.reranker_query_truncation_count = reranker_query_truncation_count
        self.reranker_query_truncated_tokens = reranker_query_truncated_tokens
        self.reranker_passage_truncation_count = reranker_passage_truncation_count
        self.reranker_passage_truncated_tokens = reranker_passage_truncated_tokens


def _execute_once(
    *,
    system: AblationSystem,
    question: AnnotationQuestion,
    embedding_provider: EmbeddingProvider,
    embedding_representation: ModelRepresentationContract,
    retriever: AblationRetriever,
    reranker_provider: RerankerProvider | None,
    reranker_batch_size: int | None,
    clock_ns: Callable[[], int],
) -> _IterationResult:
    total_started = clock_ns()
    embedding_started = clock_ns()
    try:
        with offline_model_call():
            query_vector = validate_embedding_vector(
                embedding_provider.embed_query(question.question),
                representation=embedding_representation,
            )
    except Exception as exc:
        raise ExperimentRunError("query embedding failed its representation contract") from exc
    embedding_ended = clock_ns()
    embedding_truncated_count = 0
    embedding_truncated_tokens = 0
    if embedding_representation.truncation_policy != "reject":
        if not isinstance(embedding_provider, EmbeddingTelemetryProvider):
            raise ExperimentRunError("truncating embedding provider lacks telemetry")
        telemetry = embedding_provider.consume_last_query_telemetry()
        embedding_truncated_count = telemetry.truncated_query_count
        embedding_truncated_tokens = telemetry.truncated_query_tokens

    retrieval_started = clock_ns()
    retrieval = retriever.retrieve(
        question=question.question,
        query_vector=query_vector,
        anchor_keys=anchor_keys(question.anchors),
    )
    retrieval_ended = clock_ns()
    pre_rerank_keys = tuple(item.candidate.chunk_key for item in retrieval.candidates)

    reranking_latency: int | None = None
    reranker_query_count = 0
    reranker_query_tokens = 0
    reranker_passage_count = 0
    reranker_passage_tokens = 0
    if reranker_provider is not None and reranker_batch_size is not None:
        reranked = rerank_candidates(
            reranker_provider,
            query=question.question,
            candidates=retrieval.candidates,
            batch_size=reranker_batch_size,
            clock_ns=clock_ns,
        )
        returned_keys = tuple(
            candidate.chunk_key for candidate in reranked.ranked_candidates[: system.top_k]
        )
        ranked_candidate_keys = tuple(
            candidate.chunk_key for candidate in reranked.ranked_candidates
        )
        reranking_latency = reranked.telemetry.total_latency_ns
        reranker_query_count = reranked.telemetry.truncated_query_count
        reranker_query_tokens = reranked.telemetry.truncated_query_tokens
        reranker_passage_count = reranked.telemetry.truncated_passage_count
        reranker_passage_tokens = reranked.telemetry.truncated_passage_tokens
    else:
        ranked_candidate_keys = pre_rerank_keys
        returned_keys = ranked_candidate_keys[: system.top_k]
    total_ended = clock_ns()
    return _IterationResult(
        embedding_latency_ns=embedding_ended - embedding_started,
        retrieval_latency_ns=retrieval_ended - retrieval_started,
        reranking_latency_ns=reranking_latency,
        end_to_end_latency_ns=total_ended - total_started,
        pre_rerank_chunk_keys=pre_rerank_keys,
        ranked_candidate_chunk_keys=ranked_candidate_keys,
        returned_chunk_keys=returned_keys,
        embedding_query_truncation_count=embedding_truncated_count,
        embedding_query_truncated_tokens=embedding_truncated_tokens,
        reranker_query_truncation_count=reranker_query_count,
        reranker_query_truncated_tokens=reranker_query_tokens,
        reranker_passage_truncation_count=reranker_passage_count,
        reranker_passage_truncated_tokens=reranker_passage_tokens,
    )
