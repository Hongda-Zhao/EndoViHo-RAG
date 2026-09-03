"""Checksum-bound approved-only question and oracle annotation I/O."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationQuestion,
    HybridGold,
    LiteratureGold,
    OracleEvidenceEntry,
    OracleEvidenceManifest,
    QuestionFamily,
    QuestionManifest,
    StructuredGold,
    UnsupportedGold,
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

    manifest = _revalidate_question_manifest(manifest)
    return _approved_questions_from_validated_manifest(manifest)


def _approved_questions_from_validated_manifest(
    manifest: QuestionManifest,
) -> tuple[EvaluationQuestion, ...]:
    """Select approved rows only after the enclosing manifest has been revalidated."""

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

    manifest = _revalidate_question_manifest(manifest)
    approved = _approved_questions_from_validated_manifest(manifest)
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
    _validate_structured_gold_release_bindings(manifest, approved)
    return approved


def validate_oracle_coverage(
    questions: QuestionManifest,
    oracle: OracleEvidenceManifest,
) -> None:
    """Require separately approved oracle evidence for every approved question exactly once."""

    questions = _revalidate_question_manifest(questions)
    oracle = _revalidate_oracle_manifest(oracle)
    approved_questions = _approved_questions_from_validated_manifest(questions)
    _validate_structured_gold_release_bindings(questions, approved_questions)
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
        _validate_oracle_entry_against_gold(question, entry)


def _validate_structured_gold_release_bindings(
    manifest: QuestionManifest,
    approved_questions: tuple[EvaluationQuestion, ...],
) -> None:
    """Bind every structured projection to the manifest's exact DatasetRelease."""

    for question in approved_questions:
        gold = question.gold
        structured_gold: StructuredGold | None
        if isinstance(gold, StructuredGold):
            structured_gold = gold
        elif isinstance(gold, HybridGold):
            structured_gold = gold.structured
        else:
            structured_gold = None
        if structured_gold is not None and (
            structured_gold.release_key != manifest.dataset_release_key
            or structured_gold.release_manifest_sha256
            != manifest.dataset_manifest_sha256
        ):
            raise AnnotationError(
                "structured Gold release identity does not match question manifest"
            )


def _validate_oracle_entry_against_gold(
    question: EvaluationQuestion,
    entry: OracleEvidenceEntry,
) -> None:
    """Require S6 evidence to be an exact, family-specific projection of human Gold."""

    # Coverage has already established that this is an approved entry paired to an
    # approved question.  Keep this check here, rather than weakening the authoring
    # models, so pending worksheets remain loadable and cannot become trusted by shape.
    gold = question.gold
    if gold is None:
        raise AnnotationError("approved question is missing Gold")

    if isinstance(gold, UnsupportedGold):
        if (
            entry.evidence_disposition != "no_supporting_evidence"
            or entry.structured_facts is not None
            or entry.literature_chunk_keys
        ):
            raise AnnotationError(
                "unsupported question requires approved no-supporting-evidence oracle"
            )
        return

    if entry.evidence_disposition != "evidence_supplied":
        raise AnnotationError("answerable question requires supplied oracle evidence")

    if isinstance(gold, StructuredGold):
        if entry.structured_facts != gold:
            raise AnnotationError("oracle structured facts do not exactly match question Gold")
        if entry.literature_chunk_keys:
            raise AnnotationError("structured oracle cannot carry literature chunks")
        return

    if isinstance(gold, LiteratureGold):
        if entry.structured_facts is not None:
            raise AnnotationError("literature oracle cannot carry structured facts")
        _validate_oracle_literature_chunks(entry.literature_chunk_keys, gold)
        return

    if isinstance(gold, HybridGold):
        if entry.structured_facts != gold.structured:
            raise AnnotationError("oracle structured facts do not exactly match question Gold")
        _validate_oracle_literature_chunks(
            entry.literature_chunk_keys,
            gold.literature,
        )
        return

    raise AnnotationError("approved question has an unsupported Gold contract")


def _validate_oracle_literature_chunks(
    chunk_keys: tuple[str, ...],
    gold: LiteratureGold,
) -> None:
    """Accept only manually approved group members and cover every evidence need."""

    supplied = set(chunk_keys)
    excluded = supplied & set(gold.excluded_chunk_keys)
    if excluded:
        raise AnnotationError("oracle includes an excluded or misleading Gold chunk")

    allowed = {
        chunk_key
        for group in gold.evidence_groups
        for chunk_key in group.member_chunk_keys
    }
    if supplied - allowed:
        raise AnnotationError("oracle includes a chunk not manually approved in question Gold")
    if any(not supplied & set(group.member_chunk_keys) for group in gold.evidence_groups):
        raise AnnotationError("oracle does not cover every required Gold evidence group")


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


def _revalidate_question_manifest(value: QuestionManifest) -> QuestionManifest:
    """Reject copied, subclassed, or checksum-stale in-memory question manifests."""

    if type(value) is not QuestionManifest:
        raise AnnotationError("trusted admission requires an exact QuestionManifest")
    try:
        return QuestionManifest.model_validate_json(canonical_json_bytes(value))
    except Exception as exc:
        raise AnnotationError("question manifest failed checksum revalidation") from exc


def _revalidate_oracle_manifest(value: OracleEvidenceManifest) -> OracleEvidenceManifest:
    """Reject copied, subclassed, or checksum-stale in-memory Oracle manifests."""

    if type(value) is not OracleEvidenceManifest:
        raise AnnotationError("oracle coverage requires an exact OracleEvidenceManifest")
    try:
        return OracleEvidenceManifest.model_validate_json(canonical_json_bytes(value))
    except Exception as exc:
        raise AnnotationError("oracle manifest failed checksum revalidation") from exc
