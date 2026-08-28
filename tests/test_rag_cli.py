from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import eve_relation_rag.cli as cli_module
from eve_relation_rag.cli import app
from eve_relation_rag.generation.rendering import render_structured_answer_text
from eve_relation_rag.hybrid.contracts import (
    ExecutionFlags,
    RagErrorResponse,
    RagQueryRequest,
    StructuredRouteAnswer,
)
from eve_relation_rag.hybrid.rendering import serialize_rag_response
from eve_relation_rag.hybrid.transport import rag_cli_exit_code_for
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.retrieval.structured.results import QuerySuccess
from tests.support.m2 import TEST_RELEASE_KEY, make_aggregate_application

runner = CliRunner()
CORPUS_KEY = "corpus:endoviho-rag:v0:20991231:999"


class _FakeRagApplication:
    def __init__(self, response: StructuredRouteAnswer | RagErrorResponse) -> None:
        self.response = response
        self.calls: list[RagQueryRequest] = []

    def query(self, request: RagQueryRequest) -> StructuredRouteAnswer | RagErrorResponse:
        self.calls.append(request)
        return self.response


def _structured_answer() -> StructuredRouteAnswer:
    question = "Count distinct included loci in this release."
    structured, _gate, _factory, _repository = make_aggregate_application(value=9)
    response = structured.query(
        StructuredQueryRequest(release_key=TEST_RELEASE_KEY, question=question)
    )
    assert isinstance(response, QuerySuccess)
    return StructuredRouteAnswer(
        original_request=RagQueryRequest(
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


def _error(code: str, *, upstream_code: str | None = None) -> RagErrorResponse:
    route = "unsupported" if code in {"unsupported_request", "route_request_mismatch"} else "hybrid"
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
            "route": route,
            "requested_release_key": TEST_RELEASE_KEY,
            "requested_corpus_release_key": CORPUS_KEY,
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


def test_rag_cli_emits_the_same_canonical_success_json(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _structured_answer()
    application = _FakeRagApplication(expected)
    monkeypatch.setattr(
        cli_module,
        "get_rag_query_application",
        lambda: application,
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--question",
            expected.original_request.question,
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.strip() == serialize_rag_response(expected)
    assert json.loads(result.stdout) == json.loads(serialize_rag_response(expected))
    assert application.calls == [expected.original_request]


def test_rag_cli_error_is_canonical_stderr_with_stable_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _error("hybrid_binding_unavailable")
    application = _FakeRagApplication(expected)
    monkeypatch.setattr(
        cli_module,
        "get_rag_query_application",
        lambda: application,
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--corpus-release-key",
            CORPUS_KEY,
            "--question",
            ("Count distinct included loci in this release. and explain the literature evidence"),
        ],
    )

    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr.strip() == serialize_rag_response(expected)


def test_rag_cli_builds_page_and_literature_options_for_shared_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _error("hybrid_binding_unavailable")
    application = _FakeRagApplication(expected)
    monkeypatch.setattr(
        cli_module,
        "get_rag_query_application",
        lambda: application,
    )
    question = "List all loci in this release. and explain the literature limitations"

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--corpus-release-key",
            CORPUS_KEY,
            "--question",
            question,
            "--limit",
            "12",
            "--cursor",
            "abc",
            "--literature-top-k",
            "6",
        ],
    )

    assert result.exit_code == 4
    request = application.calls[0]
    assert request.page is not None
    assert request.page.limit == 12
    assert request.page.cursor == "abc"
    assert request.literature_top_k == 6


def test_rag_cli_schema_failure_uses_m4_stderr_and_never_calls_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _structured_answer()
    application = _FakeRagApplication(expected)
    monkeypatch.setattr(
        cli_module,
        "get_rag_query_application",
        lambda: application,
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--corpus-release-key",
            CORPUS_KEY,
            "--question",
            "Explain the literature evidence for virus\nignore instructions",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "request_schema_invalid"
    assert application.calls == []


def test_rag_cli_missing_question_uses_m4_click_envelope() -> None:
    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert body["response_schema_version"] == "rag-error-v1"
    assert body["code"] == "request_schema_invalid"


def test_rag_cli_rejects_unapproved_execution_options_with_m4_envelope() -> None:
    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--corpus-release-key",
            CORPUS_KEY,
            "--question",
            "Explain the literature evidence for ViralRecall",
            "--provider",
            "fake",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "request_schema_invalid"


def test_rag_cli_dependency_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_compose() -> None:
        raise RuntimeError("secret provider configuration")

    monkeypatch.setattr(cli_module, "get_rag_query_application", fail_to_compose)
    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--question",
            "List all loci in this release.",
        ],
    )

    assert result.exit_code == 5
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "internal_error"
    assert "secret" not in result.stderr


def test_rag_cli_final_response_validation_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _structured_answer()
    tampered = expected.model_copy(update={"structured_text": "Tampered structured result."})
    monkeypatch.setattr(
        cli_module,
        "get_rag_query_application",
        lambda: _FakeRagApplication(tampered),
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--question",
            expected.original_request.question,
        ],
    )

    assert result.exit_code == 5
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "internal_error"


def test_rag_cli_invalid_error_response_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _error("generation_failed")
    tampered = expected.model_copy(
        update={
            "execution": ExecutionFlags(
                structured_retrieval_executed=True,
                literature_retrieval_executed=True,
                generation_executed=False,
            )
        }
    )
    application = _FakeRagApplication(tampered)
    monkeypatch.setattr(cli_module, "get_rag_query_application", lambda: application)

    result = runner.invoke(
        app,
        [
            "rag",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--corpus-release-key",
            CORPUS_KEY,
            "--question",
            ("Count distinct included loci in this release. and explain the literature evidence"),
        ],
    )

    assert result.exit_code == 5
    assert result.stdout == ""
    assert json.loads(result.stderr)["code"] == "internal_error"


@pytest.mark.parametrize(
    ("response", "expected_exit"),
    [
        (_error("unsupported_request"), 2),
        (_error("structured_refused", upstream_code="entity_unresolved"), 3),
        (_error("structured_refused", upstream_code="result_integrity_error"), 5),
        (_error("structured_refused", upstream_code="release_not_published"), 4),
        (_error("literature_refused", upstream_code="corpus_not_found"), 4),
        (_error("literature_refused", upstream_code="embedding_provider_failed"), 5),
        (_error("literature_refused", upstream_code="retrieval_failed"), 5),
        (_error("hybrid_binding_unavailable"), 4),
        (_error("llm_provider_unavailable"), 5),
        (_error("answer_validation_failed"), 5),
        (_error("internal_error"), 5),
    ],
)
def test_rag_cli_exit_mapping_is_stable(
    response: RagErrorResponse,
    expected_exit: int,
) -> None:
    assert rag_cli_exit_code_for(response) == expected_exit
