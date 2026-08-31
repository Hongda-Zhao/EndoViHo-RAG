"""Fail-closed V0 readiness dependency tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from sqlalchemy import Engine

from eve_relation_rag.operations.database_role import (
    DatabaseRoleAudit,
    _build_database_role_audit,
)
from eve_relation_rag.operations.readiness import READINESS_CHECK_NAMES, ReadinessService
from tests.support.m4 import make_provider_identity

RELEASE_KEY = "release:endoviho-rag:v0:20260829:001"
CORPUS_KEY = "corpus:endoviho-rag:v0:20260829:001"
RELEASE_SHA = "a" * 64
CORPUS_SHA = "b" * 64


class _Gate:
    def __init__(self, capability: object) -> None:
        self.capability = capability

    def authorize(self, _key: str) -> object:
        return self.capability


class _BindingRegistry:
    def __init__(self, **updates: str) -> None:
        values = {
            "release_key": RELEASE_KEY,
            "release_manifest_sha256": RELEASE_SHA,
            "corpus_release_key": CORPUS_KEY,
            "corpus_manifest_sha256": CORPUS_SHA,
        }
        values.update(updates)
        self.binding = SimpleNamespace(**values)

    def authorize(self, _release: str, _corpus: str) -> object:
        return self.binding


class _Provider:
    identity = make_provider_identity()

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def check_ready(self) -> bool:
        return self.ready


def _role_audit(**updates: bool) -> DatabaseRoleAudit:
    values = {
        "role_is_superuser": False,
        "role_can_create_roles": False,
        "role_can_create_databases": False,
        "role_can_replicate": False,
        "role_can_bypass_rls": False,
        "role_has_memberships": False,
        "role_default_transaction_read_only": True,
        "owns_current_database": False,
        "owns_application_schema": False,
        "owns_application_tables": False,
        "owns_application_sequences": False,
        "can_create_in_database": False,
        "can_create_in_schema": False,
        "can_select_all_application_tables": True,
        "can_write_application_tables": False,
        "can_write_application_columns": False,
        "has_application_sequence_privileges": False,
        "can_insert_dataset_receipt": False,
        "can_update_release_status": False,
    }
    values.update(updates)
    return _build_database_role_audit(values)


def _service(
    *,
    release_capability: object | None = None,
    corpus_capability: object | None = None,
    binding_registry: object | None = None,
    provider: object | None = None,
    environment: Literal["development", "test", "production"] = "test",
    engine_factory: Callable[[], Engine] | None = None,
    database_role_auditor: Callable[[Engine], DatabaseRoleAudit] | None = None,
) -> ReadinessService:
    release = release_capability or SimpleNamespace(
        release_key=RELEASE_KEY,
        manifest_sha256=RELEASE_SHA,
    )
    corpus = corpus_capability or SimpleNamespace(
        corpus_release_key=CORPUS_KEY,
        manifest_sha256=CORPUS_SHA,
    )
    registry = binding_registry or _BindingRegistry()
    local_provider = provider or _Provider()
    role_auditor = database_role_auditor or (lambda _engine: _role_audit())
    return ReadinessService(
        service="EVE Relation RAG",
        version="V0",
        engine_factory=engine_factory or (lambda: (_ for _ in ()).throw(AssertionError())),
        migration_config_path=Path("unused.ini"),
        release_key=RELEASE_KEY,
        corpus_release_key=CORPUS_KEY,
        release_gate_factory=lambda: _Gate(release),
        corpus_gate_factory=lambda: _Gate(corpus),
        binding_registry_factory=lambda: registry,  # type: ignore[arg-type,return-value]
        provider_factory=lambda: local_provider,  # type: ignore[arg-type,return-value]
        environment=environment,
        database_role_auditor=role_auditor,
    )


def _force_database_ready(
    monkeypatch: pytest.MonkeyPatch,
    service: ReadinessService,
) -> None:
    monkeypatch.setattr(service, "_database_and_migrations_ready", lambda: True)


def test_all_exact_dependencies_produce_one_sanitized_ready_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    _force_database_ready(monkeypatch, service)

    report = service.check()

    assert report.status == "ready"
    assert tuple(check.check for check in report.checks) == READINESS_CHECK_NAMES
    assert {check.status for check in report.checks} == {"ready"}
    assert "sha256" not in report.model_dump_json()


@pytest.mark.parametrize(
    ("overrides", "failed_check"),
    (
        (
            {
                "release_capability": SimpleNamespace(
                    release_key="release:endoviho-rag:v0:20260829:002",
                    manifest_sha256=RELEASE_SHA,
                )
            },
            "structured_release",
        ),
        (
            {
                "corpus_capability": SimpleNamespace(
                    corpus_release_key=CORPUS_KEY,
                    manifest_sha256="not-a-sha",
                )
            },
            "corpus_release",
        ),
        (
            {"binding_registry": _BindingRegistry(release_manifest_sha256="c" * 64)},
            "hybrid_binding",
        ),
        ({"provider": _Provider(ready=False)}, "local_provider"),
    ),
)
def test_identity_drift_or_provider_failure_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    failed_check: str,
) -> None:
    service = _service(**overrides)
    _force_database_ready(monkeypatch, service)

    report = service.check()

    assert report.status == "not_ready"
    observed = {check.check: check.status for check in report.checks}
    assert observed[failed_check] == "not_ready"


def test_unconfigured_service_never_touches_database_or_gates() -> None:
    service = ReadinessService(
        service="EVE Relation RAG",
        version="V0",
        engine_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        migration_config_path=Path("unused.ini"),
        release_key=None,
        corpus_release_key=None,
        release_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        corpus_gate_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        binding_registry_factory=lambda: (_ for _ in ()).throw(AssertionError()),
        provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("secret detail")),
        environment="test",
    )

    report = service.check()

    assert report.status == "not_ready"
    assert all(check.status == "not_ready" for check in report.checks)
    assert "secret detail" not in report.model_dump_json()


def test_production_rechecks_role_live_and_rejects_admin_or_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(Engine, object())
    audits = [_role_audit(), _role_audit(role_is_superuser=True, owns_current_database=True)]
    observed_engines: list[Engine] = []

    def audit_role(observed: Engine) -> DatabaseRoleAudit:
        observed_engines.append(observed)
        return audits.pop(0)

    service = _service(
        environment="production",
        engine_factory=lambda: engine,
        database_role_auditor=audit_role,
    )
    monkeypatch.setattr(service, "_migrations_ready", lambda observed: observed is engine)

    first = service.check()
    second = service.check()

    assert first.status == "ready"
    assert second.status == "not_ready"
    assert {check.check: check.status for check in second.checks}["database_migrations"] == (
        "not_ready"
    )
    assert observed_engines == [engine, engine]


@pytest.mark.parametrize("environment", ("development", "test"))
def test_non_production_readiness_keeps_migration_only_behavior(
    monkeypatch: pytest.MonkeyPatch,
    environment: Literal["development", "test"],
) -> None:
    engine = cast(Engine, object())
    service = _service(
        environment=environment,
        engine_factory=lambda: engine,
        database_role_auditor=lambda _engine: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(service, "_migrations_ready", lambda observed: observed is engine)

    assert service.check().status == "ready"
