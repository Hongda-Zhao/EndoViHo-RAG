"""Deterministic, I/O-free fixtures for the Phase 2 RAG-value harness.

Nothing in this module is an expert annotation, an approved Oracle entry, or a
production capability.  The fixtures exist only to exercise the experiment
software and are permanently identified as ``synthetic_tests_only``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from eve_relation_rag.application.structured import StructuredQueryApplication
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    AnswerStructuredFacts,
    EvaluationAnswer,
    EvaluationClaim,
    EvidenceCitation,
    EvidenceGroup,
    GenerationIdentity,
    LiteratureGold,
    QuestionFamily,
    RawContextSegment,
    StructuredGold,
    build_generation_identity,
)
from eve_relation_rag.experiments.rag_value_ablation.prompting import PromptPolicy
from eve_relation_rag.generation.rendering import render_structured_answer_text
from eve_relation_rag.hybrid.bindings import ApprovedHybridBindingRegistry
from eve_relation_rag.hybrid.contracts import (
    BINDING_MANIFEST_VERSION,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    canonical_self_sha256,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    EntireReleaseScope,
    ExtractedCondition,
    PlanningAudit,
    canonical_plan_sha256,
)
from eve_relation_rag.planning.resolver import CatalogReleaseResolver, ReleaseScopedEntityResolver
from eve_relation_rag.retrieval.structured.capability import (
    LineageDependencyBinding,
    LineageRole,
    ReleaseCapability,
    SourceDependencyBinding,
)
from eve_relation_rag.retrieval.structured.repository import RepositoryResult
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    Limitation,
    PublishedReleaseRef,
    QuerySuccess,
    StructuredResult,
)
from eve_relation_rag.retrieval.structured.semantic import ValidatedQuery
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService

SYNTHETIC_FIXTURE_STATUS = "synthetic_tests_only"
SYNTHETIC_RELEASE_KEY = "release:endoviho-rag:v0:20990101:001"
SYNTHETIC_RELEASE_MANIFEST_SHA256 = "a" * 64
SYNTHETIC_CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
SYNTHETIC_CORPUS_MANIFEST_SHA256 = "b" * 64
SYNTHETIC_CURSOR_SECRET = b"rag-value-phase2-tests-only-cursor-secret"

DOCUMENT_A = f"document:sha256:{'1' * 64}"
DOCUMENT_B = f"document:sha256:{'2' * 64}"
CHUNK_A = f"chunk:sha256:{'3' * 64}"
CHUNK_B = f"chunk:sha256:{'4' * 64}"


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    """One software-only case; its values must never enter trusted admission."""

    question_id: str
    family: QuestionFamily
    question_text: str
    structured_gold: StructuredGold | None
    literature_gold: LiteratureGold | None
    expected_refusal: bool = False

    @property
    def question_text_sha256(self) -> str:
        return hashlib.sha256(self.question_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SyntheticFixtureManifest:
    """Checksum-bound marker separating software fixtures from human Gold."""

    fixture_schema_version: Literal["rag-value-synthetic-fixture-v1"]
    fixture_status: Literal["synthetic_tests_only"]
    cases: tuple[SyntheticCase, ...]
    fixture_sha256: str

    def __post_init__(self) -> None:
        if self.fixture_schema_version != "rag-value-synthetic-fixture-v1":
            raise ValueError("unsupported synthetic fixture schema")
        if self.fixture_status != SYNTHETIC_FIXTURE_STATUS:
            raise ValueError("synthetic fixture status is invalid")
        question_ids = tuple(case.question_id for case in self.cases)
        if not question_ids or question_ids != tuple(sorted(set(question_ids))):
            raise ValueError("synthetic fixture cases must be nonempty, unique, and ordered")
        if self.fixture_sha256 != canonical_json_sha256(_synthetic_fixture_payload(self.cases)):
            raise ValueError("synthetic fixture checksum does not match")


@dataclass(frozen=True, slots=True)
class SyntheticOracleEvidence:
    """Fixed test evidence, deliberately not an ``OracleEvidenceEntry``."""

    fixture_status: Literal["synthetic_tests_only"]
    question_id: str
    entry_sha256: str
    structured_success: QuerySuccess | None
    citations: tuple[EvidenceCitation, ...]


@dataclass(frozen=True, slots=True)
class SyntheticGenerationRequest:
    """Complete fake-provider request, including every frozen generation setting."""

    system_instruction: str
    user_payload_json: str
    generation_identity: GenerationIdentity
    temperature: int
    max_output_tokens: int
    max_output_bytes: int
    request_sha256: str

    def __post_init__(self) -> None:
        identity = self.generation_identity
        if hashlib.sha256(self.system_instruction.encode("utf-8")).hexdigest() != (
            identity.system_instruction_sha256
        ):
            raise ValueError("synthetic request system instruction differs from identity")
        if self.temperature != identity.temperature:
            raise ValueError("synthetic request temperature differs from identity")
        if self.max_output_tokens != identity.max_output_tokens:
            raise ValueError("synthetic request token limit differs from identity")
        if self.max_output_bytes != identity.max_output_bytes:
            raise ValueError("synthetic request byte limit differs from identity")
        try:
            json.loads(self.user_payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("synthetic user payload is not valid JSON") from exc
        expected_sha256 = canonical_json_sha256(
            {
                "system_instruction": self.system_instruction,
                "user_payload_json": self.user_payload_json,
                "generation_identity": identity,
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
                "max_output_bytes": self.max_output_bytes,
            }
        )
        if self.request_sha256 != expected_sha256:
            raise ValueError("synthetic generation request checksum does not match")


def build_synthetic_generation_request(
    *,
    system_instruction: str,
    user_payload_json: str,
    generation_identity: GenerationIdentity,
) -> SyntheticGenerationRequest:
    """Bind exact prompt bytes and model settings into one provider request."""

    json.loads(user_payload_json)
    values = {
        "system_instruction": system_instruction,
        "user_payload_json": user_payload_json,
        "generation_identity": generation_identity,
        "temperature": generation_identity.temperature,
        "max_output_tokens": generation_identity.max_output_tokens,
        "max_output_bytes": generation_identity.max_output_bytes,
    }
    return SyntheticGenerationRequest(
        system_instruction=system_instruction,
        user_payload_json=user_payload_json,
        generation_identity=generation_identity,
        temperature=generation_identity.temperature,
        max_output_tokens=generation_identity.max_output_tokens,
        max_output_bytes=generation_identity.max_output_bytes,
        request_sha256=canonical_json_sha256(values),
    )


@dataclass(frozen=True, slots=True)
class SyntheticDeterministicOutput:
    """Verifiable S4/S5 structured rendering made from the recorded immutable result."""

    mode: Literal["structured", "structured_first_hybrid"]
    structured_success: QuerySuccess
    structured_text: str
    generated_answer: EvaluationAnswer | None
    output_text: str
    output_sha256: str

    def __post_init__(self) -> None:
        expected_structured = render_structured_answer_text(self.structured_success)
        if self.structured_text != expected_structured:
            raise ValueError("synthetic deterministic structured text is not canonical")
        if self.mode == "structured":
            if self.generated_answer is not None or self.output_text != expected_structured:
                raise ValueError("synthetic S4 output must contain only structured rendering")
        else:
            if self.generated_answer is None:
                raise ValueError("synthetic S5 merge requires a generated answer")
            expected_output = "\n\n".join(
                (
                    "Structured\n" + expected_structured,
                    "Generated\n" + self.generated_answer.model_dump_json(),
                )
            )
            if self.output_text != expected_output:
                raise ValueError("synthetic S5 deterministic merge is not canonical")
        if self.output_sha256 != hashlib.sha256(self.output_text.encode("utf-8")).hexdigest():
            raise ValueError("synthetic deterministic output checksum does not match")


def build_synthetic_deterministic_output(
    *,
    mode: Literal["structured", "structured_first_hybrid"],
    structured_success: QuerySuccess,
    generated_answer: EvaluationAnswer | None = None,
) -> SyntheticDeterministicOutput:
    """Call the production structured renderer, then apply the test-only S5 adapter."""

    structured_text = render_structured_answer_text(structured_success)
    output_text = (
        structured_text
        if mode == "structured"
        else "\n\n".join(
            (
                "Structured\n" + structured_text,
                "Generated\n"
                + (generated_answer.model_dump_json() if generated_answer is not None else ""),
            )
        )
    )
    return SyntheticDeterministicOutput(
        mode=mode,
        structured_success=structured_success,
        structured_text=structured_text,
        generated_answer=generated_answer,
        output_text=output_text,
        output_sha256=hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
    )


@dataclass(slots=True)
class SyntheticReleaseCapability:
    """Structural test double accepted only by injected structured services."""

    release_id: int = 9_999
    dataset_key: Literal["dataset:endoviho-rag"] = "dataset:endoviho-rag"
    release_key: str = SYNTHETIC_RELEASE_KEY
    status: Literal["published", "validation_candidate"] = "published"
    schema_version: str = "synthetic-phase2-v1"
    published_at: datetime = datetime(2099, 1, 1, tzinfo=UTC)
    manifest_sha256: str = SYNTHETIC_RELEASE_MANIFEST_SHA256
    validation_receipt_key: str = "synthetic-tests-only:receipt"
    validation_receipt_sha256: str = "c" * 64
    candidate_validation_input_sha256: str | None = None
    candidate_capability_sha256: str | None = None
    source_dependencies: Mapping[str, SourceDependencyBinding] = field(default_factory=dict)
    lineage_dependencies: Mapping[LineageRole, LineageDependencyBinding] = field(
        default_factory=dict
    )
    complete_lineage_closure_roles: frozenset[LineageRole] = frozenset()


class SyntheticReleaseGate:
    """Recording in-memory release gate."""

    def __init__(self) -> None:
        self.release = SyntheticReleaseCapability()
        self.calls: list[str] = []

    def authorize(self, release_key: str) -> ReleaseCapability:
        self.calls.append(release_key)
        if release_key != self.release.release_key:
            raise ValueError("synthetic release selector mismatch")
        return self.release


class SyntheticResolverFactory:
    """Recording factory for the existing release-scoped resolver contract."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def create(self, release: ReleaseCapability) -> ReleaseScopedEntityResolver:
        self.calls.append(release.release_key)
        return CatalogReleaseResolver(release_key=release.release_key)


class SyntheticFactRepository:
    """Recording repository that returns one fixed typed aggregate."""

    def __init__(self, value: int = 2) -> None:
        self.value = value
        self.calls: list[tuple[ValidatedQuery, tuple[str, ...] | None]] = []

    def query(
        self,
        validated: ValidatedQuery,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> RepositoryResult:
        self.calls.append((validated, page_after))
        return AggregateData(
            metric_key="distinct_included_locus_count",
            value=self.value,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )


@dataclass(frozen=True, slots=True)
class SyntheticStructuredStack:
    """Injected structured application and its observable test doubles."""

    application: StructuredQueryApplication
    gate: SyntheticReleaseGate
    resolver_factory: SyntheticResolverFactory
    repository: SyntheticFactRepository


class DeterministicFakeGenerationProvider:
    """One recording provider whose output depends only on model-visible input."""

    def __init__(
        self,
        identity: GenerationIdentity,
        *,
        call_sink: list[SyntheticGenerationRequest] | None = None,
    ) -> None:
        if identity.provider_kind != "deterministic_fake":
            raise ValueError("synthetic provider requires deterministic_fake identity")
        self._identity = identity
        self._call_sink = call_sink
        self.calls: list[SyntheticGenerationRequest] = []

    @property
    def identity(self) -> GenerationIdentity:
        return self._identity

    def generate(self, request: SyntheticGenerationRequest) -> str:
        if type(request) is not SyntheticGenerationRequest:
            raise TypeError("synthetic provider requires a complete generation request")
        if request.generation_identity != self._identity:
            raise ValueError("synthetic provider request uses a different model identity")
        self.calls.append(request)
        if self._call_sink is not None:
            self._call_sink.append(request)
        payload = json.loads(request.user_payload_json)
        if set(payload) != {"evidence", "instruction"}:
            raise ValueError("synthetic request envelope drifted")
        evidence = payload["evidence"]
        if not isinstance(evidence, dict):
            raise ValueError("synthetic evidence envelope is invalid")

        structured = evidence.get("structured_result")
        literature = evidence.get("literature_evidence")
        raw = evidence.get("raw_context")
        if not isinstance(literature, list) or not isinstance(raw, list):
            raise ValueError("synthetic evidence collections are invalid")

        claims: list[EvaluationClaim] = []
        cited_chunks: list[str] = []
        structured_facts: AnswerStructuredFacts | None = None
        if isinstance(structured, dict):
            data = structured.get("data")
            if isinstance(data, dict) and data.get("kind") == "aggregate":
                value = data.get("value")
                metric_key = data.get("metric_key")
                release = structured.get("release")
                limitations = structured.get("limitations")
                if (
                    not isinstance(value, int)
                    or not isinstance(metric_key, str)
                    or not isinstance(release, dict)
                    or not isinstance(release.get("release_key"), str)
                    or not isinstance(release.get("manifest_sha256"), str)
                    or not isinstance(limitations, list)
                ):
                    raise ValueError("synthetic structured aggregate is invalid")
                limitation_codes = tuple(
                    sorted(
                        limitation["code"]
                        for limitation in limitations
                        if isinstance(limitation, dict) and isinstance(limitation.get("code"), str)
                    )
                )
                structured_facts = AnswerStructuredFacts(
                    exact_count=value,
                    metric_key=metric_key,
                    release_key=release["release_key"],
                    release_manifest_sha256=release["manifest_sha256"],
                    limitation_codes=limitation_codes,
                )
                claims.append(
                    EvaluationClaim(
                        claim_id=f"C{len(claims) + 1}",
                        text=(
                            "The supplied structured result reports "
                            f"{value} distinct included loci."
                        ),
                        claim_type="structured_fact",
                        citation_ids=(),
                    )
                )
        if literature:
            first = literature[0]
            if not isinstance(first, dict):
                raise ValueError("synthetic literature item is invalid")
            citation_id = first.get("citation_id")
            chunk_key = first.get("chunk_key")
            if not isinstance(citation_id, str) or not isinstance(chunk_key, str):
                raise ValueError("synthetic citation identity is invalid")
            claims.append(
                EvaluationClaim(
                    claim_id=f"C{len(claims) + 1}",
                    text="The supplied synthetic passage reports a test association.",
                    claim_type="literature_fact",
                    citation_ids=(citation_id,),
                )
            )
            cited_chunks.append(chunk_key)
        elif raw:
            first_raw = raw[0]
            if not isinstance(first_raw, dict):
                raise ValueError("synthetic raw-context item is invalid")
            segment_id = first_raw.get("segment_id")
            source_kind = first_raw.get("source_kind")
            if not isinstance(segment_id, str):
                raise ValueError("synthetic raw segment identity is invalid")
            claims.append(
                EvaluationClaim(
                    claim_id=f"C{len(claims) + 1}",
                    text="The supplied raw export reports two distinct included loci.",
                    claim_type=(
                        "structured_fact"
                        if source_kind == "structured_export"
                        else "literature_fact"
                    ),
                    citation_ids=(segment_id,),
                )
            )
            if source_kind == "structured_export":
                structured_facts = AnswerStructuredFacts(
                    exact_count=2,
                    metric_key="distinct_included_locus_count",
                )

        answer = (
            EvaluationAnswer(
                answer_text=" ".join(claim.text for claim in claims),
                abstained=False,
                claims=tuple(claims),
                structured_facts=structured_facts,
                limitations=(),
                cited_chunk_ids=tuple(cited_chunks),
            )
            if claims
            else EvaluationAnswer(
                answer_text="The supplied evidence is insufficient.",
                abstained=True,
                claims=(),
                limitations=("The supplied evidence is insufficient.",),
                cited_chunk_ids=(),
            )
        )
        return answer.model_dump_json()


class SyntheticRankProvider:
    """Recording, branch-specific in-memory rank provider."""

    def __init__(self, branch: str, ranked_chunk_keys: tuple[str, ...]) -> None:
        self.branch = branch
        self.ranked_chunk_keys = ranked_chunk_keys
        self.calls: list[str] = []

    def rank(self, question: str) -> tuple[str, ...]:
        self.calls.append(question)
        return self.ranked_chunk_keys


class SyntheticOracleLoader:
    """Recording loader over fixed synthetic entries only."""

    def __init__(
        self,
        entries: tuple[SyntheticOracleEvidence, ...] = (),
        *,
        entry_factory: Callable[[str], SyntheticOracleEvidence] | None = None,
    ) -> None:
        if bool(entries) == (entry_factory is not None):
            raise ValueError("synthetic Oracle loader requires entries or one lazy factory")
        self._entries = {entry.question_id: entry for entry in entries}
        self._entry_factory = entry_factory
        self.calls: list[str] = []

    def load(self, question_id: str) -> SyntheticOracleEvidence:
        self.calls.append(question_id)
        if self._entry_factory is not None:
            entry = self._entry_factory(question_id)
            if entry.question_id != question_id:
                raise ValueError("synthetic Oracle factory returned a different question")
            return entry
        return self._entries[question_id]


def build_synthetic_generation_identity(policy: PromptPolicy) -> GenerationIdentity:
    """Build the sole fake generation identity used by every LLM condition."""

    return build_generation_identity(
        provider_key="provider:rag-value:deterministic-fake-v1",
        provider_kind="deterministic_fake",
        model_id="synthetic/rag-value-provider",
        exact_revision="1" * 40,
        model_artifact_manifest_sha256="2" * 64,
        tokenizer_id="synthetic/utf8-byte-tokenizer",
        tokenizer_revision="3" * 40,
        tokenizer_artifact_manifest_sha256="4" * 64,
        system_instruction_sha256=policy.system_instruction_sha256,
        request_template_sha256=policy.request_template_sha256,
        output_schema_sha256=policy.output_schema_sha256,
        temperature=0,
        max_output_tokens=4096,
        max_output_bytes=16_384,
        context_limit_tokens=32_768,
        timeout_seconds=5,
        retry_count=0,
        request_concurrency=1,
        seed=7,
        tools_enabled=False,
        web_enabled=False,
        conversation_memory_enabled=False,
    )


def build_synthetic_structured_stack(
    *,
    value: int = 2,
    repository: SyntheticFactRepository | None = None,
) -> SyntheticStructuredStack:
    """Compose production structured contracts over I/O-free test doubles."""

    gate = SyntheticReleaseGate()
    resolver_factory = SyntheticResolverFactory()
    repository = SyntheticFactRepository(value) if repository is None else repository
    retrieval = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=SYNTHETIC_CURSOR_SECRET,
    )
    application = StructuredQueryApplication(
        gate=gate,
        resolver_factory=resolver_factory,
        retrieval=retrieval,
    )
    return SyntheticStructuredStack(
        application=application,
        gate=gate,
        resolver_factory=resolver_factory,
        repository=repository,
    )


def run_synthetic_structured_query(
    question_text: str,
    *,
    stack: SyntheticStructuredStack | None = None,
) -> tuple[QuerySuccess, SyntheticStructuredStack]:
    """Execute one supported controlled-English aggregate over the fake stack."""

    stack = build_synthetic_structured_stack() if stack is None else stack
    response = stack.application.query(
        StructuredQueryRequest(
            release_key=SYNTHETIC_RELEASE_KEY,
            question=question_text,
        )
    )
    if not isinstance(response, QuerySuccess):
        raise ValueError("synthetic structured question did not produce QuerySuccess")
    if len(stack.repository.calls) != 1:
        raise ValueError("synthetic structured query did not execute exactly once")
    return response, stack


def synthetic_citations(chunk_keys: tuple[str, ...]) -> tuple[EvidenceCitation, ...]:
    """Hydrate fixed synthetic chunk keys into response-local citations."""

    source = {
        CHUNK_A: (
            DOCUMENT_A,
            "Synthetic source A reports a Transferred gene test association.",
        ),
        CHUNK_B: (
            DOCUMENT_B,
            "Synthetic source B reports an Integrated virus test association.",
        ),
    }
    citations: list[EvidenceCitation] = []
    for index, chunk_key in enumerate(chunk_keys, start=1):
        document_key, text = source[chunk_key]
        citations.append(
            EvidenceCitation(
                citation_id=f"D{index}",
                document_key=document_key,
                chunk_key=chunk_key,
                locator_text=f"synthetic paragraph {index}",
                text=text,
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(citations)


def synthetic_raw_segments() -> tuple[RawContextSegment, ...]:
    """Return the fixed S1 structured-first raw export order."""

    values: tuple[tuple[Literal["structured_export", "document"], str, str], ...] = (
        (
            "structured_export",
            "synthetic-source:structured-export",
            "The synthetic release contains 2 distinct included loci.",
        ),
        (
            "document",
            "synthetic-source:document-a",
            "Synthetic source A reports a Transferred gene test association.",
        ),
        (
            "document",
            "synthetic-source:document-b",
            "Synthetic source B reports an Integrated virus test association.",
        ),
    )
    return tuple(
        RawContextSegment(
            segment_id=f"R{index}",
            source_kind=source_kind,
            source_key=source_key,
            source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            byte_start=0,
            byte_end=len(text.encode("utf-8")),
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        for index, (source_kind, source_key, text) in enumerate(values, start=1)
    )


def _default_synthetic_cases() -> tuple[SyntheticCase, ...]:
    structured_gold = StructuredGold(
        exact_count=2,
        metric_key="distinct_included_locus_count",
        release_key=SYNTHETIC_RELEASE_KEY,
        release_manifest_sha256=SYNTHETIC_RELEASE_MANIFEST_SHA256,
        required_limitation_codes=("assembly_local_locus_is_not_independent_integration_event",),
    )
    literature_gold = LiteratureGold(
        required_document_keys=(DOCUMENT_A, DOCUMENT_B),
        evidence_groups=(
            EvidenceGroup(
                group_id="synthetic-evidence-a",
                required_document_key=DOCUMENT_A,
                required_chunk_key=CHUNK_A,
            ),
            EvidenceGroup(
                group_id="synthetic-evidence-b",
                required_document_key=DOCUMENT_B,
                required_chunk_key=CHUNK_B,
            ),
        ),
        required_concepts=("Integrated virus", "Transferred gene"),
    )
    structured_question = "Count distinct included loci in this release."
    return (
        SyntheticCase(
            question_id="synthetic-hybrid-001",
            family="hybrid",
            question_text=structured_question,
            structured_gold=structured_gold,
            literature_gold=literature_gold,
        ),
        SyntheticCase(
            question_id="synthetic-literature-001",
            family="literature",
            question_text="Which association does the supplied synthetic literature report?",
            structured_gold=None,
            literature_gold=literature_gold,
        ),
        SyntheticCase(
            question_id="synthetic-structured-001",
            family="structured",
            question_text=structured_question,
            structured_gold=structured_gold,
            literature_gold=None,
        ),
        SyntheticCase(
            question_id="synthetic-unsupported-evidence-001",
            family="unsupported",
            question_text="Which source passage supports the missing synthetic association?",
            structured_gold=None,
            literature_gold=None,
            expected_refusal=True,
        ),
        SyntheticCase(
            question_id="synthetic-unsupported-policy-001",
            family="unsupported",
            question_text="Run HMMER on this new sequence.",
            structured_gold=None,
            literature_gold=None,
            expected_refusal=True,
        ),
    )


def build_synthetic_fixture_manifest(
    cases: tuple[SyntheticCase, ...] | None = None,
) -> SyntheticFixtureManifest:
    """Build checksum-bound software cases with no human approval fields."""

    cases = _default_synthetic_cases() if cases is None else cases
    if not cases:
        raise ValueError("synthetic fixture manifest requires at least one case")
    question_ids = tuple(case.question_id for case in cases)
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("synthetic fixture question IDs must be unique")
    if question_ids != tuple(sorted(question_ids)):
        raise ValueError("synthetic fixture cases must use canonical question-ID order")
    payload = _synthetic_fixture_payload(cases)
    return SyntheticFixtureManifest(
        fixture_schema_version="rag-value-synthetic-fixture-v1",
        fixture_status="synthetic_tests_only",
        cases=cases,
        fixture_sha256=canonical_json_sha256(payload),
    )


def validate_synthetic_fixture_manifest(
    value: SyntheticFixtureManifest,
) -> SyntheticFixtureManifest:
    """Recompute an exact fixture identity before publication authority is issued."""

    if type(value) is not SyntheticFixtureManifest:
        raise TypeError("an exact SyntheticFixtureManifest is required")
    if (
        value.fixture_schema_version != "rag-value-synthetic-fixture-v1"
        or value.fixture_status != SYNTHETIC_FIXTURE_STATUS
    ):
        raise ValueError("synthetic fixture version or status is invalid")
    if len(value.cases) != 5:
        raise ValueError("Phase 2 requires exactly five synthetic cases")
    case_ids = tuple(case.question_id for case in value.cases)
    if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
        raise ValueError("synthetic cases must have canonical unique IDs")
    if tuple(sorted(case.family for case in value.cases)) != (
        "hybrid",
        "literature",
        "structured",
        "unsupported",
        "unsupported",
    ):
        raise ValueError(
            "synthetic fixture requires one answerable case per family and two refusal cases"
        )
    if any(case.expected_refusal != (case.family == "unsupported") for case in value.cases):
        raise ValueError("only the unsupported synthetic case may require refusal")
    if value.fixture_sha256 != canonical_json_sha256(_synthetic_fixture_payload(value.cases)):
        raise ValueError("synthetic fixture checksum does not match")
    return value


def _synthetic_fixture_payload(cases: tuple[SyntheticCase, ...]) -> dict[str, object]:
    return {
        "fixture_schema_version": "rag-value-synthetic-fixture-v1",
        "fixture_status": SYNTHETIC_FIXTURE_STATUS,
        "cases": tuple(
            {
                "question_id": case.question_id,
                "family": case.family,
                "question_text": case.question_text,
                "question_text_sha256": case.question_text_sha256,
                "structured_gold": case.structured_gold,
                "literature_gold": case.literature_gold,
                "expected_refusal": case.expected_refusal,
            }
            for case in cases
        ),
    }


def build_synthetic_oracle_entries(
    manifest: SyntheticFixtureManifest,
) -> tuple[SyntheticOracleEvidence, ...]:
    """Build fixed test Oracle inputs without claiming human review."""

    return tuple(build_synthetic_oracle_entry(case) for case in manifest.cases)


def build_synthetic_oracle_entry(case: SyntheticCase) -> SyntheticOracleEvidence:
    """Materialize exactly one test-only Oracle entry on an admitted S6 request."""

    if case.expected_refusal:
        empty_payload: dict[str, object] = {
            "fixture_status": SYNTHETIC_FIXTURE_STATUS,
            "question_id": case.question_id,
            "question_text_sha256": case.question_text_sha256,
            "structured_result_sha256": None,
            "chunk_keys": (),
        }
        return SyntheticOracleEvidence(
            fixture_status="synthetic_tests_only",
            question_id=case.question_id,
            entry_sha256=canonical_json_sha256(empty_payload),
            structured_success=None,
            citations=(),
        )
    structured_success = None
    if case.structured_gold is not None:
        structured_success = _synthetic_oracle_structured_success(case.question_text)
    citations = synthetic_citations((CHUNK_A, CHUNK_B))
    payload: dict[str, object] = {
        "fixture_status": SYNTHETIC_FIXTURE_STATUS,
        "question_id": case.question_id,
        "question_text_sha256": case.question_text_sha256,
        "structured_result_sha256": (
            None
            if structured_success is None
            else canonical_json_sha256(structured_success.structured_result.model_dump(mode="json"))
        ),
        "chunk_keys": tuple(citation.chunk_key for citation in citations),
    }
    return SyntheticOracleEvidence(
        fixture_status="synthetic_tests_only",
        question_id=case.question_id,
        entry_sha256=canonical_json_sha256(payload),
        structured_success=structured_success,
        citations=citations,
    )


def _synthetic_oracle_structured_success(question_text: str) -> QuerySuccess:
    """Deserialize one fixed typed Oracle fixture without invoking a retrieval route."""

    expected_question = "Count distinct included loci in this release."
    if question_text != expected_question:
        raise ValueError("synthetic Oracle has no fixed structured entry for this question")
    plan = AggregatePlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=SYNTHETIC_RELEASE_KEY,
        original_question=question_text,
        scope=EntireReleaseScope(
            scope_type="entire_release",
            explicitly_requested=True,
        ),
        intent="aggregate",
        metric_key="distinct_included_locus_count",
    )
    conditions = (
        ExtractedCondition(
            source_text="Count",
            source_start=0,
            source_end=5,
            condition_id="condition:001:intent",
            condition_kind="intent",
            mapped_target="intent:aggregate",
        ),
        ExtractedCondition(
            source_text="distinct included loci",
            source_start=6,
            source_end=28,
            condition_id="condition:002:metric",
            condition_kind="metric",
            mapped_target="metric_key:distinct_included_locus_count",
        ),
        ExtractedCondition(
            source_text="in this release",
            source_start=29,
            source_end=44,
            condition_id="condition:003:scope",
            condition_kind="scope",
            mapped_target="scope:entire_release",
        ),
    )
    audit = PlanningAudit(
        extracted_conditions=conditions,
        mapped_condition_ids=tuple(condition.condition_id for condition in conditions),
    )
    structured_result = StructuredResult(
        plan_sha256=canonical_plan_sha256(plan),
        release=PublishedReleaseRef(
            dataset_key="dataset:endoviho-rag",
            release_key=SYNTHETIC_RELEASE_KEY,
            schema_version="synthetic-phase2-v1",
            status="published",
            manifest_sha256=SYNTHETIC_RELEASE_MANIFEST_SHA256,
            published_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        data=AggregateData(
            metric_key="distinct_included_locus_count",
            value=2,
            unit="loci",
            deduplication_key="release_key+locus_key",
        ),
        limitations=(
            Limitation(
                code="assembly_local_locus_is_not_independent_integration_event",
                message=(
                    "An assembly-local locus is not evidence of an independent integration event."
                ),
            ),
        ),
    )
    return QuerySuccess(
        query_plan=plan,
        planning_audit=audit,
        structured_result=structured_result,
        fact_retrieval_executed=True,
    )


def authorize_synthetic_hybrid_binding() -> tuple[HybridReleaseBinding, str]:
    """Exercise the production immutable binding registry with a test-only pair."""

    binding = HybridReleaseBinding(
        release_key=SYNTHETIC_RELEASE_KEY,
        release_manifest_sha256=SYNTHETIC_RELEASE_MANIFEST_SHA256,
        corpus_release_key=SYNTHETIC_CORPUS_KEY,
        corpus_manifest_sha256=SYNTHETIC_CORPUS_MANIFEST_SHA256,
    )
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (binding,),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    manifest = HybridReleaseBindingManifest.model_validate(payload)
    registry = ApprovedHybridBindingRegistry(manifest)
    authorized = registry.authorize(SYNTHETIC_RELEASE_KEY, SYNTHETIC_CORPUS_KEY)
    if authorized != binding:
        raise ValueError("synthetic hybrid binding registry returned a different pair")
    return authorized, registry.manifest_sha256


__all__ = [
    "CHUNK_A",
    "CHUNK_B",
    "DOCUMENT_A",
    "DOCUMENT_B",
    "SYNTHETIC_CORPUS_KEY",
    "SYNTHETIC_CORPUS_MANIFEST_SHA256",
    "SYNTHETIC_FIXTURE_STATUS",
    "SYNTHETIC_RELEASE_KEY",
    "SYNTHETIC_RELEASE_MANIFEST_SHA256",
    "DeterministicFakeGenerationProvider",
    "SyntheticDeterministicOutput",
    "SyntheticCase",
    "SyntheticFixtureManifest",
    "SyntheticGenerationRequest",
    "SyntheticOracleEvidence",
    "SyntheticOracleLoader",
    "SyntheticRankProvider",
    "SyntheticStructuredStack",
    "authorize_synthetic_hybrid_binding",
    "build_synthetic_deterministic_output",
    "build_synthetic_fixture_manifest",
    "build_synthetic_generation_identity",
    "build_synthetic_generation_request",
    "build_synthetic_oracle_entry",
    "build_synthetic_oracle_entries",
    "run_synthetic_structured_query",
    "synthetic_citations",
    "synthetic_raw_segments",
    "validate_synthetic_fixture_manifest",
]
