"""Checksum-bound annotation loading and legacy-to-pending migration."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AnnotationManifest,
    AnnotationQuestion,
    EvidenceGroup,
    build_annotation_manifest,
)
from eve_relation_rag.literature.benchmarking import BenchmarkDefinition
from eve_relation_rag.literature.hashing import canonical_json_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AnnotationIOError(RuntimeError):
    """Raised when annotation input/output identity is unsafe or unapproved."""


def load_annotation_manifest(
    path: Path,
    approved_sha256: str,
) -> AnnotationManifest:
    """Load annotations only when the exact file checksum is approved."""

    raw = _read_approved_file(path, approved_sha256, description="annotation manifest")
    try:
        manifest = AnnotationManifest.model_validate_json(raw)
    except Exception as exc:
        raise AnnotationIOError("annotation manifest contract is invalid") from exc
    if raw != canonical_json_bytes(manifest) + b"\n":
        raise AnnotationIOError("annotation manifest is not canonical JSON")
    return manifest


def load_legacy_benchmark(
    path: Path,
    approved_sha256: str,
) -> BenchmarkDefinition:
    """Load the existing benchmark without changing or approving its gold labels."""

    raw = _read_approved_file(path, approved_sha256, description="legacy benchmark")
    try:
        return BenchmarkDefinition.model_validate_json(raw)
    except Exception as exc:
        raise AnnotationIOError("legacy benchmark contract is invalid") from exc


def migrate_legacy_benchmark_to_pending(
    legacy: BenchmarkDefinition,
) -> AnnotationManifest:
    """Preserve existing questions/gold exactly, but require new expert review before use."""

    questions = tuple(
        AnnotationQuestion(
            question_id=question.question_key,
            question=question.question,
            category=None,
            anchors=question.anchors,
            required_chunk_keys=question.relevant_chunk_keys,
            acceptable_alternative_chunk_keys=(),
            excluded_chunk_keys=(),
            evidence_groups=tuple(
                EvidenceGroup(
                    group_id=f"legacy-required-{index:03d}",
                    required_chunk_key=chunk_key,
                )
                for index, chunk_key in enumerate(question.relevant_chunk_keys, start=1)
            ),
            review_status="pending",
            reviewer_id=None,
            reviewed_at=None,
            annotation_notes="Migrated from literature-benchmark-v1; expert review required.",
        )
        for question in legacy.questions
    )
    return build_annotation_manifest(
        corpus_release_key=legacy.corpus_release_key,
        corpus_manifest_sha256=legacy.corpus_manifest_sha256,
        questions=questions,
    )


def write_new_annotation_manifest(path: Path, manifest: AnnotationManifest) -> str:
    """Create a canonical annotation file once and return its exact file SHA-256."""

    if path.exists() or path.is_symlink():
        raise AnnotationIOError("annotation output already exists")
    try:
        path.parent.resolve(strict=True)
    except OSError as exc:
        raise AnnotationIOError("annotation output parent does not exist") from exc
    value = canonical_json_bytes(manifest) + b"\n"
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except OSError as exc:
        raise AnnotationIOError("annotation output could not be created") from exc
    return hashlib.sha256(value).hexdigest()


def _read_approved_file(path: Path, approved_sha256: str, *, description: str) -> bytes:
    if _SHA256_RE.fullmatch(approved_sha256) is None:
        raise AnnotationIOError(f"approved {description} checksum is invalid")
    if path.is_symlink():
        raise AnnotationIOError(f"{description} must not be a symbolic link")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AnnotationIOError(f"{description} cannot be read") from exc
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise AnnotationIOError(f"{description} checksum is not approved")
    return raw
