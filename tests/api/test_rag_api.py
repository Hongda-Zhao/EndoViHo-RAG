from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from eve_relation_rag.api.app import app
from eve_relation_rag.bootstrap import get_rag_query_application
from eve_relation_rag.generation.rendering import render_structured_answer_text
from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    RagErrorResponse,
    RagQueryRequest,
    StructuredRouteAnswer,
)
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from eve_relation_rag.hybrid.transport import rag_http_status_for
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.retrieval.structured.results import QuerySuccess
from tests.support.m2 import TEST_RELEASE_KEY, make_aggregate_application


class _FakeRagApplication:
    def __init__(self, response: StructuredRouteAnswer | RagErrorResponse) -> None:
        self.response = response
        self.calls: list[RagQueryRequest] = []

    def query(self, request: RagQueryRequest) -> StructuredRouteAnswer | RagErrorResponse:
        self.calls.append(request)
        return self.response


def _structured_answer() -> StructuredRouteAnswer:
    question = "Count distinct included loci in this release."
    structured, _gate, _factory, _repository = make_aggregate_application(value=7)
    response = structured.query(
        StructuredQueryRequest(release_key=TEST_RELEASE_KEY, question=question)
    )
    assert isinstance(response, QuerySuccess)
    return StructuredRouteAnswer(
        response_schema_version="structured-route-answer-v1",
        response_kind="structured_route_answer",
        route="structured",
        original_request=RagQueryRequest(
            request_schema_version="rag-query-request-v1",
            release_key=TEST_RELEASE_KEY,
            question=question,
        ),
        query_success=response,
        structured_text=render_structured_answer_text(response),
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )


def _error(
    code: str,
    *,
    upstream_code: str | None = None,
) -> RagErrorResponse:
    route = None if code == "request_schema_invalid" else "hybrid"
    generation_executed = code in {
        "generation_failed",
        "generated_draft_invalid",
        "answer_validation_failed",
    }
    literature_executed = generation_executed or code in {
        "context_integrity_error",
        "context_too_large",
        "llm_provider_unavailable",
    }
    return RagErrorResponse.model_validate(
        {
            "response_schema_version": "rag-error-v1",
            "response_kind": "error",
            "route": route,
            "requested_release_key": TEST_RELEASE_KEY,
            "requested_corpus_release_key": "corpus:endoviho-rag:v0:20991231:999",
            "code": code,
            "message": "The routed request was refused.",
            "upstream_code": upstream_code,
            "execution": {
                "structured_retrieval_executed": literature_executed,
                "literature_retrieval_executed": literature_executed,
                "generation_executed": generation_executed,
            },
        }
    )


def test_rag_api_returns_the_canonical_application_response() -> None:
    expected = _structured_answer()
    application = _FakeRagApplication(expected)
    app.dependency_overrides[get_rag_query_application] = lambda: application
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v0/query",
                json=expected.original_request.model_dump(mode="json"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.text == serialize_rag_response(expected)
    assert response.json() == json.loads(serialize_rag_response(expected))
    assert application.calls == [expected.original_request]


def test_rag_api_maps_application_refusal_without_rewriting_it() -> None:
    expected = _error("hybrid_binding_unavailable")
    application = _FakeRagApplication(expected)
    app.dependency_overrides[get_rag_query_application] = lambda: application
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v0/query",
                json={
                    "release_key": TEST_RELEASE_KEY,
                    "corpus_release_key": "corpus:endoviho-rag:v0:20991231:999",
                    "question": (
                        "Count distinct included loci in this release."
                        " and explain the literature evidence"
                    ),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.text == serialize_rag_response(expected)
    assert application.calls[0].request_schema_version == "rag-query-request-v1"


def test_rag_request_validation_uses_only_the_m4_error_envelope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v0/query",
            json={
                "release_key": TEST_RELEASE_KEY,
                "corpus_release_key": "corpus:endoviho-rag:v0:20991231:999",
                "question": "Explain the literature evidence for ViralRecall",
                "sql": "SELECT 1",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "request_schema_invalid",
        "execution": {
            "generation_executed": False,
            "literature_retrieval_executed": False,
            "structured_retrieval_executed": False,
        },
        "message": "The routed RAG request is invalid.",
        "requested_corpus_release_key": "corpus:endoviho-rag:v0:20991231:999",
        "requested_release_key": TEST_RELEASE_KEY,
        "response_kind": "error",
        "response_schema_version": "rag-error-v1",
        "route": None,
        "upstream_code": None,
    }


def test_invalid_release_alias_is_not_echoed_by_validation_envelope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v0/query",
            json={
                "release_key": "latest",
                "question": "List all loci in this release.",
            },
        )

    assert response.status_code == 422
    assert response.json()["requested_release_key"] is None


def test_rag_dependency_failure_is_sanitized_without_affecting_old_handlers() -> None:
    def fail_to_compose() -> None:
        raise RuntimeError("secret provider configuration")

    app.dependency_overrides[get_rag_query_application] = fail_to_compose
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v0/query",
                json={
                    "release_key": TEST_RELEASE_KEY,
                    "question": "List all loci in this release.",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text


def test_rag_final_response_validation_failure_is_sanitized() -> None:
    expected = _structured_answer()
    tampered = expected.model_copy(update={"structured_text": "Tampered structured result."})
    application = _FakeRagApplication(tampered)
    app.dependency_overrides[get_rag_query_application] = lambda: application
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v0/query",
                json=expected.original_request.model_dump(mode="json"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (_error("request_schema_invalid"), 422),
        (_error("structured_refused", upstream_code="release_not_found"), 404),
        (_error("structured_refused", upstream_code="release_not_published"), 409),
        (_error("structured_refused", upstream_code="result_integrity_error"), 500),
        (_error("literature_refused", upstream_code="corpus_not_found"), 404),
        (_error("literature_refused", upstream_code="embedding_provider_failed"), 503),
        (_error("literature_refused", upstream_code="retrieval_failed"), 500),
        (_error("hybrid_binding_unavailable"), 409),
        (_error("llm_provider_unavailable"), 503),
        (_error("generation_failed"), 503),
        (_error("internal_error"), 500),
    ],
)
def test_rag_http_status_mapping_is_stable(
    response: RagErrorResponse,
    expected_status: int,
) -> None:
    assert rag_http_status_for(response) == expected_status
