"""Checksum-bound approved-only question and oracle annotation I/O."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationQuestion,
    OracleEvidenceManifest,
    QuestionFamily,
    QuestionManifest,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AnnotationError(RuntimeError):
    """Raised when annotation bytes, approval, or cross-manifest identity is unsafe."""


def load_question_manifest(path: Path, approved_file_sha256: str) -> QuestionManifest:
    """Load only exact canonical bytes approved outside the benchmark process."""

    raw = _read_approved_file(path, approved_file_sha256, "question manifest")
    try:
        manifest = QuestionManifest.model_validate_json(raw)
    except Exception as exc:
        raise AnnotationError("question manifest contract is invalid") from exc
    _require_canonical_json(raw, manifest, "question manifest")
    return manifest


def load_oracle_manifest(path: Path, approved_file_sha256: str) -> OracleEvidenceManifest:
    """Load only a separately approved, canonical S6 oracle manifest."""

    raw = _read_approved_file(path, approved_file_sha256, "oracle manifest")
    try:
        manifest = OracleEvidenceManifest.model_validate_json(raw)
    except Exception as exc:
        raise AnnotationError("oracle manifest contract is invalid") from exc
    _require_canonical_json(raw, manifest, "oracle manifest")
    return manifest


def require_approved_questions(manifest: QuestionManifest) -> tuple[EvaluationQuestion, ...]:
    """Return only human-approved questions and fail when none are available."""

    approved = manifest.approved_questions
    if not approved:
        raise AnnotationError("trusted benchmark requires at least one approved question")
    if manifest.gold_sha256 is None:
        raise AnnotationError("approved questions require a gold checksum")
    return approved


def require_trusted_question_set(
    manifest: QuestionManifest,
) -> tuple[EvaluationQuestion, ...]:
    """Apply the preregistered 15-20-per-family admission gate for a trusted run."""

    approved = require_approved_questions(manifest)
    if not 60 <= len(approved) <= 80:
        raise AnnotationError("trusted benchmark requires 60-80 approved questions")
    families: tuple[QuestionFamily, ...] = (
        "structured",
        "literature",
        "hybrid",
        "unsupported",
    )
    if any(
        not 15 <= manifest.approved_family_counts[family] <= 20
        for family in families
    ):
        raise AnnotationError("trusted benchmark requires 15-20 approved questions per family")
    if (
        manifest.dataset_release_key is None
        or manifest.corpus_release_key is None
    ):
        raise AnnotationError("trusted question set requires dataset and corpus identities")
    return approved


def validate_oracle_coverage(
    questions: QuestionManifest,
    oracle: OracleEvidenceManifest,
) -> None:
    """Require separately approved oracle evidence for every approved question exactly once."""

    approved_questions = require_approved_questions(questions)
    approved_entries = tuple(
        entry for entry in oracle.entries if entry.review_status == "approved"
    )
    if tuple(entry.question_id for entry in approved_entries) != tuple(
        question.question_id for question in approved_questions
    ):
        raise AnnotationError("approved oracle entries do not exactly cover approved questions")
    question_by_id = {question.question_id: question for question in approved_questions}
    for entry in approved_entries:
        question = question_by_id[entry.question_id]
        if entry.question_text_sha256 != question.question_text_sha256:
            raise AnnotationError("oracle entry question checksum does not match")
        if (
            entry.dataset_release_key != questions.dataset_release_key
            or entry.dataset_manifest_sha256 != questions.dataset_manifest_sha256
            or entry.corpus_release_key != questions.corpus_release_key
            or entry.corpus_manifest_sha256 != questions.corpus_manifest_sha256
        ):
            raise AnnotationError("oracle entry release/corpus identity does not match questions")


def write_new_canonical_json(path: Path, value: object) -> str:
    """Create canonical JSON once; never overwrite annotations or output via this helper."""

    if path.exists() or path.is_symlink():
        raise AnnotationError("annotation output already exists")
    try:
        path.parent.resolve(strict=True)
    except OSError as exc:
        raise AnnotationError("annotation output parent does not exist") from exc
    raw = canonical_json_bytes(value) + b"\n"
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except OSError as exc:
        raise AnnotationError("annotation output could not be created") from exc
    return hashlib.sha256(raw).hexdigest()


def _read_approved_file(path: Path, approved_sha256: str, description: str) -> bytes:
    if _SHA256_RE.fullmatch(approved_sha256) is None:
        raise AnnotationError(f"approved {description} checksum is invalid")
    if path.is_symlink():
        raise AnnotationError(f"{description} must not be a symbolic link")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AnnotationError(f"{description} cannot be read") from exc
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise AnnotationError(f"{description} checksum is not approved")
    return raw


def _require_canonical_json(raw: bytes, value: object, description: str) -> None:
    if raw != canonical_json_bytes(value) + b"\n":
        raise AnnotationError(f"{description} is not canonical JSON")
