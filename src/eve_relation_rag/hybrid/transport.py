"""Stable HTTP/CLI mappings and safe transport errors for Milestone 4."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    RagErrorResponse,
)
from eve_relation_rag.literature.contracts import CorpusReleaseKey
from eve_relation_rag.planning.query_plans import PublishedReleaseKey

_PUBLISHED_RELEASE_KEY_ADAPTER: TypeAdapter[str] = TypeAdapter(PublishedReleaseKey)
_CORPUS_RELEASE_KEY_ADAPTER: TypeAdapter[str] = TypeAdapter(CorpusReleaseKey)

_UPSTREAM_NOT_FOUND = frozenset(
    {
        "release_not_found",
        "entity_unresolved",
        "entity_not_in_release",
        "corpus_not_found",
    }
)
_UPSTREAM_CONFLICT = frozenset(
    {
        "release_not_published",
        "release_dependencies_incomplete",
        "release_manifest_invalid",
        "entity_ambiguous",
        "corpus_not_published",
        "corpus_manifest_invalid",
        "corpus_receipt_invalid",
        "corpus_incomplete",
        "document_license_not_approved",
        "embedding_incomplete",
        "embedding_model_mismatch",
    }
)
_UPSTREAM_CURSOR = frozenset({"cursor_invalid", "cursor_plan_mismatch"})
_UPSTREAM_ENTITY = frozenset({"entity_unresolved", "entity_ambiguous", "entity_not_in_release"})
_UPSTREAM_DEPENDENCY = frozenset({"embedding_provider_failed"})
_UPSTREAM_SERVER_FAILURE = frozenset(
    {
        "query_plan_version_unsupported",
        "compiler_constraint_unmapped",
        "result_integrity_error",
        "structured_query_failed",
        "retrieval_failed",
    }
)
_UPSTREAM_RELEASE = frozenset(
    {
        "release_not_found",
        "release_not_published",
        "release_dependencies_incomplete",
        "release_manifest_invalid",
        "corpus_not_found",
        "corpus_not_published",
        "corpus_manifest_invalid",
        "corpus_receipt_invalid",
        "corpus_incomplete",
        "document_license_not_approved",
        "embedding_incomplete",
        "embedding_model_mismatch",
    }
)
_DEPENDENCY_CODES = frozenset({"llm_provider_unavailable", "generation_failed"})
_CONFLICT_CODES = frozenset({"hybrid_binding_unavailable"})
_SERVER_FAILURE_CODES = frozenset(
    {
        "anchor_integrity_error",
        "context_integrity_error",
        "generated_draft_invalid",
        "answer_validation_failed",
        "internal_error",
    }
)


def rag_http_status_for(response: RagErrorResponse) -> int:
    """Map one strict M4 error to its deterministic HTTP status."""

    if response.code in _DEPENDENCY_CODES:
        return 503
    if response.code == "internal_error":
        return 500
    if response.code in {"structured_refused", "literature_refused"}:
        if response.upstream_code in _UPSTREAM_CURSOR:
            return 400
        if response.upstream_code in _UPSTREAM_NOT_FOUND:
            return 404
        if response.upstream_code in _UPSTREAM_CONFLICT:
            return 409
        if response.upstream_code in _UPSTREAM_DEPENDENCY:
            return 503
        if response.upstream_code in _UPSTREAM_SERVER_FAILURE:
            return 500
        return 422
    if response.code in _CONFLICT_CODES:
        return 409
    if response.code in _SERVER_FAILURE_CODES:
        return 422
    return 422


def rag_cli_exit_code_for(response: RagErrorResponse) -> int:
    """Map one strict M4 error to the stable CLI exit contract."""

    if response.code in {"structured_refused", "literature_refused"}:
        if response.upstream_code in _UPSTREAM_ENTITY:
            return 3
        if response.upstream_code in _UPSTREAM_RELEASE:
            return 4
        if (
            response.upstream_code in _UPSTREAM_DEPENDENCY
            or response.upstream_code in _UPSTREAM_SERVER_FAILURE
        ):
            return 5
        return 2
    if response.code == "hybrid_binding_unavailable":
        return 4
    if response.code in _DEPENDENCY_CODES or response.code in _SERVER_FAILURE_CODES:
        return 5
    return 2


def rag_request_validation_response(
    _errors: Sequence[Mapping[str, Any]],
    *,
    body: object = None,
) -> RagErrorResponse:
    """Return a safe M4 schema error, retaining only syntactically valid exact keys."""

    release_key = None
    corpus_release_key = None
    if isinstance(body, Mapping):
        release_key = _validated_key(
            body.get("release_key"),
            _PUBLISHED_RELEASE_KEY_ADAPTER,
        )
        corpus_release_key = _validated_key(
            body.get("corpus_release_key"),
            _CORPUS_RELEASE_KEY_ADAPTER,
        )
    return RagErrorResponse(
        response_schema_version="rag-error-v1",
        response_kind="error",
        route=None,
        requested_release_key=release_key,
        requested_corpus_release_key=corpus_release_key,
        code="request_schema_invalid",
        message="The routed RAG request is invalid.",
        upstream_code=None,
        execution=_not_executed(),
    )


def rag_internal_error_response() -> RagErrorResponse:
    """Return a sanitized M4 failure without exception or configuration detail."""

    return RagErrorResponse(
        response_schema_version="rag-error-v1",
        response_kind="error",
        route=None,
        requested_release_key=None,
        requested_corpus_release_key=None,
        code="internal_error",
        message="The routed RAG request could not be completed.",
        upstream_code=None,
        execution=_not_executed(),
    )


def _validated_key(value: object, adapter: TypeAdapter[Any]) -> Any | None:
    try:
        return adapter.validate_python(value, strict=True)
    except ValidationError:
        return None


def _not_executed() -> ExecutionFlags:
    return ExecutionFlags(
        structured_retrieval_executed=False,
        literature_retrieval_executed=False,
        generation_executed=False,
    )


__all__ = [
    "rag_cli_exit_code_for",
    "rag_http_status_for",
    "rag_internal_error_response",
    "rag_request_validation_response",
]
