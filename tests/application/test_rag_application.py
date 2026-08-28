from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from eve_relation_rag.application.rag import RagQueryApplication
from eve_relation_rag.generation.context import (
    APPROVED_ANSWER_INSTRUCTIONS,
    build_context_pack,
)
from eve_relation_rag.generation.rendering import render_literature_components
from eve_relation_rag.hybrid.bindings import (
    ApprovedHybridBindingRegistry,
    HybridBindingRegistry,
    UnavailableHybridBindingRegistry,
)
from eve_relation_rag.hybrid.contracts import (
    BINDING_MANIFEST_VERSION,
    AnswerCitation,
    EvidenceSpan,
    ExecutionFlags,
    GenerationComposition,
    HybridReleaseBinding,
    HybridReleaseBindingManifest,
    HybridRouteAnswer,
    LiteratureClaim,
    LiteratureRouteAnswer,
    ProviderIdentity,
    RagErrorResponse,
    RagQueryRequest,
    StructuredRouteAnswer,
    canonical_self_sha256,
)
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
    PlainTextLocator,
    RetrievedChunk,
    RetrievedChunks,
)
from eve_relation_rag.literature.hashing import canonical_query_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec, canonical_plan_sha256
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.results import (
    ErrorResponse,
    PlanSuccess,
    QuerySuccess,
    StructuredError,
)
from tests.support.m2 import (
    TEST_RELEASE_KEY,
    FakeGate,
    TestsOnlyQueryableRelease,
    make_aggregate_application,
)
from tests.support.m4 import make_structured_success

CORPUS_KEY = "corpus:endoviho-rag:v0:20991231:999"
CORPUS_MANIFEST_SHA = "b" * 64


@dataclass(frozen=True)
class _CorpusCapability:
    corpus_release_key: str = CORPUS_KEY
    manifest_sha256: str = CORPUS_MANIFEST_SHA


class _CorpusGate:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.capability = _CorpusCapability()

    def authorize(self, corpus_release_key: str) -> _CorpusCapability:
        self.calls.append(corpus_release_key)
        return self.capability


class _LiteratureService:
    def __init__(self, result: RetrievedChunks) -> None:
        self.result = result
        self.calls: list[tuple[LiteratureRetrievalInvocation, object]] = []

    def retrieve_authorized(
        self,
        invocation: LiteratureRetrievalInvocation,
        capability: object,
    ) -> RetrievedChunks:
        self.calls.append((invocation, capability))
        return RetrievedChunks.model_validate(
            self.result.model_dump(mode="python")
            | {
                "query_sha256": canonical_query_sha256(
                    invocation.request,
                    invocation.system_anchors,
                )
            }
        )


@dataclass(frozen=True)
class _AnchorResolution:
    anchors: tuple[Any, ...] = ()
    diagnostics: tuple[str, ...] = ("structured_anchor_unmatched",)


class _AnchorResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[QuerySuccess, object]] = []

    def resolve(self, success: QuerySuccess, corpus: object) -> _AnchorResolution:
        self.calls.append((success, corpus))
        return _AnchorResolution()


class _Composer:
    def __init__(self, composition: GenerationComposition) -> None:
        self.composition = composition
        self.calls: list[object] = []

    def compose(self, context: object) -> GenerationComposition:
        self.calls.append(context)
        return self.composition.model_copy(update={"context_sha256": context.context_sha256})


class _OrderedStructuredApplication:
    def __init__(self, success: QuerySuccess, events: list[str]) -> None:
        self._success = success
        self._events = events

    def query(self, _request: StructuredQueryRequest) -> QuerySuccess:
        self._events.append("structured_facts")
        return self._success

    def query_with_pre_fact_hook(
        self,
        _request: StructuredQueryRequest,
        hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
    ) -> QuerySuccess:
        self._events.append("structured_plan")
        planned = PlanSuccess(
            query_plan=self._success.query_plan,
            planning_audit=self._success.planning_audit,
            resolved_entities=self._success.resolved_entities,
        )
        hook(TestsOnlyQueryableRelease(), planned)
        self._events.append("structured_facts")
        return self._success


class _OrderedBindingRegistry:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._delegate = _bindings()

    def authorize(self, release_key: str, corpus_release_key: str) -> HybridReleaseBinding:
        self._events.append("binding")
        return self._delegate.authorize(release_key, corpus_release_key)


def _query_success() -> tuple[QuerySuccess, object, FakeGate]:
    application, gate, _factory, _repository = make_aggregate_application(value=3)
    response = application.query(
        StructuredQueryRequest(
            release_key=TEST_RELEASE_KEY,
            question="Count distinct included loci in this release.",
        )
    )
    assert isinstance(response, QuerySuccess)
    return response, application, gate


def _retrieved_chunks(*, empty: bool = False) -> RetrievedChunks:
    chunks: tuple[RetrievedChunk, ...]
    warnings: tuple[str, ...]
    if empty:
        chunks = ()
        warnings = ("no_chunks_retrieved",)
    else:
        text = "The synthetic workflow used a deterministic evidence comparison."
        chunks = (
            RetrievedChunk(
                citation_id="D1",
                chunk_key=f"chunk:sha256:{'c' * 64}",
                document_key=f"document:sha256:{'d' * 64}",
                title="Synthetic evidence",
                doi=None,
                pmid=None,
                pmcid=None,
                section="Methods",
                locator=PlainTextLocator(
                    locator_type="plain_text",
                    paragraph_ordinal=1,
                    line_start=1,
                    line_end=1,
                ),
                locator_text="paragraph 1, lines 1-1",
                text=text,
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                retrieval_tier="corpus_fill",
                fts_rank=1,
                vector_rank=1,
                summary_vector_rank=None,
                rrf_score="0.032786885246",
                matched_anchors=(),
            ),
        )
        warnings = ()
    return RetrievedChunks(
        result_schema_version="retrieved-chunks-v2",
        status="ok",
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
        retrieval_policy_key=RETRIEVAL_POLICY_KEY,
        embedding_model_key=EMBEDDING_MODEL_KEY,
        query_sha256="e" * 64,
        requested_top_k=8,
        returned_count=len(chunks),
        retrieval_executed=True,
        anchor_mode="none",
        anchors_applied=(),
        warnings=warnings,
        chunks=chunks,
    )


def _composition(retrieved: RetrievedChunks) -> GenerationComposition:
    chunk = retrieved.chunks[0]
    claim = LiteratureClaim(
        claim_id="C1",
        claim_text="The synthetic workflow used a deterministic evidence comparison.",
        citation_ids=("D1",),
        evidence_spans=(
            EvidenceSpan(
                citation_id="D1",
                quote="used a deterministic evidence comparison",
            ),
        ),
    )
    limitation_codes = (
        "literature_evidence_is_explanatory",
        "mechanical_validation_is_not_semantic_entailment",
    )
    citation = AnswerCitation(
        citation_id=chunk.citation_id,
        chunk_key=chunk.chunk_key,
        document_key=chunk.document_key,
        title=chunk.title,
        doi=chunk.doi,
        pmid=chunk.pmid,
        pmcid=chunk.pmcid,
        section=chunk.section,
        locator=chunk.locator,
        locator_text=chunk.locator_text,
        text_sha256=chunk.text_sha256,
    )
    text = render_literature_components(
        claims=(claim,),
        citations=(citation,),
        generated_limitation_codes=limitation_codes,
    )
    return GenerationComposition(
        composition_schema_version="generation-composition-v1",
        context_sha256="0" * 64,
        provider_identity=ProviderIdentity(
            provider_key="provider:tests-only",
            model_key="model:tests-only",
            model_revision="revision:test",
            provider_artifact_sha256=None,
            generation_policy_key="generation:tests-only",
            prompt_policy_key=APPROVED_ANSWER_INSTRUCTIONS.instruction_policy_key,
            prompt_policy_sha256=APPROVED_ANSWER_INSTRUCTIONS.source_text_sha256,
            temperature=0,
            max_output_bytes=32768,
            timeout_seconds=1,
            retry_count=0,
        ),
        claims=(claim,),
        selected_limitation_codes=limitation_codes,
        citations=(citation,),
        literature_text=text,
        literature_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        validation_scope="mechanical",
    )


def _bindings() -> ApprovedHybridBindingRegistry:
    payload: dict[str, object] = {
        "binding_schema_version": BINDING_MANIFEST_VERSION,
        "bindings": (
            HybridReleaseBinding(
                release_key=TEST_RELEASE_KEY,
                release_manifest_sha256=TestsOnlyQueryableRelease().manifest_sha256,
                corpus_release_key=CORPUS_KEY,
                corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
            ),
        ),
        "manifest_sha256": "0" * 64,
    }
    payload["manifest_sha256"] = canonical_self_sha256(payload, "manifest_sha256")
    return ApprovedHybridBindingRegistry(HybridReleaseBindingManifest.model_validate(payload))


def _application(
    *,
    retrieved: RetrievedChunks,
    composer: _Composer | None,
    binding_registry: HybridBindingRegistry | None = None,
) -> tuple[RagQueryApplication, object, FakeGate, _CorpusGate, _LiteratureService, _AnchorResolver]:
    _success, structured, structured_gate = _query_success()
    structured_gate.calls.clear()
    corpus_gate = _CorpusGate()
    literature = _LiteratureService(retrieved)
    anchors = _AnchorResolver()
    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: structured,
        corpus_gate_factory=lambda: corpus_gate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=lambda: binding_registry or _bindings(),
        anchor_resolver_factory=lambda: anchors,
        composer_factory=lambda: composer,  # type: ignore[return-value]
        context_builder=build_context_pack,
    )
    return application, structured, structured_gate, corpus_gate, literature, anchors


def test_unsupported_route_has_zero_downstream_calls() -> None:
    retrieved = _retrieved_chunks()
    composer = _Composer(_composition(retrieved))
    application, _structured, structured_gate, corpus_gate, literature, anchors = _application(
        retrieved=retrieved,
        composer=composer,
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for prevalence in birds",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "unsupported_request"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert not structured_gate.calls
    assert not corpus_gate.calls
    assert not literature.calls
    assert not anchors.calls
    assert not composer.calls


def test_unsupported_route_constructs_no_route_dependency() -> None:
    constructions: list[str] = []

    def forbidden_factory() -> Any:
        constructions.append("constructed")
        raise AssertionError("unsupported route constructed a downstream dependency")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=forbidden_factory,
        corpus_gate_factory=forbidden_factory,
        literature_service_factory=forbidden_factory,
        binding_registry_factory=forbidden_factory,
        anchor_resolver_factory=forbidden_factory,
        composer_factory=forbidden_factory,
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for prevalence in birds",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "unsupported_request"
    assert constructions == []


def test_unserializable_unchecked_request_is_refused_before_routing() -> None:
    constructions: list[str] = []

    def forbidden_factory() -> Any:
        constructions.append("constructed")
        raise AssertionError("invalid request constructed a downstream dependency")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=forbidden_factory,
        corpus_gate_factory=forbidden_factory,
        literature_service_factory=forbidden_factory,
        binding_registry_factory=forbidden_factory,
        anchor_resolver_factory=forbidden_factory,
        composer_factory=forbidden_factory,
    )
    valid = RagQueryRequest(
        release_key=TEST_RELEASE_KEY,
        question="Count distinct included loci in this release.",
    )
    unchecked = valid.model_copy(update={"question": object()})

    response = application.query(unchecked)  # type: ignore[arg-type]

    assert isinstance(response, RagErrorResponse)
    assert response.code == "request_schema_invalid"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert constructions == []


def test_unserializable_structured_error_is_revalidated_after_the_service_call() -> None:
    valid_error = ErrorResponse(
        error=StructuredError(
            code="unsupported_question",
            message="Synthetic structured refusal.",
        )
    )
    unchecked = valid_error.model_copy(update={"error": object()})

    class InvalidStructuredApplication:
        def query(self, _request: StructuredQueryRequest) -> ErrorResponse:
            return unchecked  # type: ignore[return-value]

        def query_with_pre_fact_hook(
            self,
            _request: StructuredQueryRequest,
            _hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
        ) -> ErrorResponse:
            raise AssertionError("structured route used the hybrid entry point")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=InvalidStructuredApplication,
        corpus_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        literature_service_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            question="Count distinct included loci in this release.",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "structured_refused"
    assert response.upstream_code == "result_integrity_error"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=True,
        literature_retrieval_executed=False,
        generation_executed=False,
    )


def test_foreign_structured_error_preserves_its_trusted_pre_fact_flag() -> None:
    success, _structured, _gate = _query_success()
    foreign_plan = success.query_plan.model_copy(
        update={"original_question": "Count distinct assemblies in this release."}
    )
    foreign_error = ErrorResponse(
        query_plan=foreign_plan,
        planning_audit=success.planning_audit,
        resolved_entities=success.resolved_entities,
        error=StructuredError(
            code="unsupported_capability",
            message="Synthetic structured refusal.",
        ),
        fact_retrieval_executed=False,
    )

    class ForeignErrorApplication:
        def query(self, _request: StructuredQueryRequest) -> ErrorResponse:
            return foreign_error

        def query_with_pre_fact_hook(
            self,
            _request: StructuredQueryRequest,
            _hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
        ) -> ErrorResponse:
            raise AssertionError("structured route used the hybrid entry point")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=ForeignErrorApplication,
        corpus_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        literature_service_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            question="Count distinct included loci in this release.",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "structured_refused"
    assert response.upstream_code == "result_integrity_error"
    assert response.execution.structured_retrieval_executed is False


def test_unserializable_hybrid_pre_fact_error_has_zero_execution_flags() -> None:
    valid_error = ErrorResponse(
        error=StructuredError(
            code="unsupported_question",
            message="Synthetic structured refusal.",
        )
    )
    unchecked = valid_error.model_copy(update={"error": object()})

    class InvalidStructuredApplication:
        def query(self, _request: StructuredQueryRequest) -> ErrorResponse:
            raise AssertionError("hybrid route used the structured entry point")

        def query_with_pre_fact_hook(
            self,
            _request: StructuredQueryRequest,
            _hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
        ) -> ErrorResponse:
            return unchecked  # type: ignore[return-value]

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=InvalidStructuredApplication,
        corpus_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        literature_service_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        binding_registry_factory=_bindings,
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "structured_refused"
    assert response.upstream_code == "result_integrity_error"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )


@pytest.mark.parametrize("unserializable", (False, True))
def test_hybrid_query_success_without_pre_fact_hook_fails_closed_with_exact_flags(
    unserializable: bool,
) -> None:
    success, _structured, _gate = _query_success()
    returned = (
        success.model_copy(update={"structured_result": object()}) if unserializable else success
    )
    constructions: list[str] = []

    class NoHookStructuredApplication:
        def query(self, _request: StructuredQueryRequest) -> QuerySuccess:
            raise AssertionError("hybrid route used the structured entry point")

        def query_with_pre_fact_hook(
            self,
            _request: StructuredQueryRequest,
            _hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
        ) -> QuerySuccess:
            return returned  # type: ignore[return-value]

    def forbidden_factory() -> Any:
        constructions.append("constructed")
        raise AssertionError("missing pre-fact hook constructed a downstream dependency")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=NoHookStructuredApplication,
        corpus_gate_factory=forbidden_factory,
        literature_service_factory=forbidden_factory,
        binding_registry_factory=_bindings,
        anchor_resolver_factory=forbidden_factory,
        composer_factory=forbidden_factory,
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == ("structured_refused" if unserializable else "internal_error")
    assert response.upstream_code == ("result_integrity_error" if unserializable else None)
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=True,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert constructions == []


def test_structured_success_is_not_revisited_after_its_trusted_json_capture() -> None:
    question = f"Show locus locus:eve:v1:sha256:{'a' * 64}"
    success = make_structured_success("locus_detail", structured_question=question)

    class OneShotMapping(Mapping[str, object]):
        def __init__(self, value: Mapping[str, object]) -> None:
            self._value = dict(value)
            self._iterations = 0

        def __getitem__(self, key: str) -> object:
            return self._value[key]

        def __len__(self) -> int:
            return len(self._value)

        def __iter__(self) -> Iterator[str]:
            self._iterations += 1
            if self._iterations > 1:
                raise RuntimeError("unchecked mapping was traversed more than once")
            return iter(self._value)

    data: Any = success.structured_result.data
    assertion = data.public_assertions[0]
    evidence = assertion.supporting_evidence.model_copy(
        update={"source_locator": OneShotMapping(assertion.supporting_evidence.source_locator)}
    )
    changed_assertion = assertion.model_copy(update={"supporting_evidence": evidence})
    changed_data = data.model_copy(update={"public_assertions": (changed_assertion,)})
    changed_result = success.structured_result.model_copy(update={"data": changed_data})
    unchecked = success.model_copy(update={"structured_result": changed_result})

    class OneShotStructuredApplication:
        def query(self, _request: StructuredQueryRequest) -> QuerySuccess:
            return unchecked

        def query_with_pre_fact_hook(
            self,
            _request: StructuredQueryRequest,
            _hook: Callable[[TestsOnlyQueryableRelease, PlanSuccess], None],
        ) -> QuerySuccess:
            raise AssertionError("structured route used the hybrid entry point")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=OneShotStructuredApplication,
        corpus_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        literature_service_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            release_key=success.query_plan.release_key,
            question=question,
        )
    )

    assert isinstance(response, StructuredRouteAnswer)
    assert serialize_rag_response(response)


def test_structured_route_reuses_unchanged_m2_application_without_other_layers() -> None:
    retrieved = _retrieved_chunks()
    composer = _Composer(_composition(retrieved))
    application, _structured, structured_gate, corpus_gate, literature, anchors = _application(
        retrieved=retrieved,
        composer=composer,
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            question="Count distinct included loci in this release.",
        )
    )

    assert isinstance(response, StructuredRouteAnswer)
    assert response.query_success.structured_result.data.value == 3
    assert response.execution.generation_executed is False
    assert structured_gate.calls  # only the unchanged M2 application owns these calls
    assert not corpus_gate.calls
    assert not literature.calls
    assert not anchors.calls
    assert not composer.calls


def test_literature_route_retrieves_builds_context_and_generates() -> None:
    retrieved = _retrieved_chunks()
    composer = _Composer(_composition(retrieved))
    application, _structured, structured_gate, corpus_gate, literature, anchors = _application(
        retrieved=retrieved,
        composer=composer,
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature methods for ViralRecall",
        )
    )

    assert isinstance(response, LiteratureRouteAnswer)
    assert response.generation.claims[0].citation_ids == ("D1",)
    assert response.execution.generation_executed is True
    assert not structured_gate.calls
    assert corpus_gate.calls == [CORPUS_KEY]
    assert len(literature.calls) == 1
    assert not anchors.calls
    assert len(composer.calls) == 1


def test_literature_route_never_constructs_structured_or_hybrid_dependencies() -> None:
    retrieved = _retrieved_chunks()
    corpus_gate = _CorpusGate()
    literature = _LiteratureService(retrieved)
    composer = _Composer(_composition(retrieved))
    forbidden: list[str] = []

    def forbidden_factory() -> Any:
        forbidden.append("constructed")
        raise AssertionError("literature route constructed a structured or hybrid dependency")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=forbidden_factory,
        corpus_gate_factory=lambda: corpus_gate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=forbidden_factory,
        anchor_resolver_factory=forbidden_factory,
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature methods for ViralRecall",
        )
    )

    assert isinstance(response, LiteratureRouteAnswer)
    assert forbidden == []


def test_foreign_corpus_result_is_rejected_before_context_or_generation() -> None:
    question = "Explain the literature evidence for ViralRecall"
    foreign_corpus_key = "corpus:endoviho-rag:v0:20991230:998"
    foreign = RetrievedChunks.model_validate(
        _retrieved_chunks().model_dump(mode="python")
        | {
            "corpus_release_key": foreign_corpus_key,
            "corpus_manifest_sha256": "f" * 64,
            "query_sha256": canonical_query_sha256(
                LiteratureRetrievalRequest(
                    request_schema_version="literature-retrieval-request-v1",
                    corpus_release_key=foreign_corpus_key,
                    question=question,
                    top_k=8,
                ),
                (),
            ),
        }
    )

    class ForeignLiteratureService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> RetrievedChunks:
            return foreign

    literature = ForeignLiteratureService()
    composer = _Composer(_composition(_retrieved_chunks()))

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(RagQueryRequest(corpus_release_key=CORPUS_KEY, question=question))

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.upstream_code == "corpus_manifest_invalid"
    assert response.execution.literature_retrieval_executed is True
    assert response.execution.generation_executed is False
    assert composer.calls == []


def test_wrong_retrieval_selector_is_rejected_before_context_or_generation() -> None:
    question = "Explain the literature evidence for ViralRecall"
    wrong_top_k = 7
    wrong = RetrievedChunks.model_validate(
        _retrieved_chunks().model_dump(mode="python")
        | {
            "requested_top_k": wrong_top_k,
            "query_sha256": canonical_query_sha256(
                LiteratureRetrievalRequest(
                    request_schema_version="literature-retrieval-request-v1",
                    corpus_release_key=CORPUS_KEY,
                    question=question,
                    top_k=wrong_top_k,
                ),
                (),
            ),
        }
    )

    class WrongSelectorLiteratureService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> RetrievedChunks:
            return wrong

    composer = _Composer(_composition(_retrieved_chunks()))
    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=WrongSelectorLiteratureService,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(RagQueryRequest(corpus_release_key=CORPUS_KEY, question=question))

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.execution.literature_retrieval_executed is True
    assert composer.calls == []


def test_unexpected_literature_failure_records_the_attempted_retrieval() -> None:
    class ExplodingLiteratureService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> RetrievedChunks:
            raise RuntimeError("unexpected retrieval failure")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=ExplodingLiteratureService,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=True,
        generation_executed=False,
    )


def test_unchecked_literature_error_is_revalidated_after_the_service_call() -> None:
    unchecked = LiteratureRetrievalError.model_construct(
        error_schema_version="literature-retrieval-error-v1",
        status="error",
        code="not-an-approved-code",
        message="untrusted",
        requested_corpus_release_key=CORPUS_KEY,
        retrieval_executed=False,
    )

    class UncheckedErrorService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> LiteratureRetrievalError:
            return unchecked

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=UncheckedErrorService,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.upstream_code == "corpus_manifest_invalid"
    assert response.execution.literature_retrieval_executed is True


def test_foreign_literature_error_preserves_its_trusted_pre_retrieval_flag() -> None:
    foreign_error = LiteratureRetrievalError(
        error_schema_version="literature-retrieval-error-v1",
        status="error",
        code="corpus_not_found",
        message="Synthetic literature refusal.",
        requested_corpus_release_key="corpus:endoviho-rag:v0:20991230:998",
        retrieval_executed=False,
    )

    class ForeignErrorService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> LiteratureRetrievalError:
            return foreign_error

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=ForeignErrorService,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.upstream_code == "corpus_manifest_invalid"
    assert response.execution.literature_retrieval_executed is False


def test_unserializable_literature_success_fails_closed_after_the_service_call() -> None:
    unserializable = _retrieved_chunks().model_copy(update={"chunks": (object(),)})

    class UnserializableSuccessService:
        def retrieve_authorized(
            self,
            _invocation: LiteratureRetrievalInvocation,
            _capability: object,
        ) -> RetrievedChunks:
            return unserializable

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=UnserializableSuccessService,
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=lambda: (_ for _ in ()).throw(AssertionError()),
    )

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "literature_refused"
    assert response.upstream_code == "corpus_manifest_invalid"
    assert response.execution.literature_retrieval_executed is True


def test_unexpected_context_or_composer_failure_preserves_execution_flags() -> None:
    retrieved = _retrieved_chunks()

    def exploding_context(**_values: Any) -> object:
        raise RuntimeError("unexpected context failure")

    class ExplodingComposer:
        def compose(self, _context: object) -> GenerationComposition:
            raise RuntimeError("unexpected composer failure")

    context_application, *_rest = _application(retrieved=retrieved, composer=None)
    context_application._context_builder = exploding_context  # type: ignore[attr-defined]
    context_response = context_application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )
    composer_application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=lambda: _LiteratureService(retrieved),
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        anchor_resolver_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        composer_factory=ExplodingComposer,  # type: ignore[arg-type]
    )
    composer_response = composer_application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(context_response, RagErrorResponse)
    assert context_response.code == "internal_error"
    assert context_response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=True,
        generation_executed=False,
    )
    assert isinstance(composer_response, RagErrorResponse)
    assert composer_response.code == "internal_error"
    assert composer_response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=True,
        generation_executed=True,
    )


def test_hybrid_route_preserves_structured_result_and_runs_fixed_order() -> None:
    retrieved = _retrieved_chunks()
    composer = _Composer(_composition(retrieved))
    application, _structured, structured_gate, corpus_gate, literature, anchors = _application(
        retrieved=retrieved,
        composer=composer,
    )
    request = RagQueryRequest(
        release_key=TEST_RELEASE_KEY,
        corpus_release_key=CORPUS_KEY,
        question=(
            "Count distinct included loci in this release. and explain the literature evidence"
        ),
    )

    response = application.query(request)

    assert isinstance(response, HybridRouteAnswer)
    assert response.query_success.structured_result.data.value == 3
    assert response.anchor_diagnostics == ("structured_anchor_unmatched",)
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=True,
        literature_retrieval_executed=True,
        generation_executed=True,
    )
    assert structured_gate.calls
    assert corpus_gate.calls == [CORPUS_KEY]
    assert len(anchors.calls) == 1
    assert len(literature.calls) == 1
    assert len(composer.calls) == 1


def test_hybrid_route_obeys_the_exact_pre_fact_call_order() -> None:
    events: list[str] = []
    success, _structured, _gate = _query_success()
    retrieved = _retrieved_chunks()
    corpus_delegate = _CorpusGate()
    literature_delegate = _LiteratureService(retrieved)
    anchor_delegate = _AnchorResolver()
    composer_delegate = _Composer(_composition(retrieved))

    class OrderedCorpusGate:
        def authorize(self, corpus_release_key: str) -> _CorpusCapability:
            events.append("corpus_gate")
            return corpus_delegate.authorize(corpus_release_key)

    class OrderedLiteratureService:
        def retrieve_authorized(
            self,
            invocation: LiteratureRetrievalInvocation,
            capability: object,
        ) -> RetrievedChunks:
            events.append("literature")
            return literature_delegate.retrieve_authorized(invocation, capability)

    class OrderedAnchorResolver:
        def resolve(self, query_success: QuerySuccess, corpus: object) -> _AnchorResolution:
            events.append("anchors")
            return anchor_delegate.resolve(query_success, corpus)

    class OrderedComposer:
        def compose(self, context: object) -> GenerationComposition:
            events.append("generation")
            return composer_delegate.compose(context)

    def ordered_context_builder(**values: Any) -> object:
        events.append("context")
        return build_context_pack(**values)

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: _OrderedStructuredApplication(success, events),
        corpus_gate_factory=OrderedCorpusGate,
        literature_service_factory=OrderedLiteratureService,
        binding_registry_factory=lambda: _OrderedBindingRegistry(events),
        anchor_resolver_factory=OrderedAnchorResolver,
        composer_factory=OrderedComposer,  # type: ignore[arg-type]
        context_builder=ordered_context_builder,  # type: ignore[arg-type]
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, HybridRouteAnswer)
    assert events == [
        "binding",
        "structured_plan",
        "corpus_gate",
        "structured_facts",
        "anchors",
        "literature",
        "context",
        "generation",
    ]


def test_hybrid_without_approved_binding_stops_before_every_downstream_call() -> None:
    retrieved = _retrieved_chunks()
    composer = _Composer(_composition(retrieved))
    application, _structured, structured_gate, corpus_gate, literature, anchors = _application(
        retrieved=retrieved,
        composer=composer,
        binding_registry=UnavailableHybridBindingRegistry(),
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "hybrid_binding_unavailable"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert not structured_gate.calls
    assert not corpus_gate.calls
    assert not literature.calls
    assert not anchors.calls
    assert not composer.calls


def test_hybrid_rejects_a_foreign_structured_question_before_anchor_resolution() -> None:
    success, _structured, _gate = _query_success()
    foreign_plan = success.query_plan.model_copy(
        update={"original_question": "Count distinct assemblies in this release."}
    )
    foreign_result = success.structured_result.model_copy(
        update={"plan_sha256": canonical_plan_sha256(foreign_plan)}
    )
    foreign = QuerySuccess.model_validate(
        success.model_dump(mode="python")
        | {"query_plan": foreign_plan, "structured_result": foreign_result}
    )
    anchors = _AnchorResolver()
    literature = _LiteratureService(_retrieved_chunks())
    composer = _Composer(_composition(_retrieved_chunks()))

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: _OrderedStructuredApplication(foreign, []),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=_bindings,
        anchor_resolver_factory=lambda: anchors,
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "structured_refused"
    assert response.upstream_code == "result_integrity_error"
    assert response.execution.structured_retrieval_executed is True
    assert anchors.calls == []
    assert literature.calls == []
    assert composer.calls == []


def test_hybrid_rejects_an_unserializable_structured_success_with_exact_flags() -> None:
    success, _structured, _gate = _query_success()
    unserializable = success.model_copy(update={"structured_result": object()})
    anchors = _AnchorResolver()
    literature = _LiteratureService(_retrieved_chunks())
    composer = _Composer(_composition(_retrieved_chunks()))
    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: _OrderedStructuredApplication(
            unserializable,  # type: ignore[arg-type]
            [],
        ),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=_bindings,
        anchor_resolver_factory=lambda: anchors,
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.route == "hybrid"
    assert response.requested_release_key == TEST_RELEASE_KEY
    assert response.requested_corpus_release_key == CORPUS_KEY
    assert response.code == "structured_refused"
    assert response.upstream_code == "result_integrity_error"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=True,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert anchors.calls == []
    assert literature.calls == []
    assert composer.calls == []


def test_unexpected_anchor_failure_preserves_completed_structured_stage() -> None:
    success, _structured, _gate = _query_success()
    literature = _LiteratureService(_retrieved_chunks())
    composer = _Composer(_composition(_retrieved_chunks()))

    class ExplodingAnchorResolver:
        def resolve(self, _success: QuerySuccess, _corpus: object) -> _AnchorResolution:
            raise RuntimeError("unexpected anchor failure")

    application = RagQueryApplication(
        router=DeterministicRouter(),
        structured_application_factory=lambda: _OrderedStructuredApplication(success, []),
        corpus_gate_factory=_CorpusGate,
        literature_service_factory=lambda: literature,
        binding_registry_factory=_bindings,
        anchor_resolver_factory=ExplodingAnchorResolver,
        composer_factory=lambda: composer,  # type: ignore[return-value]
    )

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release. and explain the literature evidence"
            ),
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "internal_error"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=True,
        literature_retrieval_executed=False,
        generation_executed=False,
    )
    assert literature.calls == []
    assert composer.calls == []


def test_hybrid_empty_retrieval_returns_structured_answer_without_generation() -> None:
    empty = _retrieved_chunks(empty=True)
    nonempty = _retrieved_chunks()
    composer = _Composer(_composition(nonempty))
    application, *_rest = _application(retrieved=empty, composer=composer)

    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            corpus_release_key=CORPUS_KEY,
            question=(
                "Count distinct included loci in this release."
                " and explain the literature limitations"
            ),
        )
    )

    assert isinstance(response, HybridRouteAnswer)
    assert response.generation is None
    assert response.execution.generation_executed is False
    assert "insufficient evidence" in response.answer_text
    assert not composer.calls


def test_generation_remains_fail_closed_when_no_provider_is_approved() -> None:
    retrieved = _retrieved_chunks()
    application, *_rest = _application(retrieved=retrieved, composer=None)

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "llm_provider_unavailable"
    assert response.execution == ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=True,
        generation_executed=False,
    )


def test_application_revalidates_composer_claims_against_the_exact_context() -> None:
    retrieved = _retrieved_chunks()
    base = _composition(retrieved)
    invalid_claim = LiteratureClaim(
        claim_id="C1",
        claim_text="The synthetic workflow used a deterministic evidence comparison.",
        citation_ids=("D2",),
        evidence_spans=(
            EvidenceSpan(
                citation_id="D2",
                quote="used a deterministic evidence comparison",
            ),
        ),
    )
    invalid_text = render_literature_components(
        claims=(invalid_claim,),
        citations=base.citations,
        generated_limitation_codes=base.selected_limitation_codes,
    )
    invalid = GenerationComposition.model_validate(
        base.model_dump(mode="python")
        | {
            "claims": (invalid_claim,),
            "literature_text": invalid_text,
            "literature_text_sha256": hashlib.sha256(invalid_text.encode("utf-8")).hexdigest(),
        }
    )
    composer = _Composer(invalid)
    application, *_rest = _application(retrieved=retrieved, composer=composer)

    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )

    assert isinstance(response, RagErrorResponse)
    assert response.code == "answer_validation_failed"
    assert response.execution.generation_executed is True
    assert len(composer.calls) == 1


def test_public_response_serializer_rejects_coherent_answer_text_tampering() -> None:
    retrieved = _retrieved_chunks()
    application, *_rest = _application(
        retrieved=retrieved,
        composer=_Composer(_composition(retrieved)),
    )
    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )
    assert isinstance(response, LiteratureRouteAnswer)
    tampered_text = "Coherently tampered answer."
    tampered = LiteratureRouteAnswer.model_validate(
        response.model_dump(mode="python")
        | {
            "answer_text": tampered_text,
            "answer_sha256": hashlib.sha256(tampered_text.encode("utf-8")).hexdigest(),
        }
    )

    with pytest.raises(ValueError, match="canonical rendering"):
        serialize_rag_response(tampered)


def test_public_response_serializer_rejects_original_top_k_selector_tampering() -> None:
    retrieved = _retrieved_chunks()
    application, *_rest = _application(
        retrieved=retrieved,
        composer=_Composer(_composition(retrieved)),
    )
    response = application.query(
        RagQueryRequest(
            corpus_release_key=CORPUS_KEY,
            question="Explain the literature evidence for ViralRecall",
        )
    )
    assert isinstance(response, LiteratureRouteAnswer)
    tampered_request = response.original_request.model_copy(update={"literature_top_k": 7})
    tampered = response.model_copy(update={"original_request": tampered_request})

    with pytest.raises(ValueError, match="top_k.*original request selector"):
        serialize_rag_response(tampered)


def test_public_response_serializer_rejects_original_page_selector_tampering() -> None:
    retrieved = _retrieved_chunks()
    application, *_rest = _application(retrieved=retrieved, composer=None)
    response = application.query(
        RagQueryRequest(
            release_key=TEST_RELEASE_KEY,
            question="Count distinct included loci in this release.",
        )
    )
    assert isinstance(response, StructuredRouteAnswer)
    tampered_request = response.original_request.model_copy(update={"page": PageSpec(limit=7)})
    tampered = response.model_copy(update={"original_request": tampered_request})

    with pytest.raises(ValueError, match="page.*original request selector"):
        serialize_rag_response(tampered)


@pytest.mark.parametrize(
    ("code", "route", "release_key", "corpus_key", "execution", "upstream_code"),
    (
        (
            "structured_refused",
            "literature",
            None,
            CORPUS_KEY,
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            "result_integrity_error",
        ),
        (
            "literature_refused",
            "structured",
            TEST_RELEASE_KEY,
            None,
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            "corpus_manifest_invalid",
        ),
        (
            "hybrid_binding_unavailable",
            "literature",
            None,
            CORPUS_KEY,
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            None,
        ),
        (
            "anchor_integrity_error",
            "structured",
            TEST_RELEASE_KEY,
            None,
            ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            None,
        ),
        (
            "llm_provider_unavailable",
            "structured",
            TEST_RELEASE_KEY,
            None,
            ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            None,
        ),
    ),
)
def test_error_contract_rejects_code_route_mismatches(
    code: str,
    route: str,
    release_key: str | None,
    corpus_key: str | None,
    execution: ExecutionFlags,
    upstream_code: str | None,
) -> None:
    with pytest.raises(ValueError, match="incompatible.*route"):
        RagErrorResponse.model_validate(
            {
                "route": route,
                "requested_release_key": release_key,
                "requested_corpus_release_key": corpus_key,
                "code": code,
                "message": "Synthetic refusal.",
                "upstream_code": upstream_code,
                "execution": execution,
            }
        )


@pytest.mark.parametrize(
    ("code", "route", "valid_execution", "tampered_execution"),
    (
        (
            "llm_provider_unavailable",
            "literature",
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=False,
            ),
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=True,
            ),
        ),
        (
            "generation_failed",
            "literature",
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=True,
            ),
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=False,
            ),
        ),
        (
            "context_integrity_error",
            "literature",
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=False,
            ),
            ExecutionFlags(
                structured_retrieval_executed=False,
                literature_retrieval_executed=True,
                generation_executed=True,
            ),
        ),
        (
            "anchor_integrity_error",
            "hybrid",
            ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=False,
                generation_executed=False,
            ),
            ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=True,
                generation_executed=False,
            ),
        ),
    ),
)
def test_public_response_serializer_rejects_error_stage_tampering(
    code: str,
    route: str,
    valid_execution: ExecutionFlags,
    tampered_execution: ExecutionFlags,
) -> None:
    valid = RagErrorResponse.model_validate(
        {
            "route": route,
            "requested_release_key": TEST_RELEASE_KEY if route == "hybrid" else None,
            "requested_corpus_release_key": CORPUS_KEY,
            "code": code,
            "message": "Synthetic refusal.",
            "upstream_code": None,
            "execution": valid_execution,
        }
    )
    tampered = valid.model_copy(update={"execution": tampered_execution})

    with pytest.raises(ValueError, match="execution flags.*error code"):
        serialize_rag_response(tampered)
