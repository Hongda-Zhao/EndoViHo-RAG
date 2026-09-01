"""Self-consistent machine-result contracts for retrieval ablation runs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    HardwareRecord,
    QuestionCategory,
    RecordedModelIdentity,
    TrustStatus,
)
from eve_relation_rag.experiments.embedding_ablation.metrics import (
    LatencySummary,
    QualitySummaryByCategory,
    QuestionMetrics,
    summarize_latency,
    summarize_quality,
)
from eve_relation_rag.experiments.embedding_ablation.source_guard import (
    ProductionSourceFingerprint,
)
from eve_relation_rag.experiments.embedding_ablation.trust import RunTrustDecision
from eve_relation_rag.literature.contracts import (
    ChunkKey,
    CorpusReleaseKey,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class LatencySamples(StrictFrozenSchema):
    """Per-iteration stage timings for one question."""

    embedding_ns: tuple[int, ...] = Field(min_length=1)
    retrieval_ns: tuple[int, ...] = Field(min_length=1)
    reranking_ns: tuple[int, ...] | None
    end_to_end_ns: tuple[int, ...] = Field(min_length=1)

    @field_validator("embedding_ns", "retrieval_ns", "end_to_end_ns")
    @classmethod
    def nonnegative_samples(cls, samples: tuple[int, ...]) -> tuple[int, ...]:
        if any(type(sample) is not int or sample < 0 for sample in samples):
            raise ValueError("latency samples must be non-negative integer nanoseconds")
        return samples

    @field_validator("reranking_ns")
    @classmethod
    def nonnegative_optional_samples(
        cls, samples: tuple[int, ...] | None
    ) -> tuple[int, ...] | None:
        if samples is not None and (
            not samples or any(type(sample) is not int or sample < 0 for sample in samples)
        ):
            raise ValueError("reranking latency samples must be non-empty and non-negative")
        return samples

    @model_validator(mode="after")
    def equal_sample_counts(self) -> Self:
        count = len(self.embedding_ns)
        if len(self.retrieval_ns) != count or len(self.end_to_end_ns) != count:
            raise ValueError("all latency stages must have the same sample count")
        if self.reranking_ns is not None and len(self.reranking_ns) != count:
            raise ValueError("reranking latency sample count does not match")
        return self


class TruncationCounts(StrictFrozenSchema):
    """Explicit query/passage truncation counts for one measured question."""

    embedding_query_count: int = Field(ge=0)
    embedding_query_tokens: int = Field(ge=0)
    reranker_query_count: int = Field(ge=0)
    reranker_query_tokens: int = Field(ge=0)
    reranker_passage_count: int = Field(ge=0)
    reranker_passage_tokens: int = Field(ge=0)


class QuestionExecutionResult(StrictFrozenSchema):
    """Text-free result and measurements for one system/question pair."""

    system_key: str = Field(min_length=1, max_length=128)
    question_id: StableToken
    category: QuestionCategory
    query_sha256: Sha256
    pre_rerank_chunk_keys: tuple[ChunkKey, ...]
    ranked_candidate_chunk_keys: tuple[ChunkKey, ...]
    returned_chunk_keys: tuple[ChunkKey, ...] = Field(max_length=10)
    metrics: QuestionMetrics
    latency: LatencySamples
    truncation: TruncationCounts

    @model_validator(mode="after")
    def validate_metrics_and_candidates(self) -> Self:
        if len(self.pre_rerank_chunk_keys) != len(set(self.pre_rerank_chunk_keys)):
            raise ValueError("pre-rerank candidates must be unique")
        if len(self.ranked_candidate_chunk_keys) != len(set(self.ranked_candidate_chunk_keys)):
            raise ValueError("ranked candidates must be unique")
        if set(self.ranked_candidate_chunk_keys) != set(self.pre_rerank_chunk_keys):
            raise ValueError("reranking must preserve the complete candidate set")
        if len(self.returned_chunk_keys) != len(set(self.returned_chunk_keys)):
            raise ValueError("returned candidates must be unique")
        if self.returned_chunk_keys != self.ranked_candidate_chunk_keys[:10]:
            raise ValueError("returned candidates must be the first ten ranked candidates")
        if (
            self.metrics.question_id != self.question_id
            or self.metrics.category != self.category
            or self.metrics.returned_chunk_keys != self.returned_chunk_keys
        ):
            raise ValueError("question metrics do not match the execution result")
        return self


class SystemLatencySummary(StrictFrozenSchema):
    """Aggregate latency percentiles over every measured question iteration."""

    embedding: LatencySummary
    retrieval: LatencySummary
    reranking: LatencySummary | None
    end_to_end: LatencySummary


class ResourceUsage(StrictFrozenSchema):
    """Measured process/model/index resources and corpus-embedding truncation."""

    peak_process_rss_bytes: int = Field(ge=0)
    peak_accelerator_memory_bytes: int | None = Field(default=None, ge=0)
    embedding_model_size_bytes: int = Field(ge=0)
    reranker_model_size_bytes: int | None = Field(default=None, ge=0)
    index_size_bytes: int = Field(ge=0)
    passage_embedding_truncation_count: int = Field(ge=0)
    passage_embedding_truncated_tokens: int = Field(ge=0)


class SystemExecutionResult(StrictFrozenSchema):
    """All quality, latency, and resource output for one exact system."""

    system: AblationSystem
    question_results: tuple[QuestionExecutionResult, ...] = Field(min_length=1)
    quality: QualitySummaryByCategory
    latency: SystemLatencySummary
    resources: ResourceUsage
    system_result_sha256: Sha256

    @model_validator(mode="after")
    def validate_aggregates_and_hash(self) -> Self:
        question_ids = tuple(result.question_id for result in self.question_results)
        if question_ids != tuple(sorted(question_ids)):
            raise ValueError("system question results must be ordered by question_id")
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("system question results must be unique")
        if any(result.system_key != self.system.system_key for result in self.question_results):
            raise ValueError("question result system_key does not match system")
        expected_quality = summarize_quality(
            tuple(result.metrics for result in self.question_results)
        )
        expected_latency = _summarize_system_latency(self.question_results)
        if self.quality != expected_quality or self.latency != expected_latency:
            raise ValueError("system aggregate metrics do not match question results")
        payload = self.model_dump(mode="python")
        del payload["system_result_sha256"]
        if self.system_result_sha256 != canonical_json_sha256(payload):
            raise ValueError("system result checksum does not match")
        return self


class FailureRecord(StrictFrozenSchema):
    """Text-safe structured failure without query, passage, or document contents."""

    system_key: str = Field(min_length=1, max_length=128)
    question_id: StableToken | None
    stage: Literal[
        "manifest",
        "artifact",
        "snapshot",
        "embedding",
        "retrieval",
        "reranking",
        "metrics",
        "reporting",
    ]
    error_code: StableToken
    message: str = Field(min_length=1, max_length=1000)


class ProviderRecord(StrictFrozenSchema):
    """Serializable projection of trust-gate-issued provider evidence."""

    component: Literal["embedding", "reranker"]
    provider_kind: Literal["verified_local", "deterministic_fake", "unverified"]
    model_key: str
    artifact_manifest_sha256: str
    model_identity: RecordedModelIdentity | None
    reason: str

    @model_validator(mode="after")
    def validate_embedded_identity(self) -> Self:
        if self.model_identity is not None and (
            self.model_identity.model_key != self.model_key
            or self.model_identity.artifact_manifest_sha256
            != self.artifact_manifest_sha256
            or self.model_identity.representation.task_kind != self.component
        ):
            raise ValueError("provider model metadata does not match its runtime identity")
        if self.provider_kind == "verified_local" and self.model_identity is None:
            raise ValueError("verified local provider requires complete model metadata")
        return self


class ExperimentManifest(StrictFrozenSchema):
    """Common immutable inputs shared by every system in one run."""

    experiment_schema_version: Literal["embedding-ablation-experiment-v1"]
    experiment_key: StableToken
    source_commit: str
    source_tree_clean: bool
    production_source_fingerprint: ProductionSourceFingerprint
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    corpus_fingerprint_sha256: Sha256
    annotation_manifest_sha256: Sha256
    gold_sha256: Sha256
    approved_question_count: int = Field(ge=1)
    hardware_record: HardwareRecord
    hardware_record_sha256: Sha256
    warmup_count: int = Field(ge=0)
    measured_iteration_count: int = Field(ge=1)
    offline_model_runtime_enforced: Literal[True]
    systems: tuple[AblationSystem, ...] = Field(min_length=1)
    providers: tuple[ProviderRecord, ...] = Field(min_length=1)
    trust_status: TrustStatus
    trust_reasons: tuple[str, ...]
    experiment_manifest_sha256: Sha256

    @field_validator("source_commit")
    @classmethod
    def exact_source_commit(cls, value: str) -> str:
        if _COMMIT_RE.fullmatch(value) is None:
            raise ValueError("source_commit must be an exact lowercase 40-hex commit")
        return value

    @model_validator(mode="after")
    def validate_identity_and_hashes(self) -> Self:
        system_keys = tuple(system.system_key for system in self.systems)
        if system_keys != tuple(sorted(system_keys)) or len(system_keys) != len(set(system_keys)):
            raise ValueError("experiment systems must be uniquely sorted by system_key")
        if self.hardware_record_sha256 != canonical_json_sha256(self.hardware_record):
            raise ValueError("hardware record checksum does not match")
        if self.trust_status == "trusted" and not self.source_tree_clean:
            raise ValueError("trusted experiment requires a clean source tree")
        if self.trust_status == "trusted" and any(
            provider.provider_kind != "verified_local" for provider in self.providers
        ):
            raise ValueError("trusted experiment requires only verified local providers")
        provider_sort_keys = tuple(
            (provider.component, provider.model_key, provider.artifact_manifest_sha256)
            for provider in self.providers
        )
        if provider_sort_keys != tuple(sorted(provider_sort_keys)) or len(
            provider_sort_keys
        ) != len(set(provider_sort_keys)):
            raise ValueError("experiment providers must be uniquely sorted by identity")
        provider_identities = {
            (provider.component, provider.model_key, provider.artifact_manifest_sha256)
            for provider in self.providers
        }
        required_identities: set[tuple[str, str, str]] = set()
        for system in self.systems:
            required_identities.add(
                (
                    "embedding",
                    system.embedding_model_key,
                    system.embedding_artifact_manifest_sha256,
                )
            )
            required_identities.add(
                (
                    "embedding",
                    system.effective_query_encoder_model_key,
                    system.effective_query_encoder_artifact_manifest_sha256,
                )
            )
            if (
                system.reranker_model_key is not None
                and system.reranker_artifact_manifest_sha256 is not None
            ):
                required_identities.add(
                    (
                        "reranker",
                        system.reranker_model_key,
                        system.reranker_artifact_manifest_sha256,
                    )
                )
        if not required_identities <= provider_identities:
            raise ValueError("experiment providers do not cover every system component")
        provider_by_identity = {
            (provider.component, provider.model_key, provider.artifact_manifest_sha256): provider
            for provider in self.providers
        }
        for system in self.systems:
            passage_provider = provider_by_identity[
                (
                    "embedding",
                    system.embedding_model_key,
                    system.embedding_artifact_manifest_sha256,
                )
            ]
            query_provider = provider_by_identity[
                (
                    "embedding",
                    system.effective_query_encoder_model_key,
                    system.effective_query_encoder_artifact_manifest_sha256,
                )
            ]
            for provider in (passage_provider, query_provider):
                if (
                    provider.model_identity is not None
                    and provider.model_identity.representation.dimension
                    != system.embedding_dimension
                ):
                    raise ValueError("system dimension does not match provider model metadata")
        payload = self.model_dump(mode="python")
        del payload["experiment_manifest_sha256"]
        if self.experiment_manifest_sha256 != canonical_json_sha256(payload):
            raise ValueError("experiment manifest checksum does not match")
        return self


class ExperimentRun(StrictFrozenSchema):
    """One complete machine-result set ready for deterministic serialization."""

    manifest: ExperimentManifest
    system_results: tuple[SystemExecutionResult, ...] = Field(min_length=1)
    failures: tuple[FailureRecord, ...] = ()

    @model_validator(mode="after")
    def validate_system_coverage(self) -> Self:
        observed_systems = tuple(result.system for result in self.system_results)
        if observed_systems != self.manifest.systems:
            raise ValueError("system results do not exactly cover manifest systems")
        if self.manifest.trust_status != "failed" and self.failures:
            raise ValueError("a successful experiment cannot contain failure records")
        expected_question_identity: tuple[tuple[str, QuestionCategory], ...] | None = None
        for result in self.system_results:
            if len(result.question_results) != self.manifest.approved_question_count:
                raise ValueError("system result does not cover every approved question")
            question_identity = tuple(
                (question.question_id, question.category)
                for question in result.question_results
            )
            if expected_question_identity is None:
                expected_question_identity = question_identity
            elif question_identity != expected_question_identity:
                raise ValueError("systems do not cover the same approved questions")
            for question in result.question_results:
                if len(question.latency.embedding_ns) != self.manifest.measured_iteration_count:
                    raise ValueError("question latency sample count does not match the manifest")
                has_reranking_latency = question.latency.reranking_ns is not None
                if has_reranking_latency != (result.system.reranker_model_key is not None):
                    raise ValueError("question reranking latency does not match the system")
        return self


def build_system_execution_result(
    *,
    system: AblationSystem,
    question_results: Sequence[QuestionExecutionResult],
    resources: ResourceUsage,
) -> SystemExecutionResult:
    """Build and checksum one system result after recomputing all aggregates."""

    ordered = tuple(sorted(question_results, key=lambda result: result.question_id))
    payload: dict[str, object] = {
        "system": system,
        "question_results": ordered,
        "quality": summarize_quality(tuple(result.metrics for result in ordered)),
        "latency": _summarize_system_latency(ordered),
        "resources": resources,
    }
    return SystemExecutionResult.model_validate(
        {**payload, "system_result_sha256": canonical_json_sha256(payload)}
    )


def build_experiment_manifest(
    *,
    experiment_key: str,
    source_commit: str,
    source_tree_clean: bool,
    production_source_fingerprint: ProductionSourceFingerprint,
    corpus_release_key: str,
    corpus_manifest_sha256: str,
    corpus_fingerprint_sha256: str,
    annotation_manifest_sha256: str,
    gold_sha256: str,
    approved_question_count: int,
    hardware_record: HardwareRecord,
    warmup_count: int,
    measured_iteration_count: int,
    systems: Sequence[AblationSystem],
    trust_decision: RunTrustDecision,
) -> ExperimentManifest:
    """Build a self-checksummed manifest from a trust-gate-issued decision."""

    if (
        corpus_release_key != trust_decision.corpus_release_key
        or corpus_manifest_sha256 != trust_decision.corpus_manifest_sha256
        or annotation_manifest_sha256 != trust_decision.annotation_manifest_sha256
        or gold_sha256 != trust_decision.gold_sha256
        or approved_question_count != trust_decision.approved_question_count
    ):
        raise ValueError("experiment inputs do not match the trust-gated annotations")
    ordered_systems = tuple(sorted(systems, key=lambda system: system.system_key))
    provider_records = provider_records_from_trust_decision(trust_decision)
    payload: dict[str, object] = {
        "experiment_schema_version": "embedding-ablation-experiment-v1",
        "experiment_key": experiment_key,
        "source_commit": source_commit,
        "source_tree_clean": source_tree_clean,
        "production_source_fingerprint": production_source_fingerprint,
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "corpus_fingerprint_sha256": corpus_fingerprint_sha256,
        "annotation_manifest_sha256": annotation_manifest_sha256,
        "gold_sha256": gold_sha256,
        "approved_question_count": approved_question_count,
        "hardware_record": hardware_record,
        "hardware_record_sha256": canonical_json_sha256(hardware_record),
        "warmup_count": warmup_count,
        "measured_iteration_count": measured_iteration_count,
        "offline_model_runtime_enforced": True,
        "systems": ordered_systems,
        "providers": provider_records,
        "trust_status": trust_decision.status,
        "trust_reasons": trust_decision.reasons,
    }
    return ExperimentManifest.model_validate(
        {**payload, "experiment_manifest_sha256": canonical_json_sha256(payload)}
    )


def provider_records_from_trust_decision(
    trust_decision: RunTrustDecision,
) -> tuple[ProviderRecord, ...]:
    """Project issuer-bound evidence into canonical serializable records."""

    return tuple(
        ProviderRecord(
            component=record.component,
            provider_kind=record.provider_kind,
            model_key=record.model_key,
            artifact_manifest_sha256=record.artifact_manifest_sha256,
            model_identity=record.model_identity,
            reason=record.reason,
        )
        for record in trust_decision.provider_records
    )


def _summarize_system_latency(
    results: Sequence[QuestionExecutionResult],
) -> SystemLatencySummary:
    embedding = tuple(sample for result in results for sample in result.latency.embedding_ns)
    retrieval = tuple(sample for result in results for sample in result.latency.retrieval_ns)
    end_to_end = tuple(sample for result in results for sample in result.latency.end_to_end_ns)
    reranking_rows = tuple(
        result.latency.reranking_ns
        for result in results
        if result.latency.reranking_ns is not None
    )
    if reranking_rows and len(reranking_rows) != len(results):
        raise ValueError("reranking latency must be present for every or no question")
    reranking = tuple(sample for row in reranking_rows for sample in row)
    return SystemLatencySummary(
        embedding=summarize_latency(embedding),
        retrieval=summarize_latency(retrieval),
        reranking=(summarize_latency(reranking) if reranking else None),
        end_to_end=summarize_latency(end_to_end),
    )
