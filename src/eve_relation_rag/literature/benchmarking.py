"""Checksum-frozen deterministic and pilot literature retrieval benchmarks."""

from __future__ import annotations

import hashlib
import platform
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator
from sqlalchemy import Engine

from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    ChunkKey,
    CorpusReleaseKey,
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
    QuestionText,
    RetrievalAnchor,
    RetrievedChunks,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256, canonical_query_sha256

_METRIC_QUANTUM = Decimal("0.000000000001")


class BenchmarkValidationError(ValueError):
    """Raised when a benchmark report is not exact or self-consistent."""


class BenchmarkQuestion(StrictFrozenSchema):
    """One fixed English question with non-empty exact relevant chunk keys."""

    question_key: StableToken
    question: QuestionText
    anchors: tuple[RetrievalAnchor, ...] = ()
    relevant_chunk_keys: tuple[ChunkKey, ...] = Field(min_length=1)

    @field_validator("relevant_chunk_keys")
    @classmethod
    def canonical_relevant_keys(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if len(keys) != len(set(keys)):
            raise ValueError("relevant_chunk_keys must be unique")
        return tuple(sorted(keys))


class BenchmarkDefinition(StrictFrozenSchema):
    """Self-checksummed benchmark input frozen to one corpus and policy graph."""

    benchmark_schema_version: Literal["literature-benchmark-v1"]
    tier: Literal["deterministic_ci", "pilot_release"]
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    retrieval_policy_key: Literal[
        "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
    ]
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ]
    question_count: int = Field(ge=1)
    gold_sha256: Sha256
    benchmark_manifest_sha256: Sha256
    questions: tuple[BenchmarkQuestion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_hashes_and_counts(self) -> Self:
        if self.question_count != len(self.questions):
            raise ValueError("question_count does not match questions")
        if len({question.question_key for question in self.questions}) != len(self.questions):
            raise ValueError("question_key values must be unique")
        expected_gold = canonical_json_sha256(self.questions)
        if self.gold_sha256 != expected_gold:
            raise ValueError("gold_sha256 does not match benchmark questions")
        if self.benchmark_manifest_sha256 != canonical_json_sha256(
            _definition_payload(self, include_manifest_hash=False)
        ):
            raise ValueError("benchmark_manifest_sha256 does not match definition")
        return self


class BenchmarkQuestionResult(StrictFrozenSchema):
    """Auditable per-question retrieval result and recall values."""

    question_key: StableToken
    query_sha256: Sha256 | None
    status: Literal["ok", "error"]
    error_code: str | None
    returned_chunk_keys: tuple[ChunkKey, ...]
    recall_at_5: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    recall_at_10: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    citation_ids_valid: bool
    locators_valid: bool


class BenchmarkRuntimeFingerprint(StrictFrozenSchema):
    """Explicit runtime identity recorded for a release-grade pilot benchmark."""

    runtime_schema_version: Literal["literature-benchmark-runtime-v1"] = (
        "literature-benchmark-runtime-v1"
    )
    python_version: str = Field(min_length=1, max_length=128)
    platform_system: str = Field(min_length=1, max_length=128)
    platform_release: str = Field(min_length=1, max_length=512)
    platform_machine: str = Field(min_length=1, max_length=128)
    uv_lock_sha256: Sha256
    postgresql_version: str = Field(min_length=1, max_length=512)
    pgvector_version: str = Field(min_length=1, max_length=128)

    @field_validator(
        "python_version",
        "platform_system",
        "platform_release",
        "platform_machine",
        "postgresql_version",
        "pgvector_version",
    )
    @classmethod
    def canonical_runtime_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("runtime fingerprint text must not contain surrounding whitespace")
        return value


class BenchmarkReport(StrictFrozenSchema):
    """Aggregate fixed-corpus benchmark report with publication thresholds."""

    report_schema_version: Literal["literature-benchmark-report-v1"]
    tier: Literal["deterministic_ci", "pilot_release"]
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    retrieval_policy_key: Literal[
        "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
    ]
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ]
    gold_sha256: Sha256
    question_count: int = Field(ge=1)
    benchmark_manifest_sha256: Sha256
    runtime_fingerprint: BenchmarkRuntimeFingerprint | None = None
    passed: bool
    recall_at_5: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    recall_at_10: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    citation_id_validity: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    locator_validity: str = Field(pattern=r"^(?:0|1)\.[0-9]{12}$")
    question_results: tuple[BenchmarkQuestionResult, ...]
    benchmark_sha256: Sha256

    @model_validator(mode="after")
    def validate_report_integrity(self) -> Self:
        if self.question_count != len(self.question_results):
            raise ValueError("question_count does not match question_results")
        question_keys = tuple(result.question_key for result in self.question_results)
        if len(question_keys) != len(set(question_keys)):
            raise ValueError("benchmark report contains duplicate question keys")
        if self.tier == "pilot_release" and self.runtime_fingerprint is None:
            raise ValueError("pilot-release benchmark requires a runtime fingerprint")

        for result in self.question_results:
            if result.status == "ok":
                if result.query_sha256 is None or result.error_code is not None:
                    raise ValueError("successful benchmark result has inconsistent status fields")
            elif (
                result.query_sha256 is not None
                or not result.error_code
                or result.returned_chunk_keys
                or result.recall_at_5 != "0.000000000000"
                or result.recall_at_10 != "0.000000000000"
                or result.citation_ids_valid
                or result.locators_valid
            ):
                raise ValueError("failed benchmark result has inconsistent status fields")

        recall_at_5 = _mean(result.recall_at_5 for result in self.question_results)
        recall_at_10 = _mean(result.recall_at_10 for result in self.question_results)
        citation_validity = _mean(
            "1.000000000000" if result.citation_ids_valid else "0.000000000000"
            for result in self.question_results
        )
        locator_validity = _mean(
            "1.000000000000" if result.locators_valid else "0.000000000000"
            for result in self.question_results
        )
        if (
            self.recall_at_5 != recall_at_5
            or self.recall_at_10 != recall_at_10
            or self.citation_id_validity != citation_validity
            or self.locator_validity != locator_validity
        ):
            raise ValueError("benchmark aggregate metrics do not match question results")

        passed = _passes_release_gates(
            self.question_results,
            recall_at_5=recall_at_5,
            recall_at_10=recall_at_10,
            citation_validity=citation_validity,
            locator_validity=locator_validity,
        )
        if self.passed != passed:
            raise ValueError("benchmark passed flag does not match release gates")
        if self.benchmark_sha256 != canonical_json_sha256(_report_payload(self)):
            raise ValueError("benchmark_sha256 does not match the complete report")
        return self


class LiteratureRetriever(Protocol):
    """Minimal benchmark boundary implemented by published or validated-candidate services."""

    def retrieve(
        self, invocation: LiteratureRetrievalInvocation
    ) -> RetrievedChunks | LiteratureRetrievalError: ...


def collect_benchmark_runtime_fingerprint(
    engine: Engine,
    *,
    uv_lock_path: Path,
) -> BenchmarkRuntimeFingerprint:
    """Capture the exact local runtime used by a release-grade benchmark."""

    if uv_lock_path.is_symlink() or not uv_lock_path.is_file():
        raise BenchmarkValidationError("uv.lock is unavailable or is a symbolic link")
    try:
        lock_sha256 = hashlib.sha256(uv_lock_path.read_bytes()).hexdigest()
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            postgresql_version = str(
                connection.exec_driver_sql("SELECT version()").scalar_one()
            )
            pgvector_version = str(
                connection.exec_driver_sql(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                ).scalar_one()
            )
    except Exception as exc:
        raise BenchmarkValidationError(
            "benchmark runtime fingerprint could not be established"
        ) from exc
    return BenchmarkRuntimeFingerprint(
        python_version=platform.python_version(),
        platform_system=platform.system(),
        platform_release=platform.release(),
        platform_machine=platform.machine(),
        uv_lock_sha256=lock_sha256,
        postgresql_version=postgresql_version,
        pgvector_version=pgvector_version,
    )


def build_benchmark_definition(
    *,
    tier: Literal["deterministic_ci", "pilot_release"],
    corpus_release_key: str,
    corpus_manifest_sha256: str,
    questions: Sequence[BenchmarkQuestion],
) -> BenchmarkDefinition:
    """Build a canonical self-checksummed definition from already typed gold questions."""

    question_tuple = tuple(questions)
    gold_sha256 = canonical_json_sha256(question_tuple)
    provisional = {
        "benchmark_schema_version": "literature-benchmark-v1",
        "tier": tier,
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "retrieval_policy_key": RETRIEVAL_POLICY_KEY,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "question_count": len(question_tuple),
        "gold_sha256": gold_sha256,
        "questions": question_tuple,
    }
    return BenchmarkDefinition(
        benchmark_schema_version="literature-benchmark-v1",
        tier=tier,
        corpus_release_key=corpus_release_key,
        corpus_manifest_sha256=corpus_manifest_sha256,
        retrieval_policy_key=RETRIEVAL_POLICY_KEY,
        embedding_model_key=EMBEDDING_MODEL_KEY,
        question_count=len(question_tuple),
        gold_sha256=gold_sha256,
        benchmark_manifest_sha256=canonical_json_sha256(provisional),
        questions=question_tuple,
    )


def run_benchmark(
    retriever: LiteratureRetriever,
    definition: BenchmarkDefinition,
    *,
    runtime_fingerprint: BenchmarkRuntimeFingerprint | None = None,
) -> BenchmarkReport:
    """Run every question at top-10 and enforce the approved aggregate gates."""

    if definition.tier == "pilot_release" and runtime_fingerprint is None:
        raise BenchmarkValidationError(
            "pilot-release benchmark requires an explicit runtime fingerprint"
        )

    results: list[BenchmarkQuestionResult] = []
    for gold in definition.questions:
        invocation = LiteratureRetrievalInvocation(
            request=LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=definition.corpus_release_key,
                question=gold.question,
                top_k=10,
            ),
            system_anchors=gold.anchors,
        )
        response = retriever.retrieve(invocation)
        if isinstance(response, LiteratureRetrievalError):
            results.append(_failed_question_result(gold.question_key, response.code))
            continue

        expected_query_sha256 = canonical_query_sha256(
            invocation.request, invocation.system_anchors
        )
        if (
            response.corpus_release_key != definition.corpus_release_key
            or response.corpus_manifest_sha256 != definition.corpus_manifest_sha256
            or response.retrieval_policy_key != definition.retrieval_policy_key
            or response.embedding_model_key != definition.embedding_model_key
            or response.requested_top_k != 10
            or not response.retrieval_executed
            or response.query_sha256 != expected_query_sha256
        ):
            results.append(
                _failed_question_result(
                    gold.question_key,
                    "benchmark_response_identity_mismatch",
                )
            )
            continue

        returned = tuple(chunk.chunk_key for chunk in response.chunks)
        expected_citations = tuple(f"D{index}" for index in range(1, len(response.chunks) + 1))
        observed_citations = tuple(chunk.citation_id for chunk in response.chunks)
        results.append(
            BenchmarkQuestionResult(
                question_key=gold.question_key,
                query_sha256=response.query_sha256,
                status="ok",
                error_code=None,
                returned_chunk_keys=returned,
                recall_at_5=_recall(returned[:5], gold.relevant_chunk_keys),
                recall_at_10=_recall(returned[:10], gold.relevant_chunk_keys),
                citation_ids_valid=(
                    observed_citations == expected_citations
                    and len(observed_citations) == len(set(observed_citations))
                ),
                locators_valid=all(
                    bool(chunk.locator_text) and bool(chunk.text) for chunk in response.chunks
                ),
            )
        )

    recall_at_5 = _mean(result.recall_at_5 for result in results)
    recall_at_10 = _mean(result.recall_at_10 for result in results)
    citation_validity = _mean(
        "1.000000000000" if result.citation_ids_valid else "0.000000000000" for result in results
    )
    locator_validity = _mean(
        "1.000000000000" if result.locators_valid else "0.000000000000" for result in results
    )
    passed = _passes_release_gates(
        results,
        recall_at_5=recall_at_5,
        recall_at_10=recall_at_10,
        citation_validity=citation_validity,
        locator_validity=locator_validity,
    )
    report_payload = {
        "report_schema_version": "literature-benchmark-report-v1",
        "tier": definition.tier,
        "corpus_release_key": definition.corpus_release_key,
        "corpus_manifest_sha256": definition.corpus_manifest_sha256,
        "retrieval_policy_key": definition.retrieval_policy_key,
        "embedding_model_key": definition.embedding_model_key,
        "gold_sha256": definition.gold_sha256,
        "question_count": definition.question_count,
        "benchmark_manifest_sha256": definition.benchmark_manifest_sha256,
        "runtime_fingerprint": runtime_fingerprint,
        "passed": passed,
        "recall_at_5": recall_at_5,
        "recall_at_10": recall_at_10,
        "citation_id_validity": citation_validity,
        "locator_validity": locator_validity,
        "question_results": tuple(results),
    }
    report = BenchmarkReport.model_validate(
        {
            **report_payload,
            "benchmark_sha256": canonical_json_sha256(report_payload),
        }
    )
    validate_benchmark_report_against_definition(report, definition)
    return report


def validate_benchmark_report_against_definition(
    report: BenchmarkReport,
    definition: BenchmarkDefinition,
) -> None:
    """Recompute question-level evidence and bind a report to one exact definition."""

    identity_pairs = (
        (report.tier, definition.tier),
        (report.corpus_release_key, definition.corpus_release_key),
        (report.corpus_manifest_sha256, definition.corpus_manifest_sha256),
        (report.retrieval_policy_key, definition.retrieval_policy_key),
        (report.embedding_model_key, definition.embedding_model_key),
        (report.gold_sha256, definition.gold_sha256),
        (report.question_count, definition.question_count),
        (report.benchmark_manifest_sha256, definition.benchmark_manifest_sha256),
    )
    if any(observed != expected for observed, expected in identity_pairs):
        raise BenchmarkValidationError(
            "benchmark report does not bind the exact benchmark definition"
        )

    observed_keys = tuple(result.question_key for result in report.question_results)
    expected_keys = tuple(question.question_key for question in definition.questions)
    if observed_keys != expected_keys:
        raise BenchmarkValidationError(
            "benchmark report question order does not match the definition"
        )

    for result, gold in zip(report.question_results, definition.questions, strict=True):
        expected_recall_at_5 = _recall(
            result.returned_chunk_keys[:5], gold.relevant_chunk_keys
        )
        expected_recall_at_10 = _recall(
            result.returned_chunk_keys[:10], gold.relevant_chunk_keys
        )
        if (
            result.recall_at_5 != expected_recall_at_5
            or result.recall_at_10 != expected_recall_at_10
        ):
            raise BenchmarkValidationError(
                f"benchmark recall does not match gold for {gold.question_key}"
            )
        if result.status == "ok":
            request = LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=definition.corpus_release_key,
                question=gold.question,
                top_k=10,
            )
            expected_query_sha256 = canonical_query_sha256(request, gold.anchors)
            if result.query_sha256 != expected_query_sha256:
                raise BenchmarkValidationError(
                    f"benchmark query identity mismatch for {gold.question_key}"
                )


def _failed_question_result(question_key: str, error_code: str) -> BenchmarkQuestionResult:
    return BenchmarkQuestionResult(
        question_key=question_key,
        query_sha256=None,
        status="error",
        error_code=error_code,
        returned_chunk_keys=(),
        recall_at_5="0.000000000000",
        recall_at_10="0.000000000000",
        citation_ids_valid=False,
        locators_valid=False,
    )


def _passes_release_gates(
    results: Sequence[BenchmarkQuestionResult],
    *,
    recall_at_5: str,
    recall_at_10: str,
    citation_validity: str,
    locator_validity: str,
) -> bool:
    return (
        Decimal(recall_at_5) >= Decimal("0.80")
        and Decimal(recall_at_10) >= Decimal("0.90")
        and citation_validity == "1.000000000000"
        and locator_validity == "1.000000000000"
        and all(result.status == "ok" for result in results)
    )


def _report_payload(report: BenchmarkReport) -> dict[str, object]:
    payload = report.model_dump(mode="python")
    del payload["benchmark_sha256"]
    return payload


def _recall(returned: Sequence[str], relevant: Sequence[str]) -> str:
    value = Decimal(len(set(returned) & set(relevant))) / Decimal(len(relevant))
    return _metric(value)


def _mean(values: Iterable[str]) -> str:
    materialized: tuple[str, ...] = tuple(values)
    value = sum((Decimal(item) for item in materialized), start=Decimal(0)) / Decimal(
        len(materialized)
    )
    return _metric(value)


def _metric(value: Decimal) -> str:
    return f"{value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"


def _definition_payload(
    definition: BenchmarkDefinition,
    *,
    include_manifest_hash: bool,
) -> dict[str, object]:
    payload = definition.model_dump(mode="python")
    if not include_manifest_hash:
        del payload["benchmark_manifest_sha256"]
    return payload
