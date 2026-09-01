"""Read-only fingerprints proving production defaults did not change during a run."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from eve_relation_rag.literature.contracts import Sha256, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_sha256

DEFAULT_PRODUCTION_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "src/eve_relation_rag/bootstrap.py",
    "src/eve_relation_rag/cli.py",
    "src/eve_relation_rag/config/settings.py",
    "src/eve_relation_rag/application/literature.py",
    "src/eve_relation_rag/db/models.py",
    "src/eve_relation_rag/literature/contracts.py",
    "src/eve_relation_rag/literature/embeddings.py",
    "src/eve_relation_rag/literature/gate.py",
    "src/eve_relation_rag/literature/local_bge.py",
    "src/eve_relation_rag/literature/providers.py",
    "src/eve_relation_rag/retrieval/literature/fusion.py",
    "src/eve_relation_rag/retrieval/literature/repository.py",
)


class ProductionSourceGuardError(RuntimeError):
    """Raised when protected production sources are absent or changed."""


class ProductionSourceFingerprint(StrictFrozenSchema):
    """Stable per-file hashes plus one aggregate checksum."""

    source_guard_schema_version: str = Field(pattern=r"^production-source-guard-v1$")
    file_sha256: dict[str, Sha256]
    fingerprint_sha256: Sha256

    @model_validator(mode="after")
    def validate_fingerprint(self) -> Self:
        if not self.file_sha256:
            raise ValueError("production source fingerprint must contain files")
        if tuple(self.file_sha256) != tuple(sorted(self.file_sha256)):
            raise ValueError("production source paths must be sorted")
        if self.fingerprint_sha256 != canonical_json_sha256(self.file_sha256):
            raise ValueError("production source fingerprint does not match files")
        return self


def capture_production_source_fingerprint(
    repository_root: Path,
    *,
    relative_paths: tuple[str, ...] = DEFAULT_PRODUCTION_PATHS,
) -> ProductionSourceFingerprint:
    """Hash protected files and all migration revisions without modifying the index/tree."""

    if repository_root.is_symlink():
        raise ProductionSourceGuardError("repository root must not be a symbolic link")
    try:
        root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise ProductionSourceGuardError("repository root does not exist") from exc
    paths = set(relative_paths)
    production_package = root / "src" / "eve_relation_rag"
    if production_package.is_dir():
        paths.update(
            path.relative_to(root).as_posix()
            for path in production_package.rglob("*.py")
            if "experiments" not in path.relative_to(production_package).parts
        )
    app_package = root / "app"
    if app_package.is_dir():
        paths.update(path.relative_to(root).as_posix() for path in app_package.rglob("*.py"))
    migrations = root / "migrations" / "versions"
    if migrations.exists():
        paths.update(
            path.relative_to(root).as_posix() for path in migrations.glob("*.py")
        )
    hashes: dict[str, str] = {}
    for relative_path in sorted(paths):
        candidate = root.joinpath(*relative_path.split("/"))
        if candidate.is_symlink():
            raise ProductionSourceGuardError("protected production file is a symbolic link")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProductionSourceGuardError(
                f"protected production file is missing: {relative_path}"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ProductionSourceGuardError("protected production path escapes the repository")
        hashes[relative_path] = _file_sha256(resolved)
    return ProductionSourceFingerprint(
        source_guard_schema_version="production-source-guard-v1",
        file_sha256=hashes,
        fingerprint_sha256=canonical_json_sha256(hashes),
    )


def assert_production_sources_unchanged(
    before: ProductionSourceFingerprint,
    after: ProductionSourceFingerprint,
) -> None:
    """Fail if an experiment changed any protected production source/default."""

    if before != after:
        raise ProductionSourceGuardError("production defaults changed during the ablation")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
