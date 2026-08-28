from fastapi.testclient import TestClient

from eve_relation_rag.api.app import app


def test_health_endpoint_returns_stable_english_payload() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "EVE Relation RAG",
        "version": "V0",
    }


def test_legacy_unversioned_query_route_is_not_implemented() -> None:
    with TestClient(app) as client:
        assert client.get("/query").status_code == 404


def test_openapi_description_reports_v0_surface_and_activation_boundary() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["description"] == (
        "V0 routed structured, literature, and hybrid RAG surface over exact immutable releases. "
        "The current structured pilot and generation path remain fail-closed pending approval."
    )
    assert "/v0/structured/plan" in response.json()["paths"]
    assert "/v0/structured/query" in response.json()["paths"]
