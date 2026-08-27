from __future__ import annotations

from fastapi.testclient import TestClient

from eve_relation_rag.api.app import app
from eve_relation_rag.bootstrap import get_structured_query_application
from tests.support.m2 import TEST_RELEASE_KEY, make_aggregate_application


def test_plan_and_query_routes_share_question_first_application() -> None:
    application, gate, _factory, repository = make_aggregate_application(value=5)
    app.dependency_overrides[get_structured_query_application] = lambda: application
    payload = {
        "request_schema_version": "structured-query-request-v1",
        "release_key": TEST_RELEASE_KEY,
        "question": "Count distinct included loci in this release.",
    }
    try:
        with TestClient(app) as client:
            planned = client.post("/v0/structured/plan", json=payload)
            queried = client.post("/v0/structured/query", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert planned.status_code == 200
    assert planned.json()["response_kind"] == "plan_success"
    assert planned.json()["fact_retrieval_executed"] is False
    assert queried.status_code == 200
    assert queried.json()["response_kind"] == "query_success"
    assert queried.json()["structured_result"]["data"]["value"] == 5
    assert len(repository.calls) == 1
    assert gate.calls == [TEST_RELEASE_KEY, TEST_RELEASE_KEY]


def test_fastapi_validation_uses_stable_error_envelope_and_rejects_unknown_fields() -> None:
    payload = {
        "release_key": TEST_RELEASE_KEY,
        "question": "List all loci in this release.",
        "sql": "SELECT * FROM eve_locus",
    }

    with TestClient(app) as client:
        response = client.post("/v0/structured/plan", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["response_kind"] == "error"
    assert body["error"]["code"] == "request_schema_invalid"
    assert body["fact_retrieval_executed"] is False
    assert body["structured_result"] is None
    assert body["error"]["field_errors"][0]["field"] == "sql"


def test_malformed_cursor_maps_to_http_400_before_application() -> None:
    payload = {
        "release_key": TEST_RELEASE_KEY,
        "question": "List all loci in this release.",
        "page": {"limit": 50, "cursor": "not+base64url"},
    }

    with TestClient(app) as client:
        response = client.post("/v0/structured/query", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cursor_invalid"


def test_plan_authenticates_well_formed_cursor_before_returning_success() -> None:
    application, _gate, _factory, repository = make_aggregate_application()
    app.dependency_overrides[get_structured_query_application] = lambda: application
    payload = {
        "release_key": TEST_RELEASE_KEY,
        "question": "List all loci in this release.",
        "page": {"limit": 50, "cursor": "abc"},
    }
    try:
        with TestClient(app) as client:
            response = client.post("/v0/structured/plan", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cursor_invalid"
    assert response.json()["fact_retrieval_executed"] is False
    assert repository.calls == []


def test_overlong_embedded_lineage_key_is_a_pre_fact_http_422() -> None:
    application, _gate, _factory, repository = make_aggregate_application()
    app.dependency_overrides[get_structured_query_application] = lambda: application
    payload = {
        "release_key": TEST_RELEASE_KEY,
        "question": (
            "List loci assigned exactly to source lineage term "
            f"{'t' * 256} in snapshot snapshot:synthetic."
        ),
    }
    try:
        with TestClient(app) as client:
            response = client.post("/v0/structured/plan", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_schema_invalid"
    assert response.json()["fact_retrieval_executed"] is False
    assert repository.calls == []


def test_release_key_is_required_in_project_envelope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v0/structured/plan",
            json={"question": "List all loci in this release."},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "release_required"


def test_dependency_failure_is_sanitized_as_project_envelope() -> None:
    def fail_to_compose() -> None:
        raise RuntimeError("secret configuration detail")

    app.dependency_overrides[get_structured_query_application] = fail_to_compose
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v0/structured/plan",
                json={
                    "release_key": TEST_RELEASE_KEY,
                    "question": "List all loci in this release.",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "structured_query_failed",
        "field_errors": [],
        "message": "The structured query could not be completed.",
        "suggestions": [],
    }
