"""Strict, immutable Milestone 4 routed-RAG and generation contracts.

The models in this module contain no routing, database, provider, or rendering logic.  They
establish the only values those layers may exchange and make every execution-relevant identity
canonical and checksum-bound.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from eve_relation_rag.literature.contracts import (
    CanonicalLocator,
    ChunkKey,
    CorpusReleaseKey,
    DocumentKey,
    Doi,
    LiteratureErrorCode,
    LiteratureRetrievalRequest,
    Pmcid,
    Pmid,
    RetrievedChunks,
    Sha256,
    StableToken,
)
from eve_relation_rag.literature.hashing import (
    canonical_json_bytes,
    canonical_json_sha256,
    canonical_query_sha256,
)
from eve_relation_rag.planning.query_plans import (
    PageSpec,
    PublishedReleaseKey,
    StructuredPlan,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.results import ErrorCode, QuerySuccess, StructuredResult

RAG_QUERY_REQUEST_VERSION: Final = "rag-query-request-v1"
ROUTE_DECISION_VERSION: Final = "rag-route-decision-v1"
BINDING_MANIFEST_VERSION: Final = "hybrid-release-binding-manifest-v1"
CONTEXT_PACK_VERSION: Final = "context-pack-v1"
ANSWER_INSTRUCTIONS_VERSION: Final = "answer-instructions-v1"
GENERATED_DRAFT_VERSION: Final = "generated-answer-draft-v1"
GENERATION_COMPOSITION_VERSION: Final = "generation-composition-v1"

MAX_CONTEXT_BYTES: Final = 131_072
MAX_CONTEXT_CHUNKS: Final = 8
MAX_GENERATED_CLAIMS: Final = 16
MAX_GENERATED_OUTPUT_BYTES: Final = 32_768
HYBRID_SUFFIXES: Final = (
    " and explain the literature evidence",
    " and explain the literature methods",
    " and explain the literature limitations",
)

_CLAIM_ID_RE: Final = re.compile(r"^C[1-9][0-9]*$")
_CITATION_ID_RE: Final = re.compile(r"^D[1-9][0-9]*$")
_STRUCTURED_UPSTREAM_CODE_ADAPTER: Final[TypeAdapter[ErrorCode]] = TypeAdapter(ErrorCode)
_LITERATURE_UPSTREAM_CODE_ADAPTER: Final[TypeAdapter[LiteratureErrorCode]] = TypeAdapter(
    LiteratureErrorCode
)


class StrictFrozenSchema(BaseModel):
    """Strict immutable base shared by all M4 public and internal contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def canonical_model_json(value: object) -> str:
    """Return the project canonical JSON representation as Unicode text."""

    return canonical_json_bytes(_json_compatible(value)).decode("utf-8")


def canonical_model_sha256(value: object) -> str:
    """Return the SHA-256 identity of the project canonical JSON representation."""

    return canonical_json_sha256(_json_compatible(value))


def _json_compatible(value: object) -> object:
    """Recursively use Pydantic JSON mode before the shared canonical encoder.

    M2 public release references contain timezone-aware datetimes.  Pydantic's Python-mode
    representation intentionally preserves them, while canonical JSON must receive their stable
    RFC 3339 strings.
    """

    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {key: _json_compatible(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(child) for child in value]
    return value


def canonical_self_sha256(
    value: BaseModel | Mapping[str, object],
    digest_field: str,
) -> str:
    """Hash a self-identifying object after excluding exactly its digest field."""

    payload: dict[str, Any]
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = dict(value)
    if digest_field not in payload:
        raise ValueError(f"payload is missing self-digest field {digest_field}")
    del payload[digest_field]
    return canonical_model_sha256(payload)


def _validate_ascii_line(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("text must contain non-whitespace ASCII characters")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise ValueError("text must be one printable ASCII line")
    return value


def _validate_non_empty_text(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("text must contain non-whitespace characters")
    return value


AsciiQuestion = Annotated[
    str,
    Field(min_length=1, max_length=2000),
    AfterValidator(_validate_ascii_line),
]
AsciiClaimText = Annotated[
    str,
    Field(min_length=1, max_length=1000),
    AfterValidator(_validate_ascii_line),
]
AsciiEvidenceQuote = Annotated[
    str,
    Field(min_length=1, max_length=500),
    AfterValidator(_validate_ascii_line),
]
NonEmptyText = Annotated[str, Field(min_length=1), AfterValidator(_validate_non_empty_text)]
ClaimId = Annotated[str, Field(pattern=_CLAIM_ID_RE.pattern)]
CitationId = Annotated[str, Field(pattern=_CITATION_ID_RE.pattern)]

type RagRoute = Literal["structured", "literature", "hybrid", "unsupported"]
type RouteRefusalCode = Literal["unsupported_request", "route_request_mismatch"]


class RagQueryRequest(StrictFrozenSchema):
    """The only client-authored M4 request; route and trusted objects are absent."""

    request_schema_version: Literal["rag-query-request-v1"] = RAG_QUERY_REQUEST_VERSION
    release_key: PublishedReleaseKey | None = None
    corpus_release_key: CorpusReleaseKey | None = None
    question: AsciiQuestion
    page: PageSpec | None = None
    literature_top_k: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def validate_selector_fields(self) -> Self:
        if self.page is not None and self.release_key is None:
            raise ValueError("page is accepted only when a structured release is requested")
        if self.literature_top_k is not None and self.corpus_release_key is None:
            raise ValueError("literature_top_k is accepted only when a corpus is requested")
        return self


class RouteDecision(StrictFrozenSchema):
    """Pure server-owned routing result consumed by orchestration."""

    route_schema_version: Literal["rag-route-decision-v1"] = ROUTE_DECISION_VERSION
    route: RagRoute
    original_question: AsciiQuestion
    structured_question: AsciiQuestion | None
    literature_question: AsciiQuestion | None
    effective_literature_top_k: int | None = Field(default=None, ge=1, le=8)
    refusal_code: RouteRefusalCode | None

    @model_validator(mode="after")
    def validate_route_shape(self) -> Self:
        if self.route == "structured":
            if (
                self.structured_question != self.original_question
                or self.literature_question is not None
                or self.effective_literature_top_k is not None
                or self.refusal_code is not None
            ):
                raise ValueError("structured route fields are inconsistent")
        elif self.route == "literature":
            if (
                self.structured_question is not None
                or self.literature_question is None
                or self.effective_literature_top_k is None
                or self.refusal_code is not None
            ):
                raise ValueError("literature route fields are inconsistent")
        elif self.route == "hybrid":
            if (
                self.structured_question is None
                or self.literature_question is None
                or self.effective_literature_top_k is None
                or self.refusal_code is not None
            ):
                raise ValueError("hybrid route fields are inconsistent")
        elif (
            self.structured_question is not None
            or self.literature_question is not None
            or self.effective_literature_top_k is not None
            or self.refusal_code is None
        ):
            raise ValueError("unsupported route fields are inconsistent")
        return self


class HybridReleaseBinding(StrictFrozenSchema):
    """One exact immutable DatasetRelease-to-CorpusRelease authorization pair."""

    release_key: PublishedReleaseKey
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256


class HybridReleaseBindingManifest(StrictFrozenSchema):
    """Checksum-bound local allowlist for exact hybrid release pairs."""

    binding_schema_version: Literal["hybrid-release-binding-manifest-v1"] = BINDING_MANIFEST_VERSION
    bindings: tuple[HybridReleaseBinding, ...] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        identities = tuple(
            (binding.release_key, binding.corpus_release_key) for binding in self.bindings
        )
        if len(identities) != len(set(identities)):
            raise ValueError("hybrid binding manifest contains a duplicate release pair")
        if identities != tuple(sorted(identities)):
            raise ValueError("hybrid binding entries must use canonical pair order")
        release_manifests: dict[str, str] = {}
        corpus_manifests: dict[str, str] = {}
        for binding in self.bindings:
            observed_release = release_manifests.setdefault(
                binding.release_key,
                binding.release_manifest_sha256,
            )
            if observed_release != binding.release_manifest_sha256:
                raise ValueError("one structured release key has conflicting manifest identities")
            observed_corpus = corpus_manifests.setdefault(
                binding.corpus_release_key,
                binding.corpus_manifest_sha256,
            )
            if observed_corpus != binding.corpus_manifest_sha256:
                raise ValueError("one corpus release key has conflicting manifest identities")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("manifest_sha256 does not match the canonical binding manifest")
        return self


class AnswerInstructions(StrictFrozenSchema):
    """Exact prompt-policy text admitted to a ContextPack."""

    instruction_schema_version: Literal["answer-instructions-v1"] = ANSWER_INSTRUCTIONS_VERSION
    instruction_policy_key: StableToken
    source_text: NonEmptyText
    source_text_sha256: Sha256

    @model_validator(mode="after")
    def validate_source_hash(self) -> Self:
        observed = hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()
        if self.source_text_sha256 != observed:
            raise ValueError("source_text_sha256 does not match answer instruction text")
        return self


class ContextPack(StrictFrozenSchema):
    """The complete and only factual value admitted to an LLM provider."""

    context_schema_version: Literal["context-pack-v1"] = CONTEXT_PACK_VERSION
    route: Literal["literature", "hybrid"]
    original_question: AsciiQuestion
    query_plan: StructuredPlan | None
    structured_result: StructuredResult | None
    retrieved_chunks: RetrievedChunks
    answer_instructions: AnswerInstructions
    context_sha256: Sha256

    @model_validator(mode="after")
    def validate_context_integrity(self) -> Self:
        if len(self.retrieved_chunks.chunks) > MAX_CONTEXT_CHUNKS:
            raise ValueError("ContextPack contains more than eight chunks")
        if self.retrieved_chunks.requested_top_k > MAX_CONTEXT_CHUNKS:
            raise ValueError("ContextPack retrieval top_k exceeds eight")
        if self.route == "literature":
            if self.query_plan is not None or self.structured_result is not None:
                raise ValueError("literature ContextPack forbids structured facts")
        else:
            if self.query_plan is None or self.structured_result is None:
                raise ValueError("hybrid ContextPack requires plan and structured result")
            if self.structured_result.plan_sha256 != canonical_plan_sha256(self.query_plan):
                raise ValueError("structured result does not match the ContextPack plan")
            if self.structured_result.release.release_key != self.query_plan.release_key:
                raise ValueError("structured release does not match the ContextPack plan")
            if not _hybrid_question_matches_plan(
                self.original_question,
                self.query_plan.original_question,
            ):
                raise ValueError("hybrid question does not match the ContextPack plan")
        expected_query_sha256 = canonical_query_sha256(
            LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=self.retrieved_chunks.corpus_release_key,
                question=self.original_question,
                top_k=self.retrieved_chunks.requested_top_k,
            ),
            self.retrieved_chunks.anchors_applied,
        )
        if self.retrieved_chunks.query_sha256 != expected_query_sha256:
            raise ValueError("retrieved chunks do not match the ContextPack question")
        if self.context_sha256 != canonical_self_sha256(self, "context_sha256"):
            raise ValueError("context_sha256 does not match ContextPack")
        if len(canonical_model_json(self).encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise ValueError("canonical ContextPack exceeds 131072 UTF-8 bytes")
        return self


def _hybrid_question_matches_plan(original_question: str, structured_question: str) -> bool:
    folded = original_question.casefold()
    return any(
        folded.endswith(suffix) and original_question[: -len(suffix)] == structured_question
        for suffix in HYBRID_SUFFIXES
    )


class ProviderIdentity(StrictFrozenSchema):
    """Complete server-pinned runtime identity for an LLM provider."""

    provider_key: StableToken
    model_key: StableToken
    model_revision: StableToken
    provider_artifact_sha256: Sha256 | None
    generation_policy_key: StableToken
    prompt_policy_key: StableToken
    prompt_policy_sha256: Sha256
    temperature: Literal[0] = 0
    max_output_bytes: Literal[32768] = MAX_GENERATED_OUTPUT_BYTES
    timeout_seconds: int = Field(ge=1, le=300)
    retry_count: Literal[0] = 0


class EvidenceSpan(StrictFrozenSchema):
    """One provider-selected quote bound to one current-response citation."""

    citation_id: CitationId
    quote: AsciiEvidenceQuote


class LiteratureClaim(StrictFrozenSchema):
    """One atomic generated document claim before deterministic rendering."""

    claim_id: ClaimId
    claim_text: AsciiClaimText
    citation_ids: tuple[CitationId, ...] = Field(min_length=1, max_length=4)
    evidence_spans: tuple[EvidenceSpan, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_claim_shape(self) -> Self:
        if self.claim_text[-1] not in ".?!":
            raise ValueError("claim_text must be a complete English sentence")
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("claim citation IDs must be unique")
        citation_numbers = tuple(int(value[1:]) for value in self.citation_ids)
        if citation_numbers != tuple(sorted(citation_numbers)):
            raise ValueError("claim citation IDs must use numeric canonical order")
        span_ids = tuple(span.citation_id for span in self.evidence_spans)
        if span_ids != self.citation_ids:
            raise ValueError("evidence_spans must contain exactly one span per cited chunk")
        return self


type GeneratedLimitationCode = Literal[
    "insufficient_literature_evidence",
    "literature_evidence_is_explanatory",
    "mechanical_validation_is_not_semantic_entailment",
]


class GeneratedAnswerDraft(StrictFrozenSchema):
    """The only JSON object an LLM provider may return."""

    draft_schema_version: Literal["generated-answer-draft-v1"] = GENERATED_DRAFT_VERSION
    context_sha256: Sha256
    claims: tuple[LiteratureClaim, ...] = Field(max_length=MAX_GENERATED_CLAIMS)
    selected_limitation_codes: tuple[GeneratedLimitationCode, ...]

    @model_validator(mode="after")
    def validate_draft_order(self) -> Self:
        expected_claim_ids = tuple(f"C{index}" for index in range(1, len(self.claims) + 1))
        observed_claim_ids = tuple(claim.claim_id for claim in self.claims)
        if observed_claim_ids != expected_claim_ids:
            raise ValueError("claim IDs must be contiguous C1..Cn")
        if len(self.selected_limitation_codes) != len(set(self.selected_limitation_codes)):
            raise ValueError("selected limitation codes must be unique")
        if self.selected_limitation_codes != tuple(sorted(self.selected_limitation_codes)):
            raise ValueError("selected limitation codes must use canonical order")
        required = {
            "literature_evidence_is_explanatory",
            "mechanical_validation_is_not_semantic_entailment",
        }
        if not required.issubset(self.selected_limitation_codes):
            raise ValueError("generated draft is missing required mechanical limitations")
        insufficient = "insufficient_literature_evidence" in self.selected_limitation_codes
        if bool(self.claims) == insufficient:
            raise ValueError(
                "insufficient_literature_evidence is required exactly when claims are empty"
            )
        return self


class AnswerCitation(StrictFrozenSchema):
    """System-copied citation provenance used by deterministic answer rendering."""

    citation_id: CitationId
    chunk_key: ChunkKey
    document_key: DocumentKey
    title: NonEmptyText
    doi: Doi | None
    pmid: Pmid | None
    pmcid: Pmcid | None
    section: NonEmptyText | None
    locator: CanonicalLocator
    locator_text: NonEmptyText
    text_sha256: Sha256


class GenerationComposition(StrictFrozenSchema):
    """Mechanically validated generation artifact returned by the pure composer."""

    composition_schema_version: Literal["generation-composition-v1"] = (
        GENERATION_COMPOSITION_VERSION
    )
    context_sha256: Sha256
    provider_identity: ProviderIdentity
    claims: tuple[LiteratureClaim, ...] = Field(max_length=MAX_GENERATED_CLAIMS)
    selected_limitation_codes: tuple[GeneratedLimitationCode, ...]
    citations: tuple[AnswerCitation, ...] = Field(max_length=MAX_CONTEXT_CHUNKS)
    literature_text: NonEmptyText
    literature_text_sha256: Sha256
    validation_scope: Literal["mechanical"] = "mechanical"

    @model_validator(mode="after")
    def validate_composition_hash(self) -> Self:
        observed = hashlib.sha256(self.literature_text.encode("utf-8")).hexdigest()
        if observed != self.literature_text_sha256:
            raise ValueError("literature_text_sha256 does not match rendered text")
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if citation_ids != tuple(sorted(citation_ids, key=lambda value: int(value[1:]))):
            raise ValueError("answer citations must use numeric citation order")
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("answer citations must be unique")
        return self


class ExecutionFlags(StrictFrozenSchema):
    """Actual execution calls, independent from the requested route."""

    structured_retrieval_executed: bool
    literature_retrieval_executed: bool
    generation_executed: bool


type AnchorDiagnostic = Literal["structured_anchor_unmatched"]


class StructuredRouteAnswer(StrictFrozenSchema):
    response_schema_version: Literal["structured-route-answer-v1"] = "structured-route-answer-v1"
    response_kind: Literal["structured_route_answer"] = "structured_route_answer"
    route: Literal["structured"] = "structured"
    original_request: RagQueryRequest
    query_success: QuerySuccess
    structured_text: NonEmptyText
    execution: ExecutionFlags

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.original_request.release_key != self.query_success.query_plan.release_key:
            raise ValueError("structured answer release does not match request")
        if self.original_request.question != self.query_success.query_plan.original_question:
            raise ValueError("structured answer question does not match request")
        if self.execution != ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=False,
            generation_executed=False,
        ):
            raise ValueError("structured answer execution flags are inconsistent")
        return self


class LiteratureRouteAnswer(StrictFrozenSchema):
    response_schema_version: Literal["literature-answer-v1"] = "literature-answer-v1"
    response_kind: Literal["literature_answer"] = "literature_answer"
    route: Literal["literature"] = "literature"
    original_request: RagQueryRequest
    retrieved_chunks: RetrievedChunks
    generation: GenerationComposition
    answer_text: NonEmptyText
    answer_sha256: Sha256
    execution: ExecutionFlags

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.original_request.corpus_release_key != self.retrieved_chunks.corpus_release_key:
            raise ValueError("literature answer corpus does not match request")
        if self.original_request.release_key is not None:
            raise ValueError("literature answer forbids a structured release")
        if self.execution != ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=True,
            generation_executed=True,
        ):
            raise ValueError("literature answer execution flags are inconsistent")
        if self.answer_sha256 != hashlib.sha256(self.answer_text.encode("utf-8")).hexdigest():
            raise ValueError("answer_sha256 does not match answer_text")
        return self


class HybridRouteAnswer(StrictFrozenSchema):
    response_schema_version: Literal["hybrid-answer-v1"] = "hybrid-answer-v1"
    response_kind: Literal["hybrid_answer"] = "hybrid_answer"
    route: Literal["hybrid"] = "hybrid"
    original_request: RagQueryRequest
    query_success: QuerySuccess
    retrieved_chunks: RetrievedChunks
    anchor_diagnostics: tuple[AnchorDiagnostic, ...]
    generation: GenerationComposition | None
    insufficient_evidence_limitation: Literal["insufficient_literature_evidence"] | None = None
    answer_text: NonEmptyText
    answer_sha256: Sha256
    execution: ExecutionFlags

    @model_validator(mode="after")
    def validate_response(self) -> Self:
        if self.original_request.release_key != self.query_success.query_plan.release_key:
            raise ValueError("hybrid structured release does not match request")
        if self.original_request.corpus_release_key != self.retrieved_chunks.corpus_release_key:
            raise ValueError("hybrid corpus does not match request")
        if len(self.anchor_diagnostics) != len(set(self.anchor_diagnostics)):
            raise ValueError("anchor diagnostics must be unique")
        if self.anchor_diagnostics != tuple(sorted(self.anchor_diagnostics)):
            raise ValueError("anchor diagnostics must use canonical order")
        if self.generation is None:
            if self.retrieved_chunks.chunks:
                raise ValueError("hybrid answer may omit generation only when no chunks exist")
            if self.insufficient_evidence_limitation != "insufficient_literature_evidence":
                raise ValueError("zero-chunk hybrid answer requires its explicit limitation")
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=True,
                generation_executed=False,
            )
        else:
            if not self.retrieved_chunks.chunks:
                raise ValueError("hybrid generated answer requires retrieved chunks")
            if self.insufficient_evidence_limitation is not None:
                raise ValueError("generated hybrid answer forbids an insufficiency limitation")
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=True,
                generation_executed=True,
            )
        if self.execution != expected_execution:
            raise ValueError("hybrid answer execution flags are inconsistent")
        if self.answer_sha256 != hashlib.sha256(self.answer_text.encode("utf-8")).hexdigest():
            raise ValueError("answer_sha256 does not match answer_text")
        return self


type RagErrorCode = Literal[
    "request_schema_invalid",
    "unsupported_request",
    "route_request_mismatch",
    "structured_refused",
    "literature_refused",
    "hybrid_binding_unavailable",
    "anchor_integrity_error",
    "anchor_limit_exceeded",
    "insufficient_evidence",
    "context_integrity_error",
    "context_too_large",
    "llm_provider_unavailable",
    "generation_failed",
    "generated_draft_invalid",
    "answer_validation_failed",
    "internal_error",
]


class RagErrorResponse(StrictFrozenSchema):
    response_schema_version: Literal["rag-error-v1"] = "rag-error-v1"
    response_kind: Literal["error"] = "error"
    route: RagRoute | None
    requested_release_key: PublishedReleaseKey | None
    requested_corpus_release_key: CorpusReleaseKey | None
    code: RagErrorCode
    message: AsciiQuestion
    upstream_code: StableToken | None
    execution: ExecutionFlags

    @model_validator(mode="after")
    def validate_error_envelope(self) -> Self:
        upstream_codes_allowed = self.code in {"structured_refused", "literature_refused"}
        if self.upstream_code is not None and not upstream_codes_allowed:
            raise ValueError("upstream_code is allowed only for an upstream refusal")
        if self.upstream_code is not None:
            adapter = (
                _STRUCTURED_UPSTREAM_CODE_ADAPTER
                if self.code == "structured_refused"
                else _LITERATURE_UPSTREAM_CODE_ADAPTER
            )
            try:
                adapter.validate_python(self.upstream_code, strict=True)
            except ValidationError:
                raise ValueError(
                    "upstream_code is not a stable code from the selected upstream contract"
                ) from None

        executed = self.execution
        if self.code in {
            "request_schema_invalid",
            "unsupported_request",
            "route_request_mismatch",
        } and (
            executed.structured_retrieval_executed
            or executed.literature_retrieval_executed
            or executed.generation_executed
        ):
            raise ValueError("pre-routing refusals cannot report downstream execution")
        if executed.generation_executed and not executed.literature_retrieval_executed:
            raise ValueError("generation execution requires literature retrieval execution")

        if self.code == "request_schema_invalid" and self.route is not None:
            raise ValueError("request_schema_invalid requires an unknown route")
        if self.code in {"unsupported_request", "route_request_mismatch"}:
            if self.route != "unsupported":
                raise ValueError("router refusals require the unsupported route")
        elif self.route == "unsupported":
            raise ValueError("the unsupported route is reserved for router refusals")

        compatible_routes: dict[RagErrorCode, frozenset[RagRoute | None]] = {
            "request_schema_invalid": frozenset({None}),
            "unsupported_request": frozenset({"unsupported"}),
            "route_request_mismatch": frozenset({"unsupported"}),
            "structured_refused": frozenset({"structured", "hybrid"}),
            "literature_refused": frozenset({"literature", "hybrid"}),
            "hybrid_binding_unavailable": frozenset({"hybrid"}),
            "anchor_integrity_error": frozenset({"hybrid"}),
            "anchor_limit_exceeded": frozenset({"hybrid"}),
            "insufficient_evidence": frozenset({"literature"}),
            "context_integrity_error": frozenset({"literature", "hybrid"}),
            "context_too_large": frozenset({"literature", "hybrid"}),
            "llm_provider_unavailable": frozenset({"literature", "hybrid"}),
            "generation_failed": frozenset({"literature", "hybrid"}),
            "generated_draft_invalid": frozenset({"literature", "hybrid"}),
            "answer_validation_failed": frozenset({"literature", "hybrid"}),
            "internal_error": frozenset({None, "structured", "literature", "hybrid"}),
        }
        if self.route not in compatible_routes[self.code]:
            raise ValueError("error code is incompatible with the reported route")

        if self.route is None:
            if (
                executed.structured_retrieval_executed
                or executed.literature_retrieval_executed
                or executed.generation_executed
            ):
                raise ValueError("an unknown route cannot report downstream execution")
        elif self.route == "structured":
            if self.requested_release_key is None or self.requested_corpus_release_key is not None:
                raise ValueError("structured errors require only a structured release key")
            if executed.literature_retrieval_executed or executed.generation_executed:
                raise ValueError("structured errors cannot report literature or generation")
        elif self.route == "literature":
            if self.requested_release_key is not None or self.requested_corpus_release_key is None:
                raise ValueError("literature errors require only a corpus release key")
            if executed.structured_retrieval_executed:
                raise ValueError("literature errors cannot report structured retrieval")
        elif self.route == "hybrid":
            if self.requested_release_key is None or self.requested_corpus_release_key is None:
                raise ValueError("hybrid errors require both exact release keys")
            if (
                executed.literature_retrieval_executed
                and not executed.structured_retrieval_executed
            ):
                raise ValueError("hybrid literature retrieval requires structured retrieval")

        expected_execution: ExecutionFlags | None = None
        if self.code in {"anchor_integrity_error", "anchor_limit_exceeded"}:
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=False,
                generation_executed=False,
            )
        elif self.code == "insufficient_evidence":
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=False,
            )
        elif self.code in {
            "context_integrity_error",
            "context_too_large",
            "llm_provider_unavailable",
        }:
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=self.route == "hybrid",
                literature_retrieval_executed=True,
                generation_executed=False,
            )
        elif self.code in {
            "generation_failed",
            "generated_draft_invalid",
            "answer_validation_failed",
        }:
            expected_execution = ExecutionFlags(
                structured_retrieval_executed=self.route == "hybrid",
                literature_retrieval_executed=True,
                generation_executed=True,
            )
        elif self.code == "structured_refused" and (
            executed.literature_retrieval_executed or executed.generation_executed
        ):
            raise ValueError("structured refusal cannot report later-stage execution")
        elif self.code in {"literature_refused", "hybrid_binding_unavailable"} and (
            executed.generation_executed
        ):
            raise ValueError("pre-generation refusal cannot report generation execution")

        if expected_execution is not None and executed != expected_execution:
            raise ValueError("execution flags are inconsistent with the error code")
        return self


RagResponse = Annotated[
    StructuredRouteAnswer | LiteratureRouteAnswer | HybridRouteAnswer | RagErrorResponse,
    Field(discriminator="response_kind"),
]


__all__ = [
    "ANSWER_INSTRUCTIONS_VERSION",
    "BINDING_MANIFEST_VERSION",
    "CONTEXT_PACK_VERSION",
    "GENERATED_DRAFT_VERSION",
    "GENERATION_COMPOSITION_VERSION",
    "HYBRID_SUFFIXES",
    "MAX_CONTEXT_BYTES",
    "MAX_CONTEXT_CHUNKS",
    "MAX_GENERATED_CLAIMS",
    "MAX_GENERATED_OUTPUT_BYTES",
    "RAG_QUERY_REQUEST_VERSION",
    "ROUTE_DECISION_VERSION",
    "AnchorDiagnostic",
    "AnswerCitation",
    "AnswerInstructions",
    "AsciiQuestion",
    "ContextPack",
    "EvidenceSpan",
    "ExecutionFlags",
    "GeneratedAnswerDraft",
    "GeneratedLimitationCode",
    "GenerationComposition",
    "HybridReleaseBinding",
    "HybridReleaseBindingManifest",
    "HybridRouteAnswer",
    "LiteratureClaim",
    "LiteratureRouteAnswer",
    "ProviderIdentity",
    "RagErrorCode",
    "RagErrorResponse",
    "RagQueryRequest",
    "RagResponse",
    "RagRoute",
    "RouteDecision",
    "RouteRefusalCode",
    "StrictFrozenSchema",
    "StructuredRouteAnswer",
    "canonical_model_json",
    "canonical_model_sha256",
    "canonical_self_sha256",
]
