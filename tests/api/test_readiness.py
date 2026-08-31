"""HTTP readiness status and redaction tests."""

from fastapi.testclient import TestClient

from eve_relation_rag.api.app import app
from eve_relation_rag.bootstrap import get_readiness_service
from eve_relation_rag.operations.readiness import (
    READINESS_CHECK_NAMES,
    ReadinessCheckResult,
    ReadinessReport,
)


class _Service:
    def __init__(self, status: str) -> None:
        self.status = status

    def check(self) -> ReadinessReport:
        return _report(self.status)


def _report(status: str) -> ReadinessReport:
    return ReadinessReport.model_validate(
        {
            "readiness_schema_version": "v0-readiness-v1",
            "status": status,
            "service": "EVE Relation RAG",
            "version": "V0",
            "checks": tuple(
                ReadinessCheckResult(
                    check=name,
                    status="ready" if status == "ready" else "not_ready",
                )
                for name in READINESS_CHECK_NAMES
            ),
        }
    )


def test_ready_endpoint_maps_ready_and_not_ready_to_200_and_503() -> None:
    try:
        app.dependency_overrides[get_readiness_service] = lambda: _Service("ready")
        with TestClient(app) as client:
            ready = client.get("/ready")
        app.dependency_overrides[get_readiness_service] = lambda: _Service("not_ready")
        with TestClient(app) as client:
            not_ready = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert not_ready.status_code == 503
    assert not_ready.json()["status"] == "not_ready"
    assert tuple(check["check"] for check in not_ready.json()["checks"]) == (READINESS_CHECK_NAMES)


def test_readiness_dependency_failure_is_sanitized() -> None:
    def fail() -> None:
        raise RuntimeError("database-password-and-provider-payload")

    app.dependency_overrides[get_readiness_service] = fail
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "database-password" not in response.text
    assert all(check["status"] == "not_ready" for check in response.json()["checks"])
