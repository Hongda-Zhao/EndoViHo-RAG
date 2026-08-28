from __future__ import annotations

import json
from pathlib import Path

import pytest
from click import unstyle
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import eve_relation_rag.cli as cli_module
from eve_relation_rag.api.app import app as api_app
from eve_relation_rag.bootstrap import get_structured_query_application
from eve_relation_rag.cli import app
from eve_relation_rag.literature.contracts import RetrievedChunks
from tests.support.m2 import TEST_RELEASE_KEY, make_aggregate_application

runner = CliRunner()
LITERATURE_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "literature"


class _FakeLiteratureService:
    def retrieve(self, invocation: object) -> RetrievedChunks:
        del invocation
        return RetrievedChunks(
            result_schema_version="retrieved-chunks-v2",
            status="ok",
            corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
            corpus_manifest_sha256="a" * 64,
            retrieval_policy_key=(
                "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
            ),
            embedding_model_key=(
                "embedding:hf:BAAI-bge-small-en-v1.5@"
                "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
            ),
            query_sha256="b" * 64,
            requested_top_k=8,
            returned_count=0,
            retrieval_executed=True,
            anchor_mode="none",
            anchors_applied=(),
            warnings=("no_chunks_retrieved",),
            chunks=(),
        )


def test_literature_admin_commands_expose_an_explicit_uv_lock_path() -> None:
    for command in ("benchmark", "corpus-validate"):
        result = runner.invoke(app, ["literature", command, "--help"])

        assert result.exit_code == 0, result.output
        help_text = unstyle(result.stdout)
        assert "--uv-lock-path" in help_text
        assert "Exact approved uv.lock" in help_text
        assert "required outside a source" in help_text


def test_uv_lock_path_fails_closed_outside_a_source_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_cli = tmp_path / "lib" / "python3.12" / "site-packages" / "cli.py"
    monkeypatch.setattr(cli_module, "__file__", str(installed_cli))

    with pytest.raises(RuntimeError, match="provide --uv-lock-path"):
        cli_module._resolve_uv_lock_path(None)
    explicit = tmp_path / "approved.uv.lock"
    explicit.write_text("version = 1\n", encoding="utf-8")
    assert cli_module._resolve_uv_lock_path(explicit) == explicit


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


def test_literature_retrieve_emits_stable_typed_json(monkeypatch: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli_module,
        "get_literature_retrieval_service",
        lambda: _FakeLiteratureService(),
    )

    result = runner.invoke(
        app,
        [
            "literature",
            "retrieve",
            "--corpus-release-key",
            "corpus:endoviho-rag:v0:20990101:001",
            "--question",
            "What methods were used?",
        ],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["result_schema_version"] == "retrieved-chunks-v2"
    assert body["retrieval_executed"] is True


def test_literature_manifest_validate_requires_exact_approved_checksum() -> None:
    manifest_path = LITERATURE_FIXTURE_ROOT / "synthetic_corpus_manifest.json"
    valid = runner.invoke(
        app,
        [
            "literature",
            "manifest-validate",
            "--manifest-path",
            str(manifest_path),
            "--approved-manifest-sha256",
            "887bd65b23cc9eca80657250dd0a5233e48c58a5c6a3072b13f2278485ee0b1a",
        ],
    )
    invalid = runner.invoke(
        app,
        [
            "literature",
            "manifest-validate",
            "--manifest-path",
            str(manifest_path),
            "--approved-manifest-sha256",
            "0" * 64,
        ],
    )

    assert valid.exit_code == 0
    assert json.loads(valid.stdout)["status"] == "valid"
    assert invalid.exit_code == 2
    assert json.loads(invalid.stderr)["status"] == "error"
