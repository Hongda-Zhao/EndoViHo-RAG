"""Fail-closed V0 readiness over exact runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import Field
from sqlalchemy import Engine, text

from eve_relation_rag.hybrid.bindings import HybridBindingRegistry
from eve_relation_rag.hybrid.contracts import ProviderIdentity, StrictFrozenSchema
from eve_relation_rag.operations.database_role import (
    DatabaseRoleAudit,
    audit_database_runtime_role,
)

_LOWER_HEX = frozenset("0123456789abcdef")


class _ReleaseCapability(Protocol):
    release_key: str
    manifest_sha256: str


class _CorpusCapability(Protocol):
    corpus_release_key: str
    manifest_sha256: str


class _ReleaseGate(Protocol):
    def authorize(self, release_key: str) -> _ReleaseCapability: ...


class _CorpusGate(Protocol):
    def authorize(self, corpus_release_key: str) -> _CorpusCapability: ...


class _ReadyProvider(Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...

    def check_ready(self) -> bool: ...


type ReadinessCheckName = Literal[
    "configuration",
    "database_migrations",
    "structured_release",
    "corpus_release",
    "hybrid_binding",
    "local_provider",
]
READINESS_CHECK_NAMES: tuple[ReadinessCheckName, ...] = (
    "configuration",
    "database_migrations",
    "structured_release",
    "corpus_release",
    "hybrid_binding",
    "local_provider",
)


class ReadinessCheckResult(StrictFrozenSchema):
    """One sanitized dependency outcome."""

    check: ReadinessCheckName
    status: Literal["ready", "not_ready"]


class ReadinessReport(StrictFrozenSchema):
    """Public readiness envelope containing no secrets or provider payloads."""

    readiness_schema_version: Literal["v0-readiness-v1"] = "v0-readiness-v1"
    status: Literal["ready", "not_ready"]
    service: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    checks: tuple[ReadinessCheckResult, ...] = Field(min_length=6, max_length=6)


class ReadinessService:
    """Verify every activation dependency without returning internal failure detail."""

    def __init__(
        self,
        *,
        service: str,
        version: str,
        engine_factory: Callable[[], Engine],
        migration_config_path: Path,
        release_key: str | None,
        corpus_release_key: str | None,
        release_gate_factory: Callable[[], object],
        corpus_gate_factory: Callable[[], object],
        binding_registry_factory: Callable[[], HybridBindingRegistry],
        provider_factory: Callable[[], _ReadyProvider],
        environment: Literal["development", "test", "production"],
        database_role_auditor: Callable[[Engine], DatabaseRoleAudit] = (
            audit_database_runtime_role
        ),
    ) -> None:
        self._service = service
        self._version = version
        self._engine_factory = engine_factory
        self._migration_config_path = migration_config_path
        self._release_key = release_key
        self._corpus_release_key = corpus_release_key
        self._release_gate_factory = release_gate_factory
        self._corpus_gate_factory = corpus_gate_factory
        self._binding_registry_factory = binding_registry_factory
        self._provider_factory = provider_factory
        self._environment = environment
        self._database_role_auditor = database_role_auditor

    def check(self) -> ReadinessReport:
        """Return all six exact checks; every unexpected exception becomes not-ready."""

        configured = bool(self._release_key and self._corpus_release_key)
        database_ready = self._database_and_migrations_ready() if configured else False
        release_capability: _ReleaseCapability | None = None
        corpus_capability: _CorpusCapability | None = None
        if database_ready and self._release_key is not None:
            try:
                release_gate = cast(_ReleaseGate, self._release_gate_factory())
                release_capability = release_gate.authorize(self._release_key)
                if release_capability.release_key != self._release_key or not _is_sha256(
                    release_capability.manifest_sha256
                ):
                    release_capability = None
            except Exception:
                release_capability = None
        if database_ready and self._corpus_release_key is not None:
            try:
                corpus_gate = cast(_CorpusGate, self._corpus_gate_factory())
                corpus_capability = corpus_gate.authorize(self._corpus_release_key)
                if (
                    corpus_capability.corpus_release_key != self._corpus_release_key
                    or not _is_sha256(corpus_capability.manifest_sha256)
                ):
                    corpus_capability = None
            except Exception:
                corpus_capability = None

        binding_ready = False
        if (
            release_capability is not None
            and corpus_capability is not None
            and self._release_key is not None
            and self._corpus_release_key is not None
        ):
            try:
                binding = self._binding_registry_factory().authorize(
                    self._release_key,
                    self._corpus_release_key,
                )
                binding_ready = (
                    binding.release_key == self._release_key
                    and binding.corpus_release_key == self._corpus_release_key
                    and binding.release_key == release_capability.release_key
                    and binding.release_manifest_sha256 == release_capability.manifest_sha256
                    and binding.corpus_release_key == corpus_capability.corpus_release_key
                    and binding.corpus_manifest_sha256 == corpus_capability.manifest_sha256
                )
            except Exception:
                binding_ready = False

        provider_ready = False
        try:
            provider = self._provider_factory()
            ProviderIdentity.model_validate_json(provider.identity.model_dump_json())
            provider_ready = provider.check_ready()
        except Exception:
            provider_ready = False

        outcomes: tuple[tuple[ReadinessCheckName, bool], ...] = (
            (READINESS_CHECK_NAMES[0], configured),
            (READINESS_CHECK_NAMES[1], database_ready),
            (READINESS_CHECK_NAMES[2], release_capability is not None),
            (READINESS_CHECK_NAMES[3], corpus_capability is not None),
            (READINESS_CHECK_NAMES[4], binding_ready),
            (READINESS_CHECK_NAMES[5], provider_ready),
        )
        checks = tuple(
            ReadinessCheckResult(
                check=check,
                status="ready" if passed else "not_ready",
            )
            for check, passed in outcomes
        )
        return ReadinessReport(
            status=("ready" if all(passed for _check, passed in outcomes) else "not_ready"),
            service=self._service,
            version=self._version,
            checks=checks,
        )

    def _database_and_migrations_ready(self) -> bool:
        try:
            engine = self._engine_factory()
            if not self._migrations_ready(engine):
                return False
            if self._environment != "production":
                return True
            audit = self._database_role_auditor(engine)
            return audit.runtime_readonly is True
        except Exception:
            return False

    def _migrations_ready(self, engine: Engine) -> bool:
        path = self._migration_config_path
        if path.is_symlink() or not path.is_file():
            return False
        script = ScriptDirectory.from_config(Config(str(path)))
        heads = script.get_heads()
        if len(heads) != 1:
            return False
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            observed = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        return isinstance(observed, str) and observed == heads[0]


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - _LOWER_HEX)


__all__ = [
    "READINESS_CHECK_NAMES",
    "ReadinessCheckName",
    "ReadinessCheckResult",
    "ReadinessReport",
    "ReadinessService",
]
