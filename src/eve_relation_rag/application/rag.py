"""Milestone 4 routed orchestration over unchanged M2 and M3 services."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal, Protocol, cast

from eve_relation_rag.generation.composer import GenerationComposer, GenerationComposerError
from eve_relation_rag.generation.context import ContextBuildError, build_context_pack
from eve_relation_rag.generation.rendering import (
    render_hybrid_answer_text,
    render_hybrid_insufficient_answer_text,
    render_literature_answer_text,
    render_structured_answer_text,
)
from eve_relation_rag.generation.validators import (
    AnswerValidationError,
    validate_generation_composition,
)
from eve_relation_rag.hybrid.bindings import HybridBindingRefusal, HybridBindingRegistry
from eve_relation_rag.hybrid.contracts import (
    AnchorDiagnostic,
    ContextPack,
    ExecutionFlags,
    GenerationComposition,
    HybridRouteAnswer,
    LiteratureRouteAnswer,
    RagErrorCode,
    RagErrorResponse,
    RagQueryRequest,
    RagResponse,
    RagRoute,
    StructuredRouteAnswer,
)
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.contracts import (
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    LiteratureRetrievalRequest,
    RetrievalAnchor,
    RetrievedChunks,
)
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.hashing import canonical_query_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec, StructuredPlan
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.hybrid.anchors import (
    StructuredAnchorResolution,
    StructuredAnchorResolutionError,
)
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.results import ErrorResponse, PlanSuccess, QuerySuccess


class StructuredApplication(Protocol):
    def query(
        self,
        request: StructuredQueryRequest,
    ) -> QuerySuccess | ErrorResponse: ...

    def query_with_pre_fact_hook(
        self,
        request: StructuredQueryRequest,
        hook: Callable[[ReleaseCapability, PlanSuccess], None],
    ) -> QuerySuccess | ErrorResponse: ...


class CorpusGate(Protocol):
    def authorize(self, corpus_release_key: str) -> CorpusCapability: ...


class LiteratureService(Protocol):
    def retrieve_authorized(
        self,
        invocation: LiteratureRetrievalInvocation,
        capability: CorpusCapability,
    ) -> RetrievedChunks | LiteratureRetrievalError: ...


class AnchorResolver(Protocol):
    def resolve(
        self,
        query_success: QuerySuccess,
        corpus: CorpusCapability,
    ) -> StructuredAnchorResolution: ...


class ContextBuilder(Protocol):
    def __call__(
        self,
        *,
        route: Literal["literature", "hybrid"],
        original_question: str,
        retrieved_chunks: RetrievedChunks,
        query_success: QuerySuccess | None = None,
    ) -> ContextPack: ...


class _HybridBindingIdentityMismatch(RuntimeError):
    """Internal control signal for a checksum mismatch at the pre-fact hook."""


_NO_EXECUTION = ExecutionFlags(
    structured_retrieval_executed=False,
    literature_retrieval_executed=False,
    generation_executed=False,
)


class RagQueryApplication:
    """Route one strict request through the exact approved M4 call graph."""

    def __init__(
        self,
        *,
        router: DeterministicRouter,
        structured_application_factory: Callable[[], StructuredApplication],
        corpus_gate_factory: Callable[[], CorpusGate],
        literature_service_factory: Callable[[], LiteratureService],
        binding_registry_factory: Callable[[], HybridBindingRegistry],
        anchor_resolver_factory: Callable[[], AnchorResolver],
        composer_factory: Callable[[], GenerationComposer | None],
        context_builder: ContextBuilder = build_context_pack,
    ) -> None:
        self._router = router
        self._structured_application_factory = structured_application_factory
        self._corpus_gate_factory = corpus_gate_factory
        self._literature_service_factory = literature_service_factory
        self._binding_registry_factory = binding_registry_factory
        self._anchor_resolver_factory = anchor_resolver_factory
        self._composer_factory = composer_factory
        self._context_builder = context_builder

    def query(self, request: RagQueryRequest) -> RagResponse:
        """Execute exactly one route without fallback or partial generated answers."""

        try:
            trusted_request = RagQueryRequest.model_validate_json(request.model_dump_json())
        except Exception:
            return _error(
                route=None,
                release_key=None,
                corpus_release_key=None,
                code="request_schema_invalid",
                message="The routed query request is invalid.",
            )

        try:
            decision = self._router.route(trusted_request)
        except Exception:
            return _error(
                route=None,
                release_key=trusted_request.release_key,
                corpus_release_key=trusted_request.corpus_release_key,
                code="internal_error",
                message="The routed query could not be classified safely.",
            )
        if decision.route == "unsupported":
            return _error(
                route="unsupported",
                release_key=trusted_request.release_key,
                corpus_release_key=trusted_request.corpus_release_key,
                code=cast(RagErrorCode, decision.refusal_code),
                message=(
                    "The request fields do not match the selected route."
                    if decision.refusal_code == "route_request_mismatch"
                    else "The question is outside the approved routed query grammar."
                ),
            )
        if decision.route == "structured":
            return self._query_structured(trusted_request, cast(str, decision.structured_question))
        if decision.route == "literature":
            return self._query_literature(
                trusted_request,
                cast(str, decision.literature_question),
                cast(int, decision.effective_literature_top_k),
            )
        return self._query_hybrid(
            trusted_request,
            structured_question=cast(str, decision.structured_question),
            literature_question=cast(str, decision.literature_question),
            top_k=cast(int, decision.effective_literature_top_k),
        )

    def _query_structured(
        self,
        request: RagQueryRequest,
        structured_question: str,
    ) -> StructuredRouteAnswer | RagErrorResponse:
        release_key = cast(str, request.release_key)
        try:
            response = self._structured_application_factory().query(
                StructuredQueryRequest(
                    release_key=release_key,
                    question=structured_question,
                    page=request.page,
                )
            )
        except Exception:
            return _error(
                route="structured",
                release_key=request.release_key,
                corpus_release_key=None,
                code="internal_error",
                message="The structured query service is unavailable.",
            )
        if isinstance(response, ErrorResponse):
            return _structured_error(
                request,
                response,
                structured_question=structured_question,
                invalid_structured_executed=True,
            )
        trusted_or_error = _trusted_structured_success(
            request,
            response,
            route="structured",
            structured_question=structured_question,
        )
        if isinstance(trusted_or_error, RagErrorResponse):
            return trusted_or_error
        trusted_response = trusted_or_error
        try:
            structured_text = render_structured_answer_text(trusted_response)
            return StructuredRouteAnswer(
                original_request=request,
                query_success=trusted_response,
                structured_text=structured_text,
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        except Exception:
            return _error(
                route="structured",
                release_key=request.release_key,
                corpus_release_key=None,
                code="internal_error",
                message="The structured answer could not be rendered safely.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )

    def _query_literature(
        self,
        request: RagQueryRequest,
        literature_question: str,
        top_k: int,
    ) -> LiteratureRouteAnswer | RagErrorResponse:
        corpus_key = cast(str, request.corpus_release_key)
        try:
            corpus = self._corpus_gate_factory().authorize(corpus_key)
        except LiteratureRetrievalRefusal as refusal:
            return _literature_gate_error(request, route="literature", refusal=refusal)
        except Exception:
            return _error(
                route="literature",
                release_key=None,
                corpus_release_key=corpus_key,
                code="internal_error",
                message="The literature release gate is unavailable.",
            )
        retrieved_or_error = self._retrieve(
            corpus=corpus,
            question=literature_question,
            top_k=top_k,
            anchors=(),
        )
        if isinstance(retrieved_or_error, RagErrorResponse):
            return retrieved_or_error
        retrieved = retrieved_or_error
        if not retrieved.chunks:
            return _error(
                route="literature",
                release_key=None,
                corpus_release_key=corpus_key,
                code="insufficient_evidence",
                message=(
                    "The approved corpus supplied insufficient evidence for a literature claim."
                ),
                execution=ExecutionFlags(
                    structured_retrieval_executed=False,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        generated = self._generate(
            request=request,
            route="literature",
            retrieved=retrieved,
            query_success=None,
        )
        if isinstance(generated, RagErrorResponse):
            return generated
        composition, context = generated
        try:
            answer_text = render_literature_answer_text(composition)
            return LiteratureRouteAnswer(
                original_request=request,
                retrieved_chunks=retrieved,
                generation=composition,
                answer_text=answer_text,
                answer_sha256=_text_sha256(answer_text),
                execution=ExecutionFlags(
                    structured_retrieval_executed=False,
                    literature_retrieval_executed=True,
                    generation_executed=True,
                ),
            )
        except Exception:
            return _answer_validation_error(
                request,
                route="literature",
                structured_executed=False,
                context=context,
            )

    def _query_hybrid(
        self,
        request: RagQueryRequest,
        *,
        structured_question: str,
        literature_question: str,
        top_k: int,
    ) -> HybridRouteAnswer | RagErrorResponse:
        release_key = cast(str, request.release_key)
        corpus_key = cast(str, request.corpus_release_key)
        try:
            binding = self._binding_registry_factory().authorize(release_key, corpus_key)
        except HybridBindingRefusal as refusal:
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code="hybrid_binding_unavailable",
                message=refusal.message,
            )
        except Exception:
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code="internal_error",
                message="The hybrid release binding service is unavailable.",
            )

        corpus: CorpusCapability | None = None

        def authorize_bound_corpus(
            release: ReleaseCapability,
            _planned: PlanSuccess,
        ) -> None:
            nonlocal corpus
            if (
                release.release_key != binding.release_key
                or release.manifest_sha256 != binding.release_manifest_sha256
            ):
                raise _HybridBindingIdentityMismatch
            authorized_corpus = self._corpus_gate_factory().authorize(corpus_key)
            if (
                authorized_corpus.corpus_release_key != binding.corpus_release_key
                or authorized_corpus.manifest_sha256 != binding.corpus_manifest_sha256
            ):
                raise _HybridBindingIdentityMismatch
            corpus = authorized_corpus

        try:
            structured_response = self._structured_application_factory().query_with_pre_fact_hook(
                StructuredQueryRequest(
                    release_key=release_key,
                    question=structured_question,
                    page=request.page,
                ),
                authorize_bound_corpus,
            )
        except _HybridBindingIdentityMismatch:
            return _binding_identity_error(request)
        except LiteratureRetrievalRefusal as refusal:
            return _literature_gate_error(request, route="hybrid", refusal=refusal)
        except Exception:
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code="internal_error",
                message="The hybrid query service is unavailable.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=corpus is not None,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        if isinstance(structured_response, ErrorResponse):
            return _structured_error(
                request,
                structured_response,
                route="hybrid",
                structured_question=structured_question,
                invalid_structured_executed=corpus is not None,
            )
        trusted_or_error = _trusted_structured_success(
            request,
            structured_response,
            route="hybrid",
            structured_question=structured_question,
        )
        if isinstance(trusted_or_error, RagErrorResponse):
            return trusted_or_error
        query_success = trusted_or_error
        if corpus is None:  # pragma: no branch - invariant failure is a typed response.
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code="internal_error",
                message="The bound corpus preflight did not complete.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        if (
            query_success.structured_result.release.manifest_sha256
            != binding.release_manifest_sha256
        ):
            return _binding_identity_error(
                request,
                structured_executed=True,
            )

        try:
            resolution = self._anchor_resolver_factory().resolve(query_success, corpus)
        except StructuredAnchorResolutionError as refusal:
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code=refusal.code,
                message=refusal.message,
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        except Exception:
            return _error(
                route="hybrid",
                release_key=release_key,
                corpus_release_key=corpus_key,
                code="internal_error",
                message="The structured anchor resolver is unavailable.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )

        retrieved_or_error = self._retrieve(
            corpus=corpus,
            question=literature_question,
            top_k=top_k,
            anchors=resolution.anchors,
            request=request,
            route="hybrid",
            structured_executed=True,
        )
        if isinstance(retrieved_or_error, RagErrorResponse):
            return retrieved_or_error
        retrieved = retrieved_or_error
        if retrieved.corpus_manifest_sha256 != binding.corpus_manifest_sha256:
            return _binding_identity_error(
                request,
                structured_executed=True,
                literature_executed=True,
            )

        diagnostics: tuple[AnchorDiagnostic, ...] = resolution.diagnostics
        if not retrieved.chunks:
            try:
                answer_text = render_hybrid_insufficient_answer_text(query_success)
                return HybridRouteAnswer(
                    original_request=request,
                    query_success=query_success,
                    retrieved_chunks=retrieved,
                    anchor_diagnostics=diagnostics,
                    generation=None,
                    insufficient_evidence_limitation="insufficient_literature_evidence",
                    answer_text=answer_text,
                    answer_sha256=_text_sha256(answer_text),
                    execution=ExecutionFlags(
                        structured_retrieval_executed=True,
                        literature_retrieval_executed=True,
                        generation_executed=False,
                    ),
                )
            except Exception:
                return _error(
                    route="hybrid",
                    release_key=release_key,
                    corpus_release_key=corpus_key,
                    code="internal_error",
                    message="The hybrid insufficiency answer could not be rendered safely.",
                    execution=ExecutionFlags(
                        structured_retrieval_executed=True,
                        literature_retrieval_executed=True,
                        generation_executed=False,
                    ),
                )

        generated = self._generate(
            request=request,
            route="hybrid",
            retrieved=retrieved,
            query_success=query_success,
        )
        if isinstance(generated, RagErrorResponse):
            return generated
        composition, context = generated
        try:
            answer_text = render_hybrid_answer_text(
                query_success.structured_result,
                composition,
            )
            return HybridRouteAnswer(
                original_request=request,
                query_success=query_success,
                retrieved_chunks=retrieved,
                anchor_diagnostics=diagnostics,
                generation=composition,
                insufficient_evidence_limitation=None,
                answer_text=answer_text,
                answer_sha256=_text_sha256(answer_text),
                execution=ExecutionFlags(
                    structured_retrieval_executed=True,
                    literature_retrieval_executed=True,
                    generation_executed=True,
                ),
            )
        except Exception:
            return _answer_validation_error(
                request,
                route="hybrid",
                structured_executed=True,
                context=context,
            )

    def _retrieve(
        self,
        *,
        corpus: CorpusCapability,
        question: str,
        top_k: int,
        anchors: tuple[RetrievalAnchor, ...],
        request: RagQueryRequest | None = None,
        route: RagRoute = "literature",
        structured_executed: bool = False,
    ) -> RetrievedChunks | RagErrorResponse:
        target_request = request or RagQueryRequest(
            corpus_release_key=corpus.corpus_release_key,
            question=question,
            literature_top_k=top_k,
        )
        try:
            invocation = LiteratureRetrievalInvocation(
                request=LiteratureRetrievalRequest(
                    request_schema_version="literature-retrieval-request-v1",
                    corpus_release_key=corpus.corpus_release_key,
                    question=question,
                    top_k=top_k,
                ),
                system_anchors=anchors,
            )
            service = self._literature_service_factory()
        except Exception:
            return _error(
                route=route,
                release_key=target_request.release_key,
                corpus_release_key=target_request.corpus_release_key,
                code="literature_refused",
                message="The configured literature retrieval service is unavailable.",
                upstream_code="embedding_provider_failed",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=False,
                    generation_executed=False,
                ),
            )
        try:
            response = service.retrieve_authorized(invocation, corpus)
        except Exception:
            return _error(
                route=route,
                release_key=target_request.release_key,
                corpus_release_key=target_request.corpus_release_key,
                code="literature_refused",
                message="The configured literature retrieval service is unavailable.",
                upstream_code="embedding_provider_failed",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        if isinstance(response, LiteratureRetrievalError):
            try:
                serialized_error = response.model_dump_json()
                trusted_error = LiteratureRetrievalError.model_validate_json(serialized_error)
                if trusted_error.model_dump_json() != serialized_error:
                    raise ValueError("literature error changed during strict validation")
                selector_matches = (
                    trusted_error.requested_corpus_release_key
                    == invocation.request.corpus_release_key
                )
            except Exception:
                return _literature_integrity_error(
                    target_request,
                    route=route,
                    structured_executed=structured_executed,
                )
            if not selector_matches:
                return _literature_integrity_error(
                    target_request,
                    route=route,
                    structured_executed=structured_executed,
                    literature_executed=trusted_error.retrieval_executed,
                )
            return _error(
                route=route,
                release_key=target_request.release_key,
                corpus_release_key=target_request.corpus_release_key,
                code="literature_refused",
                message="The exact literature retrieval request was refused.",
                upstream_code=trusted_error.code,
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=trusted_error.retrieval_executed,
                    generation_executed=False,
                ),
            )
        try:
            if not isinstance(response, RetrievedChunks):
                raise TypeError("literature service returned an unknown response variant")
            serialized_response = response.model_dump_json()
            trusted_response = RetrievedChunks.model_validate_json(serialized_response)
            if trusted_response.model_dump_json() != serialized_response:
                raise ValueError("literature result changed during strict validation")
            response_matches = (
                trusted_response.corpus_release_key == corpus.corpus_release_key
                and trusted_response.corpus_manifest_sha256 == corpus.manifest_sha256
                and trusted_response.requested_top_k == invocation.request.top_k
                and trusted_response.anchors_applied == invocation.system_anchors
                and trusted_response.query_sha256
                == canonical_query_sha256(invocation.request, invocation.system_anchors)
            )
        except Exception:
            return _literature_integrity_error(
                target_request,
                route=route,
                structured_executed=structured_executed,
            )
        if not response_matches:
            return _error(
                route=route,
                release_key=target_request.release_key,
                corpus_release_key=target_request.corpus_release_key,
                code="literature_refused",
                message="The literature retrieval result does not match the authorized corpus.",
                upstream_code="corpus_manifest_invalid",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        return trusted_response

    def _generate(
        self,
        *,
        request: RagQueryRequest,
        route: Literal["literature", "hybrid"],
        retrieved: RetrievedChunks,
        query_success: QuerySuccess | None,
    ) -> tuple[GenerationComposition, ContextPack] | RagErrorResponse:
        structured_executed = query_success is not None
        try:
            context = self._context_builder(
                route=route,
                original_question=request.question,
                retrieved_chunks=retrieved,
                query_success=query_success,
            )
        except ContextBuildError as refusal:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code=refusal.code,
                message=refusal.public_message,
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        except Exception:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code="internal_error",
                message="The ContextPack could not be built safely.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        try:
            composer = self._composer_factory()
        except Exception:
            composer = None
        if composer is None:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code="llm_provider_unavailable",
                message="No approved LLM provider is configured.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=False,
                ),
            )
        try:
            composition = composer.compose(context)
            trusted_composition = validate_generation_composition(context, composition)
            return trusted_composition, context
        except GenerationComposerError as refusal:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code=refusal.code,
                message=refusal.public_message,
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=refusal.generation_executed,
                ),
            )
        except AnswerValidationError as refusal:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code="answer_validation_failed",
                message=refusal.public_message,
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=True,
                ),
            )
        except Exception:
            return _error(
                route=cast(RagRoute, route),
                release_key=request.release_key,
                corpus_release_key=request.corpus_release_key,
                code="internal_error",
                message="The generated answer could not be composed safely.",
                execution=ExecutionFlags(
                    structured_retrieval_executed=structured_executed,
                    literature_retrieval_executed=True,
                    generation_executed=True,
                ),
            )


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(
    *,
    route: RagRoute | None,
    release_key: str | None,
    corpus_release_key: str | None,
    code: RagErrorCode,
    message: str,
    upstream_code: str | None = None,
    execution: ExecutionFlags = _NO_EXECUTION,
) -> RagErrorResponse:
    return RagErrorResponse(
        route=route,
        requested_release_key=release_key,
        requested_corpus_release_key=corpus_release_key,
        code=code,
        message=message,
        upstream_code=upstream_code,
        execution=execution,
    )


def _structured_error(
    request: RagQueryRequest,
    response: ErrorResponse,
    *,
    route: Literal["structured", "hybrid"] = "structured",
    structured_question: str,
    invalid_structured_executed: bool,
) -> RagErrorResponse:
    try:
        serialized = response.model_dump_json()
        trusted = ErrorResponse.model_validate_json(serialized)
        if trusted.model_dump_json() != serialized:
            raise ValueError("structured error changed during strict validation")
        plan_matches = _structured_plan_matches_request(
            request,
            structured_question=structured_question,
            query_plan=trusted.query_plan,
        )
    except Exception:
        return _structured_integrity_error(
            request,
            route=route,
            structured_executed=invalid_structured_executed,
        )
    if not plan_matches:
        return _structured_integrity_error(
            request,
            route=route,
            structured_executed=trusted.fact_retrieval_executed,
        )
    return _error(
        route=route,
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="structured_refused",
        message="The exact structured query was refused.",
        upstream_code=trusted.error.code,
        execution=ExecutionFlags(
            structured_retrieval_executed=trusted.fact_retrieval_executed,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )


def _trusted_structured_success(
    request: RagQueryRequest,
    response: QuerySuccess,
    *,
    route: Literal["structured", "hybrid"],
    structured_question: str,
) -> QuerySuccess | RagErrorResponse:
    try:
        if not isinstance(response, QuerySuccess):
            raise TypeError("structured service returned an unknown response variant")
        serialized = response.model_dump_json()
        trusted = QuerySuccess.model_validate_json(serialized)
        if trusted.model_dump_json() != serialized:
            raise ValueError("structured result changed during strict validation")
        plan_matches = _structured_plan_matches_request(
            request,
            structured_question=structured_question,
            query_plan=trusted.query_plan,
        )
    except Exception:
        return _structured_integrity_error(
            request,
            route=route,
            structured_executed=True,
        )
    if not plan_matches:
        return _structured_integrity_error(
            request,
            route=route,
            structured_executed=True,
        )
    return trusted


def _structured_plan_matches_request(
    request: RagQueryRequest,
    *,
    structured_question: str,
    query_plan: StructuredPlan | None,
) -> bool:
    if query_plan is None:
        return True
    try:
        release_key = query_plan.release_key
        original_question = query_plan.original_question
        plan_page = cast(PageSpec | None, getattr(query_plan, "page", None))
    except Exception:
        return False
    if release_key != request.release_key or original_question != structured_question:
        return False
    if plan_page is None:
        return request.page is None
    return plan_page == (request.page or PageSpec())


def _structured_integrity_error(
    request: RagQueryRequest,
    *,
    route: Literal["structured", "hybrid"],
    structured_executed: bool,
) -> RagErrorResponse:
    return _error(
        route=route,
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="structured_refused",
        message="The structured query result does not match the exact routed request.",
        upstream_code="result_integrity_error",
        execution=ExecutionFlags(
            structured_retrieval_executed=structured_executed,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )


def _literature_integrity_error(
    request: RagQueryRequest,
    *,
    route: RagRoute,
    structured_executed: bool,
    literature_executed: bool = True,
) -> RagErrorResponse:
    return _error(
        route=route,
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="literature_refused",
        message="The literature retrieval result failed integrity validation.",
        upstream_code="corpus_manifest_invalid",
        execution=ExecutionFlags(
            structured_retrieval_executed=structured_executed,
            literature_retrieval_executed=literature_executed,
            generation_executed=False,
        ),
    )


def _literature_gate_error(
    request: RagQueryRequest,
    *,
    route: RagRoute,
    refusal: LiteratureRetrievalRefusal,
) -> RagErrorResponse:
    return _error(
        route=route,
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="literature_refused",
        message="The exact corpus release was refused.",
        upstream_code=refusal.code,
        execution=ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=refusal.retrieval_executed,
            generation_executed=False,
        ),
    )


def _binding_identity_error(
    request: RagQueryRequest,
    *,
    structured_executed: bool = False,
    literature_executed: bool = False,
) -> RagErrorResponse:
    return _error(
        route="hybrid",
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="hybrid_binding_unavailable",
        message="The exact release pair does not match its approved manifest identities.",
        execution=ExecutionFlags(
            structured_retrieval_executed=structured_executed,
            literature_retrieval_executed=literature_executed,
            generation_executed=False,
        ),
    )


def _answer_validation_error(
    request: RagQueryRequest,
    *,
    route: RagRoute,
    structured_executed: bool,
    context: ContextPack,
) -> RagErrorResponse:
    del context
    return _error(
        route=route,
        release_key=request.release_key,
        corpus_release_key=request.corpus_release_key,
        code="answer_validation_failed",
        message="The generated answer could not be validated safely.",
        execution=ExecutionFlags(
            structured_retrieval_executed=structured_executed,
            literature_retrieval_executed=True,
            generation_executed=True,
        ),
    )


__all__ = ["RagQueryApplication"]
