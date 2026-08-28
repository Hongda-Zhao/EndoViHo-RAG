"""Checksum-bound deterministic M4 mechanical generation benchmark."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.domain.keys import canonical_json_sha256
from eve_relation_rag.generation.composer import GenerationComposer, GenerationComposerError
from eve_relation_rag.generation.context import build_hybrid_context, canonical_context_json
from eve_relation_rag.generation.rendering import render_hybrid_answer_text
from eve_relation_rag.hybrid.contracts import RagQueryRequest
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.rendering import render_structured_result_text
from tests.support.m4 import (
    TEST_CORPUS_RELEASE_KEY,
    DeterministicGenerationProvider,
    StructuredVariant,
    make_generated_draft,
    make_provider_identity,
    make_retrieved_chunks,
    make_structured_success,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "m4" / "generation_cases.json"
TEST_RELEASE_KEY = "release:endoviho-rag:v0:20260827:002"

type Scenario = Literal[
    "valid",
    "prompt_injection_chunk",
    "unknown_citation",
    "wrong_quote",
    "invented_identifier",
    "wrong_context_hash",
    "malformed_provider_output",
    "provider_identity_mismatch",
    "unsupported_refusal",
]
type ExpectedStatus = Literal["accepted", "rejected", "unsupported"]
type ExpectedError = Literal[
    "answer_validation_failed",
    "generated_draft_invalid",
    "llm_provider_unavailable",
    "unsupported_request",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenerationBenchmarkTargets(_FrozenModel):
    structured_values_and_identifiers_unchanged_percent: Literal[100]
    document_claims_with_current_citations_percent: Literal[100]
    exact_evidence_spans_percent: Literal[100]
    invented_identifier_accept_count: Literal[0]
    unsupported_refusal_percent: Literal[100]
    unsupported_downstream_call_count: Literal[0]


class GenerationBenchmarkCase(_FrozenModel):
    case_id: str = Field(pattern=r"^(?:hybrid|unsupported)-[a-z0-9-]+-[0-9]{2}$")
    route: Literal["hybrid", "unsupported"]
    structured_variant: StructuredVariant | None
    question: str = Field(min_length=1, max_length=2000)
    scenario: Scenario
    chunk_text: str | None
    claim_text: str | None
    evidence_citation_id: str | None
    evidence_quote: str | None
    expected_identifiers: tuple[str, ...]
    expected_status: ExpectedStatus
    expected_error: ExpectedError | None

    @model_validator(mode="after")
    def validate_case_shape(self) -> Self:
        generated_fields = (
            self.structured_variant,
            self.chunk_text,
        )
        if self.route == "unsupported":
            if self.scenario != "unsupported_refusal" or self.expected_status != "unsupported":
                raise ValueError("unsupported case must be an unsupported_refusal")
            if any(value is not None for value in generated_fields):
                raise ValueError("unsupported case cannot contain generation inputs")
            if any(
                value is not None
                for value in (
                    self.claim_text,
                    self.evidence_citation_id,
                    self.evidence_quote,
                )
            ):
                raise ValueError("unsupported case cannot contain a generated claim")
            return self

        if any(value is None for value in generated_fields):
            raise ValueError("hybrid case requires a structured variant and chunk")
        if self.scenario == "malformed_provider_output":
            if any(
                value is not None
                for value in (
                    self.claim_text,
                    self.evidence_citation_id,
                    self.evidence_quote,
                )
            ):
                raise ValueError("malformed output case cannot contain parsed draft fields")
        elif any(
            value is None
            for value in (
                self.claim_text,
                self.evidence_citation_id,
                self.evidence_quote,
            )
        ):
            raise ValueError("draft-producing hybrid case requires one complete claim")
        if self.expected_status == "accepted" and self.expected_error is not None:
            raise ValueError("accepted case cannot declare an error")
        if self.expected_status == "rejected" and self.expected_error is None:
            raise ValueError("rejected case requires an error")
        return self


class GenerationBenchmark(_FrozenModel):
    benchmark_schema_version: Literal["m4-generation-benchmark-v1"]
    case_count: int = Field(ge=11)
    hybrid_case_count: int = Field(ge=10)
    required_structured_variants: tuple[StructuredVariant, ...]
    target_metrics: GenerationBenchmarkTargets
    cases: tuple[GenerationBenchmarkCase, ...]
    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_counts_coverage_and_checksum(self) -> Self:
        if self.case_count != len(self.cases):
            raise ValueError("case_count does not match cases")
        observed_hybrid_count = sum(case.route == "hybrid" for case in self.cases)
        if self.hybrid_case_count != observed_hybrid_count:
            raise ValueError("hybrid_case_count does not match cases")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("generation benchmark case IDs must be unique")
        required_variants: set[StructuredVariant] = {
            "aggregate",
            "assembly_detail",
            "assembly_page",
            "locus_detail",
            "locus_page",
            "source_taxon_page",
        }
        if set(self.required_structured_variants) != required_variants:
            raise ValueError("required_structured_variants must name all six variants")
        observed_variants = {
            case.structured_variant for case in self.cases if case.structured_variant is not None
        }
        if observed_variants != required_variants:
            raise ValueError("generation cases do not cover all six structured variants")
        required_scenarios: set[Scenario] = {
            "valid",
            "prompt_injection_chunk",
            "unknown_citation",
            "wrong_quote",
            "invented_identifier",
            "wrong_context_hash",
            "malformed_provider_output",
            "unsupported_refusal",
        }
        if not required_scenarios.issubset({case.scenario for case in self.cases}):
            raise ValueError("generation cases omit an approved adversarial scenario")
        payload = self.model_dump(mode="python")
        del payload["benchmark_sha256"]
        if canonical_json_sha256(payload) != self.benchmark_sha256:
            raise ValueError("benchmark_sha256 does not match canonical benchmark payload")
        return self


class GenerationBenchmarkReport(_FrozenModel):
    structured_values_and_identifiers_unchanged_percent: int
    document_claims_with_current_citations_percent: int
    exact_evidence_spans_percent: int
    invented_identifier_accept_count: int
    unsupported_refusal_percent: int
    unsupported_downstream_call_count: int
    scenario_counts: dict[str, int]


def _load_benchmark() -> GenerationBenchmark:
    return GenerationBenchmark.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


def _percent(passed: int, total: int) -> int:
    assert total > 0
    return passed * 100 // total


def test_checksum_bound_generation_benchmark_meets_all_mechanical_gates() -> None:
    benchmark = _load_benchmark()
    expected_identity = make_provider_identity()
    structured_preserved = 0
    current_citations = 0
    exact_spans = 0
    accepted_claim_count = 0
    invented_identifier_accept_count = 0
    unsupported_refusals = 0
    unsupported_count = 0
    unsupported_downstream_calls = 0

    for case in benchmark.cases:
        if case.route == "unsupported":
            unsupported_count += 1
            recording_provider = DeterministicGenerationProvider(
                identity=expected_identity,
                output="provider must not be called",
            )
            request = RagQueryRequest(
                release_key=TEST_RELEASE_KEY,
                corpus_release_key=TEST_CORPUS_RELEASE_KEY,
                question=case.question,
            )
            decision = DeterministicRouter().route(request)
            assert decision.route == "unsupported", case.case_id
            assert decision.refusal_code == case.expected_error, case.case_id
            unsupported_refusals += 1
            unsupported_downstream_calls += len(recording_provider.calls)
            continue

        assert case.structured_variant is not None
        assert case.chunk_text is not None
        routed = DeterministicRouter().route(
            RagQueryRequest(
                release_key=TEST_RELEASE_KEY,
                corpus_release_key=TEST_CORPUS_RELEASE_KEY,
                question=case.question,
            )
        )
        assert routed.route == "hybrid", case.case_id
        assert routed.structured_question is not None
        query_success = make_structured_success(
            case.structured_variant,
            structured_question=routed.structured_question,
        )
        retrieved = make_retrieved_chunks(question=case.question, text=case.chunk_text)
        context = build_hybrid_context(
            original_question=case.question,
            query_success=query_success,
            retrieved_chunks=retrieved,
        )
        assert context.query_plan == query_success.query_plan, case.case_id
        assert context.structured_result == query_success.structured_result, case.case_id
        structured_preserved += 1

        if case.scenario == "malformed_provider_output":
            output = "{not-json"
        else:
            assert case.claim_text is not None
            assert case.evidence_citation_id is not None
            assert case.evidence_quote is not None
            draft_context_sha256 = (
                "f" * 64 if case.scenario == "wrong_context_hash" else context.context_sha256
            )
            output = make_generated_draft(
                context_sha256=draft_context_sha256,
                claim_text=case.claim_text,
                citation_id=case.evidence_citation_id,
                evidence_quote=case.evidence_quote,
            ).model_dump_json()

        observed_identity = (
            make_provider_identity(model_revision="revision:tests:m4-unapproved")
            if case.scenario == "provider_identity_mismatch"
            else expected_identity
        )
        provider = DeterministicGenerationProvider(identity=observed_identity, output=output)
        composer = GenerationComposer(provider=provider, expected_identity=expected_identity)

        if case.expected_status == "accepted":
            composition = composer.compose(context)
            assert len(provider.calls) == 1, case.case_id
            assert provider.calls[0] == canonical_context_json(context), case.case_id
            assert composition.context_sha256 == context.context_sha256, case.case_id
            assert len(composition.claims) == 1, case.case_id
            accepted_claim_count += 1
            claim = composition.claims[0]
            current_ids = {chunk.citation_id for chunk in retrieved.chunks}
            assert set(claim.citation_ids).issubset(current_ids), case.case_id
            current_citations += 1
            by_id = {chunk.citation_id: chunk for chunk in retrieved.chunks}
            assert all(
                evidence.quote in by_id[evidence.citation_id].text
                for evidence in claim.evidence_spans
            ), case.case_id
            exact_spans += 1
            for identifier in case.expected_identifiers:
                assert identifier in claim.claim_text, case.case_id
                assert identifier in canonical_context_json(context), case.case_id
                assert identifier in composition.literature_text, case.case_id
            hybrid_text = render_hybrid_answer_text(
                query_success.structured_result,
                composition,
            )
            assert render_structured_result_text(query_success.structured_result) in hybrid_text
            continue

        try:
            composer.compose(context)
        except GenerationComposerError as error:
            assert error.code == case.expected_error, case.case_id
            if case.scenario == "provider_identity_mismatch":
                assert error.generation_executed is False, case.case_id
                assert provider.calls == [], case.case_id
            else:
                assert error.generation_executed is True, case.case_id
                assert len(provider.calls) == 1, case.case_id
        else:
            if case.scenario == "invented_identifier":
                invented_identifier_accept_count += 1
            raise AssertionError(f"{case.case_id} unexpectedly passed mechanical validation")

    report = GenerationBenchmarkReport(
        structured_values_and_identifiers_unchanged_percent=_percent(
            structured_preserved,
            benchmark.hybrid_case_count,
        ),
        document_claims_with_current_citations_percent=_percent(
            current_citations,
            accepted_claim_count,
        ),
        exact_evidence_spans_percent=_percent(exact_spans, accepted_claim_count),
        invented_identifier_accept_count=invented_identifier_accept_count,
        unsupported_refusal_percent=_percent(unsupported_refusals, unsupported_count),
        unsupported_downstream_call_count=unsupported_downstream_calls,
        scenario_counts=dict(Counter(case.scenario for case in benchmark.cases)),
    )

    assert report.model_dump(exclude={"scenario_counts"}) == benchmark.target_metrics.model_dump()


def test_generation_benchmark_identity_is_stable() -> None:
    first = _load_benchmark()
    second = _load_benchmark()

    assert first == second
    assert first.benchmark_sha256 == (
        "538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa"
    )
