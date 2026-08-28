from __future__ import annotations

import gzip
import json

import httpx
import pytest

from eve_relation_rag.demo.client import (
    DemoClientConfig,
    DemoClientError,
    submit_query,
)
from eve_relation_rag.demo.examples import load_demo_examples
from eve_relation_rag.generation.rendering import render_structured_answer_text
from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    RagErrorResponse,
    RagQueryRequest,
    StructuredRouteAnswer,
    canonical_model_json,
)
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from tests.support.m4 import make_structured_success


def _request() -> RagQueryRequest:
    return load_demo_examples()[3].request


def _error_response() -> RagErrorResponse:
    return RagErrorResponse(
        route="unsupported",
        requested_release_key=None,
        requested_corpus_release_key=None,
        code="unsupported_request",
        message="The request is outside the approved V0 grammar.",
        upstream_code=None,
        execution=ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )


def test_client_posts_only_the_strict_non_null_request_once() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url == httpx.URL("http://api.internal:8000/v0/query")
        assert request.headers["accept"] == "application/json"
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["content-type"] == "application/json"
        assert set(body) == {"request_schema_version", "question"}
        assert "route" not in body
        return httpx.Response(
            422,
            content=serialize_rag_response(_error_response()),
            headers={"content-type": "application/json"},
        )

    result = submit_query(
        _request(),
        config=DemoClientConfig(api_base_url="http://api.internal:8000"),
        transport=httpx.MockTransport(handler),
    )

    assert len(calls) == 1
    assert result.status_code == 422
    assert result.response == _error_response()


@pytest.mark.parametrize(
    "value",
    (
        "file:///tmp/api",
        "http://user:secret@example.test",
        "http://example.test/v0",
        "http://example.test?redirect=x",
        "http://example.test:bad",
        "http://example.test:0",
        "http://example.test:65536",
        "http://[::1",
        "//example.test",
    ),
)
def test_config_rejects_non_origin_api_values(value: str) -> None:
    with pytest.raises(ValueError, match="credential-free HTTP"):
        DemoClientConfig(api_base_url=value)


def test_config_uses_safe_local_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVE_RAG_DEMO_API_BASE_URL", raising=False)
    assert DemoClientConfig.from_environment().api_base_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("content", "headers", "message"),
    (
        (b"not-json", {}, "does not match"),
        (b"", {}, "empty response"),
        (b"{}", {"content-length": "2097153"}, "safety limit"),
        (b"{}", {"content-length": "invalid"}, "invalid response length"),
        (
            gzip.compress(b"{}"),
            {"content-encoding": "gzip"},
            "unsupported response encoding",
        ),
    ),
)
def test_client_sanitizes_invalid_or_oversized_responses(
    content: bytes,
    headers: dict[str, str],
    message: str,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(503, content=content, headers=headers)
    )
    with pytest.raises(DemoClientError, match=message):
        submit_query(
            _request(),
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=transport,
        )


def test_client_enforces_streamed_byte_cap_without_returning_body() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, content=b"x" * 2049))
    with pytest.raises(DemoClientError, match="safety limit") as caught:
        submit_query(
            _request(),
            config=DemoClientConfig(
                api_base_url="http://api.test",
                max_response_bytes=2048,
            ),
            transport=transport,
        )
    assert "xxx" not in str(caught.value)


def test_client_sanitizes_network_failure_and_does_not_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("sensitive upstream detail", request=request)

    with pytest.raises(DemoClientError, match="local API could not be reached") as caught:
        submit_query(
            _request(),
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=httpx.MockTransport(handler),
        )

    assert calls == 1
    assert "sensitive" not in str(caught.value)


def test_client_rejects_status_envelope_mismatch() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=serialize_rag_response(_error_response()))
    )
    with pytest.raises(DemoClientError, match="status does not match"):
        submit_query(
            _request(),
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=transport,
        )


def test_client_revalidates_canonical_rendering_not_only_the_schema() -> None:
    question = "Count distinct included loci in this release."
    success = make_structured_success("aggregate", structured_question=question)
    request = RagQueryRequest(
        release_key=success.query_plan.release_key,
        corpus_release_key=None,
        question=question,
        page=None,
        literature_top_k=None,
    )
    valid = StructuredRouteAnswer(
        original_request=request,
        query_success=success,
        structured_text=render_structured_answer_text(success),
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )
    tampered = valid.model_copy(update={"structured_text": "A rewritten answer."})
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=canonical_model_json(tampered))
    )

    with pytest.raises(DemoClientError, match="does not match the V0 contract"):
        submit_query(
            request,
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=transport,
        )


def test_client_rejects_a_valid_response_for_a_different_request() -> None:
    question = "Count distinct included loci in this release."
    success = make_structured_success("aggregate", structured_question=question)
    structured_request = RagQueryRequest(
        release_key=success.query_plan.release_key,
        corpus_release_key=None,
        question=question,
        page=None,
        literature_top_k=None,
    )
    response = StructuredRouteAnswer(
        original_request=structured_request,
        query_success=success,
        structured_text=render_structured_answer_text(success),
        execution=ExecutionFlags(
            structured_retrieval_executed=True,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=serialize_rag_response(response))
    )

    with pytest.raises(DemoClientError, match="does not belong"):
        submit_query(
            _request(),
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=transport,
        )


def test_client_revalidates_forged_request_before_network_io() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, content=serialize_rag_response(_error_response()))

    forged = _request().model_copy(update={"literature_top_k": 9})
    with pytest.raises(DemoClientError, match="demo request does not match"):
        submit_query(
            forged,
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=httpx.MockTransport(handler),
        )
    assert calls == 0


@pytest.mark.parametrize("code", ("request_schema_invalid", "route_request_mismatch"))
def test_client_rejects_an_error_that_does_not_match_the_deterministic_request(
    code: str,
) -> None:
    response = RagErrorResponse(
        route=None if code == "request_schema_invalid" else "unsupported",
        requested_release_key=None,
        requested_corpus_release_key=None,
        code=code,  # type: ignore[arg-type]
        message="The request was refused by a different decision path.",
        upstream_code=None,
        execution=ExecutionFlags(
            structured_retrieval_executed=False,
            literature_retrieval_executed=False,
            generation_executed=False,
        ),
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            422,
            content=serialize_rag_response(response),
        )
    )

    with pytest.raises(DemoClientError, match="does not belong"):
        submit_query(
            _request(),
            config=DemoClientConfig(api_base_url="http://api.test"),
            transport=transport,
        )
