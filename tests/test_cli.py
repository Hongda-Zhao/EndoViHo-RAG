from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import eve_relation_rag.cli as cli_module
from eve_relation_rag.api.app import app as api_app
from eve_relation_rag.bootstrap import get_structured_query_application
from eve_relation_rag.cli import app
from tests.support.m2 import TEST_RELEASE_KEY, make_aggregate_application

runner = CliRunner()


def test_cli_and_api_return_semantically_identical_canonical_json(monkeypatch: object) -> None:
    application, _gate, _factory, _repository = make_aggregate_application(value=11)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli_module,
        "get_structured_query_application",
        lambda: application,
    )
    api_app.dependency_overrides[get_structured_query_application] = lambda: application
    question = "Count distinct included loci in this release."
    try:
        result = runner.invoke(
            app,
            [
                "structured",
                "query",
                "--release-key",
                TEST_RELEASE_KEY,
                "--question",
                question,
            ],
        )
        with TestClient(api_app) as client:
            api_response = client.post(
                "/v0/structured/query",
                json={"release_key": TEST_RELEASE_KEY, "question": question},
            )
    finally:
        api_app.dependency_overrides.clear()

    assert result.exit_code == 0, result.output
    assert api_response.status_code == 200
    assert json.loads(result.stdout) == api_response.json()


def test_cli_plan_is_json_and_never_executes_fact_repository(monkeypatch: object) -> None:
    application, _gate, _factory, repository = make_aggregate_application()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli_module,
        "get_structured_query_application",
        lambda: application,
    )

    result = runner.invoke(
        app,
        [
            "structured",
            "plan",
            "--release-key",
            TEST_RELEASE_KEY,
            "--question",
            "Count distinct included loci in this release.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["response_kind"] == "plan_success"
    assert repository.calls == []


def test_cli_request_error_goes_to_stderr_with_exit_two() -> None:
    result = runner.invoke(
        app,
        [
            "structured",
            "plan",
            "--release-key",
            "latest",
            "--question",
            "List all loci in this release.",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "release_key_invalid"


def test_cli_click_limit_error_uses_stable_json_envelope() -> None:
    result = runner.invoke(
        app,
        [
            "structured",
            "query",
            "--release-key",
            TEST_RELEASE_KEY,
            "--question",
            "List all loci in this release.",
            "--limit",
            "0",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert body["response_kind"] == "error"
    assert body["error"]["code"] == "limit_invalid"
    assert body["fact_retrieval_executed"] is False


def test_cli_missing_required_option_uses_stable_json_envelope() -> None:
    result = runner.invoke(
        app,
        [
            "structured",
            "plan",
            "--release-key",
            TEST_RELEASE_KEY,
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    body = json.loads(result.stderr)
    assert body["response_kind"] == "error"
    assert body["error"]["code"] == "request_schema_invalid"
    assert body["error"]["field_errors"][0]["field"] == "question"
