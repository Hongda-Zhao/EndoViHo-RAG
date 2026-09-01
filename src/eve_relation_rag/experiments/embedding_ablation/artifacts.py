"""Checksum verification for local-only embedding and reranker artifacts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from eve_relation_rag.experiments.embedding_ablation.contracts import ModelArtifactManifest
from eve_relation_rag.literature.hashing import canonical_json_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_SIZE_LIMIT = 16 * 1024 * 1024
_VERIFIER_ISSUER = object()


class ArtifactVerificationError(RuntimeError):
    """Raised before model loading when local artifact provenance is not exact."""


@dataclass(frozen=True, slots=True)
class VerifiedModelArtifact:
    """Identity issued only after an exact local directory and manifest verification."""

    model_directory: Path
    manifest_path: Path
    manifest: ModelArtifactManifest
    artifact_manifest_sha256: str
    model_size_bytes: int
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _VERIFIER_ISSUER:
            raise TypeError("VerifiedModelArtifact may only be issued by the artifact verifier")


def verify_model_artifact(
    model_directory: Path,
    manifest_path: Path,
    approved_manifest_sha256: str,
    *,
    expected_model_id: str | None = None,
    expected_revision: str | None = None,
    expected_task_kind: Literal["embedding", "reranker"] | None = None,
    expected_dimension: int | None = None,
) -> VerifiedModelArtifact:
    """Verify one local model directory without repository resolution or model imports."""

    if _SHA256_RE.fullmatch(approved_manifest_sha256) is None:
        raise ArtifactVerificationError("approved artifact manifest checksum is invalid")
    root = _resolve_regular_directory(model_directory)
    resolved_manifest = _resolve_regular_file(manifest_path, description="artifact manifest")
    try:
        if resolved_manifest.stat().st_size > _MANIFEST_SIZE_LIMIT:
            raise ArtifactVerificationError("artifact manifest is unexpectedly large")
        manifest_bytes = resolved_manifest.read_bytes()
    except OSError as exc:
        raise ArtifactVerificationError("artifact manifest cannot be read") from exc
    observed_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if observed_manifest_sha256 != approved_manifest_sha256:
        raise ArtifactVerificationError("artifact manifest checksum is not approved")
    try:
        manifest = ModelArtifactManifest.model_validate_json(manifest_bytes)
    except ValidationError as exc:
        raise ArtifactVerificationError("artifact manifest contract is invalid") from exc
    if manifest_bytes != canonical_json_bytes(manifest):
        raise ArtifactVerificationError("artifact manifest is not canonical JSON")

    _validate_expected_identity(
        manifest,
        expected_model_id=expected_model_id,
        expected_revision=expected_revision,
        expected_task_kind=expected_task_kind,
        expected_dimension=expected_dimension,
    )
    if manifest.license_review_status != "approved":
        raise ArtifactVerificationError("model artifact license has not been approved")

    listed_paths = {item.relative_path for item in manifest.files}
    for item in manifest.files:
        candidate = root.joinpath(*item.relative_path.split("/"))
        if candidate.is_symlink():
            raise ArtifactVerificationError("model artifact must not be a symbolic link")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ArtifactVerificationError("listed model artifact is missing") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ArtifactVerificationError("model artifact escapes the verified directory")
        try:
            observed_size = resolved.stat().st_size
            observed_sha256 = _file_sha256(resolved)
        except OSError as exc:
            raise ArtifactVerificationError("listed model artifact cannot be read") from exc
        if observed_size != item.byte_size or observed_sha256 != item.sha256:
            raise ArtifactVerificationError("model artifact checksum or size mismatch")

    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactVerificationError("model artifact directory contains a symbolic link")
        if path.is_file() and path.resolve() != resolved_manifest:
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != listed_paths:
        raise ArtifactVerificationError("artifact manifest does not enumerate every model file")

    return VerifiedModelArtifact(
        model_directory=root,
        manifest_path=resolved_manifest,
        manifest=manifest,
        artifact_manifest_sha256=observed_manifest_sha256,
        model_size_bytes=sum(item.byte_size for item in manifest.files),
        _issuer=_VERIFIER_ISSUER,
    )


def is_verified_artifact(value: object) -> bool:
    """Return whether a value was issued by this verifier, not merely shape-compatible."""

    return isinstance(value, VerifiedModelArtifact) and value._issuer is _VERIFIER_ISSUER


def _validate_expected_identity(
    manifest: ModelArtifactManifest,
    *,
    expected_model_id: str | None,
    expected_revision: str | None,
    expected_task_kind: Literal["embedding", "reranker"] | None,
    expected_dimension: int | None,
) -> None:
    if expected_model_id is not None and manifest.model_id != expected_model_id:
        raise ArtifactVerificationError("model ID does not match the approved identity")
    if expected_revision is not None and manifest.exact_revision != expected_revision:
        raise ArtifactVerificationError("model revision does not match the approved identity")
    if expected_task_kind is not None and manifest.representation.task_kind != expected_task_kind:
        raise ArtifactVerificationError("model task kind does not match the approved identity")
    if expected_dimension is not None and manifest.representation.dimension != expected_dimension:
        raise ArtifactVerificationError("model dimension does not match the approved identity")


def _resolve_regular_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ArtifactVerificationError("model directory must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactVerificationError("verified local model directory does not exist") from exc
    if not resolved.is_dir():
        raise ArtifactVerificationError("model artifact path must be a directory")
    return resolved


def _resolve_regular_file(path: Path, *, description: str) -> Path:
    if path.is_symlink():
        raise ArtifactVerificationError(f"{description} must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactVerificationError(f"{description} does not exist") from exc
    if not resolved.is_file():
        raise ArtifactVerificationError(f"{description} must be a regular file")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
