from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from eve_relation_rag.literature.benchmarking import (
    BenchmarkDefinition,
    BenchmarkQuestion,
    BenchmarkReport,
    BenchmarkRuntimeFingerprint,
    BenchmarkValidationError,
    build_benchmark_definition,
    run_benchmark,
    validate_benchmark_report_against_definition,
)
from eve_relation_rag.literature.contracts import (
    LiteratureRetrievalInvocation,
    PlainTextLocator,
    RetrievedChunk,
    RetrievedChunks,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256, canonical_query_sha256

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
RELEVANT_KEY = f"chunk:sha256:{'a' * 64}"
OTHER_KEY = f"chunk:sha256:{'b' * 64}"


class StaticRetriever:
    def __init__(
        self,
        returned_keys: tuple[str, ...],
        *,
        manifest_sha256: str = "c" * 64,
        query_sha256: str | None = None,
    ) -> None:
        self._returned_keys = returned_keys
        self._manifest_sha256 = manifest_sha256
        self._query_sha256 = query_sha256

    def retrieve(self, invocation: LiteratureRetrievalInvocation) -> RetrievedChunks:
        chunks = tuple(_chunk(index, key) for index, key in enumerate(self._returned_keys, start=1))
        return RetrievedChunks(
            result_schema_version="retrieved-chunks-v2",
            status="ok",
            corpus_release_key=CORPUS_KEY,
            corpus_manifest_sha256=self._manifest_sha256,
            retrieval_policy_key=(
                "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
            ),
            embedding_model_key=(
                "embedding:hf:BAAI-bge-small-en-v1.5@"
                "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
            ),
            query_sha256=(
                self._query_sha256
                or canonical_query_sha256(invocation.request, invocation.system_anchors)
            ),
            requested_top_k=10,
            returned_count=len(chunks),
            retrieval_executed=True,
            anchor_mode="none",
            anchors_applied=(),
            warnings=() if chunks else ("no_chunks_retrieved",),
            chunks=chunks,
        )


def test_benchmark_definition_and_passing_metrics_are_checksum_bound() -> None:
    definition = build_benchmark_definition(
        tier="deterministic_ci",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="c" * 64,
        questions=(
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q1",
                question="Which synthetic chunk is relevant?",
                relevant_chunk_keys=(RELEVANT_KEY,),
            ),
        ),
    )

    report = run_benchmark(StaticRetriever((RELEVANT_KEY, OTHER_KEY)), definition)

    assert report.passed is True
    assert report.recall_at_5 == report.recall_at_10 == "1.000000000000"
    assert len(definition.gold_sha256) == 64
    assert len(definition.benchmark_manifest_sha256) == 64
    assert len(report.benchmark_sha256) == 64
    assert report.corpus_manifest_sha256 == definition.corpus_manifest_sha256
    assert report.retrieval_policy_key == definition.retrieval_policy_key
    assert report.embedding_model_key == definition.embedding_model_key
    assert report.gold_sha256 == definition.gold_sha256
    assert report.question_count == definition.question_count
    assert BenchmarkReport.model_validate_json(report.model_dump_json()) == report
    validate_benchmark_report_against_definition(report, definition)


def test_benchmark_fails_when_recall_thresholds_are_not_met() -> None:
    definition = build_benchmark_definition(
        tier="deterministic_ci",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="c" * 64,
        questions=(
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q1",
                question="Which synthetic chunk is relevant?",
                relevant_chunk_keys=(RELEVANT_KEY,),
            ),
        ),
    )

    report = run_benchmark(StaticRetriever((OTHER_KEY,)), definition)

    assert report.passed is False
    assert report.recall_at_5 == report.recall_at_10 == "0.000000000000"


def test_benchmark_response_identity_mismatch_is_an_auditable_failure() -> None:
    definition = _definition()

    report = run_benchmark(
        StaticRetriever((RELEVANT_KEY,), manifest_sha256="f" * 64),
        definition,
    )

    assert report.passed is False
    assert report.question_results[0].status == "error"
    assert report.question_results[0].error_code == "benchmark_response_identity_mismatch"
    assert report.question_results[0].returned_chunk_keys == ()


def test_benchmark_query_identity_mismatch_is_an_auditable_failure() -> None:
    definition = _definition()

    report = run_benchmark(
        StaticRetriever((RELEVANT_KEY,), query_sha256="f" * 64),
        definition,
    )

    assert report.passed is False
    assert report.question_results[0].error_code == "benchmark_response_identity_mismatch"


def test_benchmark_report_rejects_tampered_aggregate_and_complete_hash() -> None:
    definition = _definition()
    report = run_benchmark(StaticRetriever((RELEVANT_KEY,)), definition)
    payload = report.model_dump(mode="python")
    payload["recall_at_5"] = "0.000000000000"

    with pytest.raises(ValidationError, match="aggregate metrics"):
        BenchmarkReport.model_validate(payload)

    payload = report.model_dump(mode="python")
    payload["benchmark_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="complete report"):
        BenchmarkReport.model_validate(payload)


def test_definition_validation_recomputes_question_recall_from_gold() -> None:
    definition = _definition()
    report = run_benchmark(StaticRetriever((RELEVANT_KEY,)), definition)
    payload = report.model_dump(mode="python")
    question = dict(payload["question_results"][0])
    question["returned_chunk_keys"] = (OTHER_KEY,)
    payload["question_results"] = (question,)
    payload["benchmark_sha256"] = _report_sha256(payload)
    internally_consistent = BenchmarkReport.model_validate(payload)

    with pytest.raises(BenchmarkValidationError, match="recall does not match gold"):
        validate_benchmark_report_against_definition(internally_consistent, definition)


def test_definition_validation_rejects_rehashed_identity_tamper() -> None:
    definition = _definition()
    report = run_benchmark(StaticRetriever((RELEVANT_KEY,)), definition)
    payload = report.model_dump(mode="python")
    payload["corpus_manifest_sha256"] = "f" * 64
    payload["benchmark_sha256"] = _report_sha256(payload)
    internally_consistent = BenchmarkReport.model_validate(payload)

    with pytest.raises(BenchmarkValidationError, match="exact benchmark definition"):
        validate_benchmark_report_against_definition(internally_consistent, definition)


def test_definition_validation_rejects_rehashed_query_tamper() -> None:
    definition = _definition()
    report = run_benchmark(StaticRetriever((RELEVANT_KEY,)), definition)
    payload = report.model_dump(mode="python")
    question = dict(payload["question_results"][0])
    question["query_sha256"] = "f" * 64
    payload["question_results"] = (question,)
    payload["benchmark_sha256"] = _report_sha256(payload)
    internally_consistent = BenchmarkReport.model_validate(payload)

    with pytest.raises(BenchmarkValidationError, match="query identity mismatch"):
        validate_benchmark_report_against_definition(internally_consistent, definition)


def test_definition_validation_rejects_wrong_question_order() -> None:
    definition = build_benchmark_definition(
        tier="deterministic_ci",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="c" * 64,
        questions=(
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q1",
                question="Which synthetic chunk is relevant?",
                relevant_chunk_keys=(RELEVANT_KEY,),
            ),
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q2",
                question="Which other synthetic chunk is relevant?",
                relevant_chunk_keys=(OTHER_KEY,),
            ),
        ),
    )
    report = run_benchmark(StaticRetriever((RELEVANT_KEY, OTHER_KEY)), definition)
    payload = report.model_dump(mode="python")
    payload["question_results"] = tuple(reversed(payload["question_results"]))
    payload["benchmark_sha256"] = _report_sha256(payload)
    reordered = BenchmarkReport.model_validate(payload)

    with pytest.raises(BenchmarkValidationError, match="question order"):
        validate_benchmark_report_against_definition(reordered, definition)


def test_pilot_benchmark_requires_explicit_runtime_fingerprint() -> None:
    definition = build_benchmark_definition(
        tier="pilot_release",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="c" * 64,
        questions=(
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q1",
                question="Which synthetic chunk is relevant?",
                relevant_chunk_keys=(RELEVANT_KEY,),
            ),
        ),
    )

    with pytest.raises(BenchmarkValidationError, match="runtime fingerprint"):
        run_benchmark(StaticRetriever((RELEVANT_KEY,)), definition)

    runtime = BenchmarkRuntimeFingerprint(
        python_version="3.12.11",
        platform_system="Darwin",
        platform_release="25.6.0",
        platform_machine="arm64",
        uv_lock_sha256="1" * 64,
        postgresql_version="PostgreSQL 16.10",
        pgvector_version="0.8.1",
    )
    report = run_benchmark(
        StaticRetriever((RELEVANT_KEY,)),
        definition,
        runtime_fingerprint=runtime,
    )

    assert report.passed is True
    assert report.runtime_fingerprint == runtime


def _definition() -> BenchmarkDefinition:
    return build_benchmark_definition(
        tier="deterministic_ci",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="c" * 64,
        questions=(
            BenchmarkQuestion(
                question_key="benchmark:synthetic:q1",
                question="Which synthetic chunk is relevant?",
                relevant_chunk_keys=(RELEVANT_KEY,),
            ),
        ),
    )


def _report_sha256(payload: dict[str, object]) -> str:
    core = dict(payload)
    del core["benchmark_sha256"]
    return canonical_json_sha256(core)


def _chunk(citation_index: int, chunk_key: str) -> RetrievedChunk:
    text = f"Text for {chunk_key}."
    return RetrievedChunk(
        citation_id=f"D{citation_index}",
        chunk_key=chunk_key,
        document_key=f"document:sha256:{'e' * 64}",
        title="Synthetic benchmark document",
        doi=None,
        pmid=None,
        pmcid=None,
        section=None,
        locator=PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=citation_index,
            line_start=citation_index,
            line_end=citation_index,
            token_start=None,
            token_end=None,
        ),
        locator_text=f"paragraph {citation_index}",
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        retrieval_tier="corpus_fill",
        fts_rank=citation_index,
        vector_rank=None,
        summary_vector_rank=None,
        rrf_score=f"{1 / (60 + citation_index):.12f}",
        matched_anchors=(),
    )
