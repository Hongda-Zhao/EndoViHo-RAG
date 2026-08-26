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


def test_scientific_query_route_is_not_implemented() -> None:
    with TestClient(app) as client:
        assert client.get("/query").status_code == 404
