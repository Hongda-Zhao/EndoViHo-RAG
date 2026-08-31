"""Strict, versioned evidence graph for the V0 publication preflight.

The aggregate benchmark and checklist are projections, not trust roots.  This module
loads every activation artifact through its typed contract, verifies its raw file
identity, and then cross-binds the receipt, benchmark, review, and clean-rebuild graph.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, Field, ValidationError, model_validator

from eve_relation_rag.activation.contracts import StructuredActivationManifest
from eve_relation_rag.domain.keys import is_release_key
from eve_relation_rag.generation.human_review import (
    HumanBenchmarkDefinition,
    HumanReviewEvaluation,
    HumanReviewPacket,
    HumanReviewSubmission,
    evaluate_human_review,
)
from eve_relation_rag.generation.policy import (
    LocalModelPolicyManifest,
    PromptPolicyManifest,
)
from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBindingManifest,
    StrictFrozenSchema,
    StructuredRouteAnswer,
    canonical_model_sha256,
    canonical_self_sha256,
)
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    CorpusReleaseKey,
    Rfc3339Utc,
    Sha256,
    StableToken,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.receipt_integrity import (
    TrustedReceiptEvidence,
)
from eve_relation_rag.literature.receipt_integrity import (
    receipt_identity as corpus_receipt_identity,
)
from eve_relation_rag.releases.receipt_integrity import (
    TrustedDatasetReceiptEvidence,
    release_validator_code_sha256,
    structured_activation_policy_code_sha256,
    structured_candidate_capability_sha256,
)
from eve_relation_rag.releases.receipt_integrity import (
    receipt_identity as dataset_receipt_identity,
)

ACTIVATION_STATE_PATH = Path("release/v0_activation_state.json")
ACTIVATION_STATE_SCHEMA_VERSION = "v0-activation-state-v2"
MAX_ACTIVATION_ARTIFACT_BYTES = 20 * 1024 * 1024
_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_PERFECT_METRIC = "1.000000000000"
V0_CORPUS_IMPORTER_CODE_SHA256 = "a19682aeb94e490a3e42caec6e0edb07dafb9c69044b300db245a7c3203bdce4"
V0_CORPUS_POLICY_CODE_SHA256 = "c5dcd6a5e8ed524cf8ee9e38cf479449840ac0e39b7c2c602ef00bda374f5771"

_RUNTIME_IDENTITY_PATHS = (
    "uv.lock",
    "src/eve_relation_rag/activation/contracts.py",
    "src/eve_relation_rag/activation/policy.py",
    "src/eve_relation_rag/activation/release_state.py",
    "src/eve_relation_rag/domain/keys.py",
    "src/eve_relation_rag/importers/audit.py",
    "src/eve_relation_rag/importers/data_s1.py",
    "src/eve_relation_rag/releases/dependencies.py",
    "src/eve_relation_rag/releases/receipt_integrity.py",
    "src/eve_relation_rag/releases/validator.py",
)


class ActivationStateError(RuntimeError):
    """The local activation evidence graph is absent, unsafe, or inconsistent."""


def _validate_artifact_path(value: str) -> str:
    pure = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or pure.is_absolute()
        or value != pure.as_posix()
        or pure.as_posix() in {".", ".."}
        or ".." in pure.parts
        or any(not part or part == "." for part in pure.parts)
    ):
        raise ValueError("artifact path must be one canonical safe repository-relative path")
    return value


ArtifactPath = Annotated[
    str,
    Field(min_length=1, max_length=1024),
    AfterValidator(_validate_artifact_path),
]


def _validate_utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("publication timestamp must be timezone-aware UTC")
    return value


PublicationTimestamp = Annotated[
    datetime,
    AfterValidator(_validate_utc_timestamp),
]


class EvidenceArtifactRef(StrictFrozenSchema):
    """Raw identity of one repository-contained activation artifact."""

    path: ArtifactPath
    byte_size: int = Field(gt=0, le=MAX_ACTIVATION_ARTIFACT_BYTES)
    file_sha256: Sha256


class V0RouteBenchmarkCase(StrictFrozenSchema):
    """One exact request/response identity in a ten-case real-route benchmark."""

    case_ordinal: int = Field(ge=1, le=10)
    case_key: StableToken
    question_sha256: Sha256
    response_sha256: Sha256
    structured_response: StructuredRouteAnswer | None = None
    result: Literal["passed"]


class V0RouteBenchmarkReport(StrictFrozenSchema):
    """Typed real structured or hybrid benchmark; aggregate flags cannot replace it."""

    benchmark_report_schema_version: Literal["v0-route-benchmark-report-v1"]
    route: Literal["structured", "hybrid"]
    release_key: str
    release_manifest_sha256: Sha256
    candidate_validation_input_sha256: Sha256
    dataset_validation_request_sha256: Sha256
    dependency_graph_sha256: Sha256
    candidate_capability_sha256: Sha256
    corpus_release_key: CorpusReleaseKey | None = None
    corpus_manifest_sha256: Sha256 | None = None
    corpus_receipt_sha256: Sha256 | None = None
    binding_manifest_sha256: Sha256 | None = None
    human_review_evaluation_sha256: Sha256 | None = None
    cases: tuple[V0RouteBenchmarkCase, ...] = Field(min_length=10, max_length=10)
    report_sha256: Sha256

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("benchmark release_key is invalid")
        ordinals = tuple(case.case_ordinal for case in self.cases)
        if ordinals != tuple(range(1, 11)):
            raise ValueError("real route benchmark must contain ordered cases 1..10")
        keys = tuple(case.case_key for case in self.cases)
        if len(keys) != len(set(keys)):
            raise ValueError("real route benchmark case keys must be unique")
        hybrid_fields = (
            self.corpus_release_key,
            self.corpus_manifest_sha256,
            self.corpus_receipt_sha256,
            self.binding_manifest_sha256,
            self.human_review_evaluation_sha256,
        )
        if self.route == "hybrid" and any(value is None for value in hybrid_fields):
            raise ValueError("hybrid benchmark is missing corpus, binding, or review identity")
        if self.route == "structured" and any(value is not None for value in hybrid_fields):
            raise ValueError("structured benchmark must not claim hybrid-only identities")
        if self.route == "structured" and any(
            case.structured_response is None
            or case.response_sha256 != canonical_model_sha256(case.structured_response)
            for case in self.cases
        ):
            raise ValueError("structured benchmark must embed every exact typed response")
        if self.route == "hybrid" and any(
            case.structured_response is not None for case in self.cases
        ):
            raise ValueError("hybrid benchmark responses live only in the review packet")
        if self.report_sha256 != canonical_self_sha256(self, "report_sha256"):
            raise ValueError("real route benchmark checksum does not match")
        return self


class V0RebuildRouteIdentity(StrictFrozenSchema):
    """One route replay retained by the clean activation rebuild."""

    route: Literal["structured", "literature", "hybrid"]
    evidence_sha256: Sha256
    result: Literal["passed"]


class V0CleanActivationRebuildReport(StrictFrozenSchema):
    """Exact identities replayed from an empty database at the release commit."""

    rebuild_report_schema_version: Literal["v0-clean-activation-rebuild-v1"]
    release_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    release_key: str
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    candidate_validation_input_sha256: Sha256
    dataset_validation_request_sha256: Sha256
    dependency_graph_sha256: Sha256
    candidate_capability_sha256: Sha256
    corpus_receipt_sha256: Sha256
    corpus_rebuild_report_sha256: Sha256
    structured_benchmark_report_sha256: Sha256
    hybrid_benchmark_report_sha256: Sha256
    human_review_evaluation_sha256: Sha256
    dependency_lock_sha256: Sha256
    dataset_validator_code_sha256: Sha256
    activation_policy_code_sha256: Sha256
    activation_state_validator_code_sha256: Sha256
    corpus_validator_code_sha256: Sha256
    corpus_importer_code_sha256: Sha256
    corpus_policy_code_sha256: Sha256
    database_started_empty: Literal[True]
    migration_head: Literal["0012_extended_viral_lineage"]
    route_replays: tuple[
        V0RebuildRouteIdentity,
        V0RebuildRouteIdentity,
        V0RebuildRouteIdentity,
    ]
    status: Literal["passed"]
    rebuild_sha256: Sha256

    @model_validator(mode="after")
    def validate_rebuild(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("clean rebuild release_key is invalid")
        if tuple(item.route for item in self.route_replays) != (
            "structured",
            "literature",
            "hybrid",
        ):
            raise ValueError("clean rebuild route identities must be structured/literature/hybrid")
        if self.rebuild_sha256 != canonical_self_sha256(self, "rebuild_sha256"):
            raise ValueError("clean activation rebuild checksum does not match")
        return self


class CorpusReleaseExport(StrictFrozenSchema):
    corpus_release_key: CorpusReleaseKey
    status: Literal["validated", "published"]
    manifest_sha256: Sha256
    policy_graph_sha256: Sha256
    manifest_document_count: int = Field(gt=0)


class CorpusImportTerminalCounts(StrictFrozenSchema):
    chunk_count: int = Field(gt=0)
    chunk_keys_sha256: Sha256
    document_count: int = Field(gt=0)
    document_keys_sha256: Sha256
    imported_documents: int = Field(ge=0)
    reused_documents: int = Field(ge=0)
    embedding_count: int = Field(gt=0)
    embeddings_sha256: Sha256


class CorpusImportParameters(StrictFrozenSchema):
    """Exact V0 import inputs, including distinct execution and policy provenance."""

    chunking_policy_key: StableToken
    embedding_model_key: StableToken
    fts_policy_key: StableToken
    model_artifact_manifest_sha256: Sha256
    parser_policy_key: StableToken
    retrieval_policy_key: StableToken
    tokenizer_model_key: StableToken
    embedding_build: Literal[True]
    policy_code_sha256: Sha256


class CorpusImportRunExport(StrictFrozenSchema):
    run_key: StableToken
    status: Literal["succeeded"]
    manifest_sha256: Sha256
    importer_version: StableToken
    code_sha256: Sha256
    parameters: CorpusImportParameters
    parameters_sha256: Sha256
    terminal_counts: CorpusImportTerminalCounts

    @model_validator(mode="after")
    def validate_parameters(self) -> Self:
        if self.parameters_sha256 != canonical_json_sha256(self.parameters):
            raise ValueError("corpus import parameters checksum does not match")
        return self


class CorpusReceiptExport(StrictFrozenSchema):
    receipt_key: StableToken
    status: Literal["passed"]
    trusted: Literal[True]
    manifest_sha256: Sha256
    policy_graph_sha256: Sha256
    rebuild_sha256: Sha256
    benchmark_sha256: Sha256
    receipt_sha256: Sha256
    validation_report: TrustedReceiptEvidence


class CorpusValidationExport(StrictFrozenSchema):
    """Typed database export for an immutable trusted corpus receipt."""

    export_schema_version: Literal["v0-corpus-validation-export-v1"]
    exported_at: Rfc3339Utc
    corpus_release: CorpusReleaseExport
    import_run: CorpusImportRunExport
    receipt: CorpusReceiptExport
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_export(self) -> Self:
        release = self.corpus_release
        run = self.import_run
        receipt = self.receipt
        evidence = receipt.validation_report
        expected_key, expected_sha256 = corpus_receipt_identity(evidence)
        rebuild = evidence.rebuild_report
        benchmark = evidence.benchmark_report
        expected_pairs = (
            (run.manifest_sha256, release.manifest_sha256),
            (run.terminal_counts.document_count, release.manifest_document_count),
            (
                run.terminal_counts.imported_documents + run.terminal_counts.reused_documents,
                release.manifest_document_count,
            ),
            (receipt.receipt_key, expected_key),
            (receipt.receipt_sha256, expected_sha256),
            (receipt.manifest_sha256, release.manifest_sha256),
            (receipt.manifest_sha256, rebuild.manifest_sha256),
            (receipt.policy_graph_sha256, release.policy_graph_sha256),
            (receipt.policy_graph_sha256, rebuild.policy_graph_sha256),
            (receipt.rebuild_sha256, rebuild.rebuild_sha256),
            (receipt.benchmark_sha256, benchmark.benchmark_sha256),
            (release.corpus_release_key, rebuild.corpus_release_key),
            (run.code_sha256, V0_CORPUS_IMPORTER_CODE_SHA256),
            (
                run.parameters.policy_code_sha256,
                V0_CORPUS_POLICY_CODE_SHA256,
            ),
            (
                run.parameters.model_artifact_manifest_sha256,
                rebuild.model_artifact_manifest_sha256,
            ),
            (run.parameters.embedding_model_key, rebuild.embedding_model_key),
            (run.parameters.tokenizer_model_key, rebuild.embedding_model_key),
            (run.terminal_counts.document_count, rebuild.document_count),
            (run.terminal_counts.chunk_count, rebuild.chunk_count),
            (run.terminal_counts.embedding_count, rebuild.embedding_count),
        )
        if any(observed != expected for observed, expected in expected_pairs):
            raise ValueError("corpus validation export identities do not form one receipt")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("corpus validation export checksum does not match")
        return self


class DatasetPublicationEvidence(StrictFrozenSchema):
    """Immutable export of the receipt-backed structured publication transition."""

    publication_evidence_schema_version: Literal["v0-dataset-publication-evidence-v1"]
    release_key: str
    manifest_sha256: Sha256
    receipt_key: StableToken
    receipt_sha256: Sha256
    status: Literal["published"]
    published_at: PublicationTimestamp
    replayed: bool
    publication_sha256: Sha256

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("dataset publication release_key is invalid")
        if not self.receipt_key.startswith("dataset-receipt:sha256:"):
            raise ValueError("dataset publication receipt_key is invalid")
        if self.publication_sha256 != canonical_self_sha256(self, "publication_sha256"):
            raise ValueError("dataset publication evidence checksum does not match")
        return self


class CorpusPublicationEvidence(StrictFrozenSchema):
    """Immutable export of the receipt-backed corpus publication transition."""

    publication_evidence_schema_version: Literal["v0-corpus-publication-evidence-v1"]
    corpus_release_key: CorpusReleaseKey
    manifest_sha256: Sha256
    receipt_key: StableToken
    receipt_sha256: Sha256
    status: Literal["published"]
    published_at: PublicationTimestamp
    replayed: bool
    publication_sha256: Sha256

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if not self.receipt_key.startswith("corpus-receipt:sha256:"):
            raise ValueError("corpus publication receipt_key is invalid")
        if self.publication_sha256 != canonical_self_sha256(self, "publication_sha256"):
            raise ValueError("corpus publication evidence checksum does not match")
        return self


class V0ActivationArtifacts(StrictFrozenSchema):
    structured_activation_manifest: EvidenceArtifactRef
    dataset_receipt_evidence: EvidenceArtifactRef
    dataset_publication_evidence: EvidenceArtifactRef
    corpus_manifest: EvidenceArtifactRef
    corpus_anchor_manifest: EvidenceArtifactRef
    corpus_receipt_export: EvidenceArtifactRef
    corpus_publication_evidence: EvidenceArtifactRef
    hybrid_binding_manifest: EvidenceArtifactRef
    model_policy_manifest: EvidenceArtifactRef
    prompt_policy_manifest: EvidenceArtifactRef
    human_benchmark_definition: EvidenceArtifactRef
    human_review_packet: EvidenceArtifactRef
    human_review_submission: EvidenceArtifactRef
    human_review_evaluation: EvidenceArtifactRef
    structured_benchmark_report: EvidenceArtifactRef
    hybrid_benchmark_report: EvidenceArtifactRef
    clean_rebuild_report: EvidenceArtifactRef


class V0ActivationStateManifest(StrictFrozenSchema):
    """Versioned map of every trust-bearing artifact consumed by publication."""

    activation_state_schema_version: Literal["v0-activation-state-v2"]
    product_version: Literal["V0"]
    package_version: Literal["0.1.0"]
    activation_evidence_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    release_key: str
    corpus_release_key: CorpusReleaseKey
    artifacts: V0ActivationArtifacts
    state_sha256: Sha256

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("activation state release_key is invalid")
        paths = tuple(
            getattr(self.artifacts, field_name).path
            for field_name in self.artifacts.__class__.model_fields
        )
        if len(paths) != len(set(paths)):
            raise ValueError("activation artifact paths must be unique")
        if self.state_sha256 != canonical_self_sha256(self, "state_sha256"):
            raise ValueError("activation state checksum does not match")
        return self


class ValidatedV0ActivationState(StrictFrozenSchema):
    """Small cross-validated projection safe for the aggregate release checks."""

    state_sha256: Sha256
    activation_evidence_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    release_key: str
    release_manifest_sha256: Sha256
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    dataset_receipt_key: StableToken
    dataset_receipt_sha256: Sha256
    dataset_publication_sha256: Sha256
    corpus_receipt_key: StableToken
    corpus_receipt_sha256: Sha256
    corpus_publication_sha256: Sha256
    structured_benchmark_report_sha256: Sha256
    hybrid_benchmark_report_sha256: Sha256
    clean_rebuild_sha256: Sha256
    human_packet_sha256: Sha256
    human_submission_sha256: Sha256
    human_evaluation_sha256: Sha256
    reviewer_key: StableToken
    reviewer_name: str
    reviewed_at: Rfc3339Utc
    reviewed_claim_count: int = Field(gt=0)
    source_artifact_sha256s: dict[ArtifactPath, Sha256] = Field(min_length=1)


def activation_validator_code_sha256() -> str:
    """Return the raw identity of this evidence-graph validator implementation."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ActivationStateError(f"activation artifact has a symlink component: {relative}")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise ActivationStateError(f"activation artifact is unavailable: {relative}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ActivationStateError(f"activation artifact escapes the repository: {relative}")
    return resolved


def _read_ref(root: Path, ref: EvidenceArtifactRef) -> bytes:
    path = _safe_file(root, ref.path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ActivationStateError(f"activation artifact is unreadable: {ref.path}") from exc
    if (
        len(raw) != ref.byte_size
        or not raw
        or len(raw) > MAX_ACTIVATION_ARTIFACT_BYTES
        or hashlib.sha256(raw).hexdigest() != ref.file_sha256
    ):
        raise ActivationStateError(f"activation artifact raw identity drifted: {ref.path}")
    return raw


def _git(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ActivationStateError("activation Git evidence is unavailable or invalid")
    return completed.stdout


def _require_commit(root: Path, commit: str, *, label: str) -> None:
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ActivationStateError(f"{label} must be one full lowercase Git commit")
    _git(root, "cat-file", "-e", f"{commit}^{{commit}}")


def _require_strict_ancestor(
    root: Path,
    ancestor: str,
    descendant: str,
    *,
    label: str,
) -> None:
    _require_commit(root, ancestor, label=f"{label} ancestor")
    _require_commit(root, descendant, label=f"{label} descendant")
    if ancestor == descendant:
        raise ActivationStateError(f"{label} commits must be distinct")
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ActivationStateError(f"{label} does not follow the required Git ancestry")


def _git_blob(root: Path, commit: str, relative: str) -> bytes:
    entry = _git(root, "ls-tree", "-z", "--full-tree", commit, "--", relative)
    suffix = b"\t" + relative.encode("utf-8") + b"\0"
    if entry.count(b"\0") != 1 or not entry.endswith(suffix):
        raise ActivationStateError(
            f"activation evidence is not one tracked regular blob: {relative}"
        )
    metadata = entry[: -len(suffix)].split(b" ")
    if (
        len(metadata) != 3
        or metadata[0] not in {b"100644", b"100755"}
        or metadata[1] != b"blob"
        or len(metadata[2]) != 40
        or any(character not in b"0123456789abcdef" for character in metadata[2])
    ):
        raise ActivationStateError(
            f"activation evidence is not one tracked regular blob: {relative}"
        )
    return _git(root, "cat-file", "blob", metadata[2].decode("ascii"))


def _verify_ref_at_commit(
    root: Path,
    commit: str,
    ref: EvidenceArtifactRef,
) -> None:
    current = _read_ref(root, ref)
    committed = _git_blob(root, commit, ref.path)
    if (
        committed != current
        or len(committed) != ref.byte_size
        or hashlib.sha256(committed).hexdigest() != ref.file_sha256
    ):
        raise ActivationStateError(
            f"activation artifact differs from its evidence commit blob: {ref.path}"
        )


def _require_runtime_identity_unchanged(
    root: Path,
    runtime_commit: str,
    activation_evidence_commit: str,
    publication_commit: str | None = None,
) -> None:
    for relative in _RUNTIME_IDENTITY_PATHS:
        runtime_raw = _git_blob(root, runtime_commit, relative)
        evidence_raw = _git_blob(root, activation_evidence_commit, relative)
        current = _safe_file(root, relative).read_bytes()
        publication_raw = (
            _git_blob(root, publication_commit, relative)
            if publication_commit is not None
            else current
        )
        if not (runtime_raw == evidence_raw == publication_raw == current):
            raise ActivationStateError(
                f"runtime code or lock drifted after the clean rebuild: {relative}"
            )


def build_v0_activation_state_manifest(
    root: Path,
    *,
    activation_evidence_commit: str,
    release_key: str,
    corpus_release_key: str,
    artifact_paths: Mapping[str, Path],
) -> V0ActivationStateManifest:
    """Bind the final typed evidence files to one already committed Git tree."""

    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ActivationStateError("activation repository root is unavailable") from exc
    _require_commit(root, activation_evidence_commit, label="activation evidence commit")
    expected_fields = frozenset(V0ActivationArtifacts.model_fields)
    if frozenset(artifact_paths) != expected_fields:
        raise ActivationStateError("activation artifact path set does not match the typed schema")

    refs: dict[str, EvidenceArtifactRef] = {}
    for field_name in V0ActivationArtifacts.model_fields:
        supplied = artifact_paths[field_name]
        if supplied.is_absolute():
            raise ActivationStateError("activation artifact paths must be repository-relative")
        try:
            relative = _validate_artifact_path(supplied.as_posix())
        except ValueError as exc:
            raise ActivationStateError("activation artifact path is invalid") from exc
        if relative == ACTIVATION_STATE_PATH.as_posix():
            raise ActivationStateError("activation state cannot include itself as evidence")
        raw = _safe_file(root, relative).read_bytes()
        if not raw or len(raw) > MAX_ACTIVATION_ARTIFACT_BYTES:
            raise ActivationStateError(f"activation artifact size is invalid: {relative}")
        ref = EvidenceArtifactRef(
            path=relative,
            byte_size=len(raw),
            file_sha256=hashlib.sha256(raw).hexdigest(),
        )
        _verify_ref_at_commit(root, activation_evidence_commit, ref)
        refs[field_name] = ref

    artifacts = V0ActivationArtifacts.model_validate(refs, strict=True)
    payload: dict[str, object] = {
        "activation_state_schema_version": ACTIVATION_STATE_SCHEMA_VERSION,
        "product_version": "V0",
        "package_version": "0.1.0",
        "activation_evidence_commit": activation_evidence_commit,
        "release_key": release_key,
        "corpus_release_key": corpus_release_key,
        "artifacts": artifacts,
        "state_sha256": "0" * 64,
    }
    payload["state_sha256"] = canonical_self_sha256(payload, "state_sha256")
    try:
        return V0ActivationStateManifest.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise ActivationStateError("activation state inputs are invalid") from exc


def _parse[ModelT: BaseModel](
    root: Path,
    ref: EvidenceArtifactRef,
    schema: type[ModelT],
) -> ModelT:
    try:
        return schema.model_validate_json(_read_ref(root, ref), strict=True)
    except ActivationStateError:
        raise
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ActivationStateError(
            f"activation artifact is not valid typed evidence: {ref.path}"
        ) from exc


def _question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ActivationStateError(message)


def _validate_human_graph(
    definition: HumanBenchmarkDefinition,
    packet: HumanReviewPacket,
    submission: HumanReviewSubmission,
    evaluation: HumanReviewEvaluation,
) -> None:
    definition_pairs = (
        (packet.definition_sha256, definition.definition_sha256),
        (packet.release_key, definition.release_key),
        (packet.release_manifest_sha256, definition.release_manifest_sha256),
        (packet.corpus_release_key, definition.corpus_release_key),
        (packet.corpus_manifest_sha256, definition.corpus_manifest_sha256),
        (packet.binding_manifest_sha256, definition.binding_manifest_sha256),
        (packet.anchor_manifest_sha256, definition.anchor_manifest_sha256),
        (packet.model_policy_manifest_sha256, definition.model_policy_manifest_sha256),
        (packet.prompt_policy_manifest_sha256, definition.prompt_policy_manifest_sha256),
    )
    _require(
        all(observed == expected for observed, expected in definition_pairs),
        "human benchmark definition and review packet identities differ",
    )
    _require(
        tuple(
            (case.case_ordinal, case.case_key, case.assembly_accession_version)
            for case in packet.cases
        )
        == tuple(
            (case.case_ordinal, case.case_key, case.assembly_accession_version)
            for case in definition.cases
        ),
        "human review packet does not retain the exact ten-case definition",
    )
    _require(
        all(
            packet_case.response.original_request.release_key == definition.release_key
            and packet_case.response.original_request.corpus_release_key
            == definition.corpus_release_key
            and packet_case.response.original_request.question == definition_case.question
            and packet_case.response.query_success.query_plan.original_question
            == definition_case.structured_question
            and packet_case.response.query_success.structured_result.release.release_key
            == definition.release_key
            and packet_case.response.query_success.structured_result.release.manifest_sha256
            == definition.release_manifest_sha256
            and packet_case.response.retrieved_chunks.corpus_release_key
            == definition.corpus_release_key
            and packet_case.response.retrieved_chunks.corpus_manifest_sha256
            == definition.corpus_manifest_sha256
            and packet_case.response.generation is not None
            and packet_case.response.generation.provider_identity.provider_key
            == definition.provider_key
            and packet_case.response.generation.provider_identity.model_key == definition.model_key
            and packet_case.response.generation.provider_identity.model_revision
            == definition.model_revision
            and packet_case.response.generation.provider_identity.provider_artifact_sha256
            == definition.model_policy_manifest_sha256
            and packet_case.response.generation.provider_identity.generation_policy_key
            == definition.generation_policy_key
            and packet_case.response.generation.provider_identity.prompt_policy_key
            == definition.prompt_policy_key
            and packet_case.response.generation.provider_identity.prompt_policy_sha256
            == definition.prompt_source_text_sha256
            and packet_case.response.execution.structured_retrieval_executed
            and packet_case.response.execution.literature_retrieval_executed
            and packet_case.response.execution.generation_executed
            for packet_case, definition_case in zip(packet.cases, definition.cases, strict=True)
        ),
        "human review packet responses do not bind the preregistered release and provider",
    )
    replayed = evaluate_human_review(packet, submission)
    _require(replayed == evaluation, "human review evaluation does not replay exactly")
    metrics = evaluation.metrics
    _require(
        evaluation.status == "passed"
        and not evaluation.issue_codes
        and metrics.case_count == 10
        and metrics.claim_count > 0
        and metrics.reviewed_claim_count == metrics.claim_count
        and metrics.supported_count == metrics.claim_count
        and metrics.partially_supported_count == 0
        and metrics.unsupported_count == 0
        and metrics.unreviewed_count == 0
        and all(
            value == _PERFECT_METRIC
            for value in (
                metrics.citation_existence,
                metrics.release_match,
                metrics.locator_validity,
                metrics.citation_coverage,
            )
        ),
        "human semantic-support evidence does not pass every ten-case gate",
    )
    _require(
        submission.reviewer_key == evaluation.reviewer_key
        and submission.reviewed_at == evaluation.reviewed_at
        and bool(submission.reviewer_name.strip()),
        "human review is not bound to one named reviewer",
    )


def validate_v0_activation_state(
    root: Path,
    *,
    state_path: Path = ACTIVATION_STATE_PATH,
    publication_commit: str | None = None,
) -> ValidatedV0ActivationState:
    """Strictly load and cross-bind the complete V0 activation evidence graph.

    The clean rebuild names the runtime commit ``R``.  The state names the later
    evidence commit ``E`` that contains every referenced report, and an outer
    publication preflight supplies the still-later clean publication commit ``S``.
    Requiring ``R < E < S`` removes both commit/self-hash fixed points.
    """

    try:
        root = root.resolve(strict=True)
        relative_state = _validate_artifact_path(state_path.as_posix())
        state_file = _safe_file(root, relative_state)
        state_raw = state_file.read_bytes()
        if not state_raw or len(state_raw) > MAX_ACTIVATION_ARTIFACT_BYTES:
            raise ActivationStateError("activation state manifest size is invalid")
        state = V0ActivationStateManifest.model_validate_json(state_raw, strict=True)
    except ActivationStateError:
        raise
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise ActivationStateError("activation state manifest is unavailable or invalid") from exc

    _require_commit(
        root,
        state.activation_evidence_commit,
        label="activation evidence commit",
    )
    if publication_commit is not None:
        _require_strict_ancestor(
            root,
            state.activation_evidence_commit,
            publication_commit,
            label="activation evidence/publication",
        )
        if _git_blob(root, publication_commit, relative_state) != state_raw:
            raise ActivationStateError("activation state differs from the publication commit blob")

    refs = state.artifacts
    for field_name in V0ActivationArtifacts.model_fields:
        ref = getattr(refs, field_name)
        _verify_ref_at_commit(root, state.activation_evidence_commit, ref)
        if publication_commit is not None:
            _verify_ref_at_commit(root, publication_commit, ref)
    structured = _parse(root, refs.structured_activation_manifest, StructuredActivationManifest)
    dataset_receipt = _parse(root, refs.dataset_receipt_evidence, TrustedDatasetReceiptEvidence)
    dataset_publication = _parse(
        root, refs.dataset_publication_evidence, DatasetPublicationEvidence
    )
    corpus = _parse(root, refs.corpus_manifest, CorpusManifest)
    anchors = _parse(root, refs.corpus_anchor_manifest, CorpusAnchorManifest)
    corpus_export = _parse(root, refs.corpus_receipt_export, CorpusValidationExport)
    corpus_publication = _parse(root, refs.corpus_publication_evidence, CorpusPublicationEvidence)
    binding = _parse(root, refs.hybrid_binding_manifest, HybridReleaseBindingManifest)
    model_policy = _parse(root, refs.model_policy_manifest, LocalModelPolicyManifest)
    prompt_policy = _parse(root, refs.prompt_policy_manifest, PromptPolicyManifest)
    definition = _parse(root, refs.human_benchmark_definition, HumanBenchmarkDefinition)
    packet = _parse(root, refs.human_review_packet, HumanReviewPacket)
    submission = _parse(root, refs.human_review_submission, HumanReviewSubmission)
    evaluation = _parse(root, refs.human_review_evaluation, HumanReviewEvaluation)
    structured_benchmark = _parse(root, refs.structured_benchmark_report, V0RouteBenchmarkReport)
    hybrid_benchmark = _parse(root, refs.hybrid_benchmark_report, V0RouteBenchmarkReport)
    rebuild = _parse(root, refs.clean_rebuild_report, V0CleanActivationRebuildReport)

    _require_strict_ancestor(
        root,
        rebuild.release_commit,
        state.activation_evidence_commit,
        label="clean rebuild/activation evidence",
    )
    _require_runtime_identity_unchanged(
        root,
        rebuild.release_commit,
        state.activation_evidence_commit,
        publication_commit,
    )

    dataset_key, dataset_sha256 = dataset_receipt_identity(dataset_receipt)
    corpus_key, corpus_sha256 = corpus_receipt_identity(corpus_export.receipt.validation_report)
    approved = dataset_receipt.validation_input
    activation = approved.activation_evidence
    candidate = approved.candidate_validation_input
    candidate_activation = candidate.candidate_activation_evidence
    corpus_evidence = corpus_export.receipt.validation_report
    candidate_capability_sha256 = structured_candidate_capability_sha256(candidate)

    publication_pairs = (
        (dataset_publication.release_key, state.release_key),
        (dataset_publication.manifest_sha256, structured.manifest_sha256),
        (dataset_publication.receipt_key, dataset_key),
        (dataset_publication.receipt_sha256, dataset_sha256),
        (corpus_publication.corpus_release_key, state.corpus_release_key),
        (corpus_publication.manifest_sha256, corpus.manifest_sha256),
        (corpus_publication.receipt_key, corpus_key),
        (corpus_publication.receipt_sha256, corpus_sha256),
    )
    _require(
        all(observed == expected for observed, expected in publication_pairs),
        "final activation is not bound to both exact published receipt transitions",
    )

    _require(
        candidate.validator_code_sha256 == release_validator_code_sha256(),
        "dataset receipt validator code identity is stale",
    )
    _require(
        dataset_receipt.dependency_graph_sha256 == candidate.expected_dependency_graph_sha256,
        "dataset receipt dependency graph differs from the approved candidate input",
    )
    _require(
        candidate_activation.activation_policy_code_sha256
        == structured_activation_policy_code_sha256(),
        "structured activation policy code identity is stale",
    )
    _require(
        state.release_key
        == structured.release_key
        == approved.release_key
        == candidate.release_key
        == candidate_activation.release_key
        == activation.release_key,
        "structured release keys do not form one activation",
    )
    _require(
        approved.release_manifest_sha256
        == structured.manifest_sha256
        == candidate.release_manifest_sha256
        == candidate_activation.structured_activation_manifest_sha256,
        "dataset receipt does not bind the structured activation manifest",
    )
    structured_pairs = (
        (candidate_activation.source_manifest_sha256, structured.source_manifest_sha256),
        (candidate_activation.source_audit_sha256, structured.source_audit_sha256),
        (
            candidate_activation.ncbi_artifact_manifest_sha256,
            structured.ncbi_artifact_manifest_sha256,
        ),
        (
            candidate_activation.ncbi_snapshot_manifest_sha256,
            structured.ncbi_snapshot_manifest_sha256,
        ),
        (
            candidate_activation.ictv_artifact_manifest_sha256,
            structured.ictv_artifact_manifest_sha256,
        ),
        (
            candidate_activation.ictv_snapshot_manifest_sha256,
            structured.ictv_snapshot_manifest_sha256,
        ),
        (candidate_activation.flank_manifest_sha256, structured.flank_manifest_sha256),
        (
            candidate_activation.inclusion_manifest_sha256,
            structured.inclusion_manifest_sha256,
        ),
        (
            candidate_activation.adjudication_manifest_sha256,
            structured.adjudication_manifest_sha256,
        ),
        (
            candidate_activation.public_locus_membership_manifest_sha256,
            structured.public_locus_membership_manifest_sha256,
        ),
        (
            candidate_activation.public_assertion_membership_manifest_sha256,
            structured.public_assertion_membership_manifest_sha256,
        ),
    )
    _require(
        all(observed == expected for observed, expected in structured_pairs),
        "dataset activation evidence differs from the typed structured packet",
    )

    _require(
        state.corpus_release_key
        == corpus.corpus_release_key
        == anchors.corpus_release_key
        == corpus_export.corpus_release.corpus_release_key,
        "corpus release keys do not form one activation",
    )
    _require(
        corpus.manifest_sha256
        == anchors.corpus_manifest_sha256
        == corpus_export.corpus_release.manifest_sha256
        == corpus_evidence.rebuild_report.manifest_sha256,
        "corpus manifest identities do not form one activation",
    )
    _require(
        anchors.anchor_manifest_sha256 == corpus_evidence.anchor_manifest_sha256,
        "corpus receipt does not bind the anchor manifest",
    )
    _require(
        corpus.document_count == corpus_export.corpus_release.manifest_document_count,
        "corpus export document count differs from the typed manifest",
    )

    exact_bindings = tuple(
        item
        for item in binding.bindings
        if item.release_key == state.release_key
        and item.corpus_release_key == state.corpus_release_key
    )
    _require(
        len(binding.bindings) == 1
        and len(exact_bindings) == 1
        and exact_bindings[0].release_manifest_sha256 == structured.manifest_sha256
        and exact_bindings[0].corpus_manifest_sha256 == corpus.manifest_sha256,
        "hybrid binding manifest is not the one exact release/corpus pair",
    )

    prompt_policy.require_approved_v0_policy()
    _require(
        model_policy.prompt_policy_manifest_sha256 == prompt_policy.manifest_sha256,
        "model policy is not bound to the prompt policy",
    )
    definition_pairs = (
        (definition.release_key, state.release_key),
        (definition.release_manifest_sha256, structured.manifest_sha256),
        (definition.corpus_release_key, state.corpus_release_key),
        (definition.corpus_manifest_sha256, corpus.manifest_sha256),
        (definition.binding_manifest_sha256, binding.manifest_sha256),
        (definition.anchor_manifest_sha256, anchors.anchor_manifest_sha256),
        (definition.model_policy_manifest_sha256, model_policy.manifest_sha256),
        (definition.prompt_policy_manifest_sha256, prompt_policy.manifest_sha256),
        (definition.provider_key, model_policy.provider_key),
        (definition.model_key, model_policy.model_key),
        (definition.model_revision, model_policy.model_revision),
        (definition.generation_policy_key, model_policy.generation_policy_key),
        (definition.prompt_policy_key, prompt_policy.prompt_policy_key),
        (definition.prompt_source_text_sha256, prompt_policy.source_text_sha256),
        (definition.timeout_seconds, model_policy.timeout_seconds),
    )
    _require(
        all(observed == expected for observed, expected in definition_pairs),
        "human benchmark definition is not bound to approved release and provider policies",
    )
    _validate_human_graph(definition, packet, submission, evaluation)

    _require(
        structured_benchmark.route == "structured" and hybrid_benchmark.route == "hybrid",
        "real route benchmark files are swapped or mislabeled",
    )
    for report in (structured_benchmark, hybrid_benchmark):
        _require(
            report.release_key == state.release_key
            and report.release_manifest_sha256 == structured.manifest_sha256
            and report.candidate_validation_input_sha256 == candidate.input_sha256
            and report.dataset_validation_request_sha256 == candidate.validation_request_sha256
            and report.dependency_graph_sha256 == dataset_receipt.dependency_graph_sha256
            and report.candidate_capability_sha256 == candidate_capability_sha256,
            "real route benchmark does not bind the acyclic dataset candidate identity",
        )
    _require(
        tuple(case.case_key for case in structured_benchmark.cases)
        == tuple(case.case_key for case in definition.cases)
        and tuple(case.question_sha256 for case in structured_benchmark.cases)
        == tuple(_question_sha256(case.structured_question) for case in definition.cases),
        "structured benchmark does not cover the preregistered ten-case cohort",
    )
    structured_responses = tuple(case.structured_response for case in structured_benchmark.cases)
    _require(
        all(response is not None for response in structured_responses),
        "structured benchmark is missing a typed response",
    )
    _require(
        all(
            response is not None
            and response.original_request.release_key == state.release_key
            and response.original_request.corpus_release_key is None
            and response.original_request.question == definition_case.structured_question
            and response.query_success.structured_result.release.release_key == state.release_key
            and response.query_success.structured_result.release.manifest_sha256
            == structured.manifest_sha256
            and response.query_success.structured_result.release.status == "validation_candidate"
            and response.query_success.structured_result.release.candidate_validation_input_sha256
            == candidate.input_sha256
            and response.query_success.structured_result.release.candidate_capability_sha256
            == candidate_capability_sha256
            for response, definition_case in zip(
                structured_responses, definition.cases, strict=True
            )
        ),
        "structured benchmark responses do not bind the exact release and questions",
    )
    _require(
        all(
            case.response.query_success.structured_result.release.status == "validation_candidate"
            and (
                case.response.query_success.structured_result.release.candidate_validation_input_sha256
                == candidate.input_sha256
            )
            and (
                case.response.query_success.structured_result.release.candidate_capability_sha256
                == candidate_capability_sha256
            )
            for case in packet.cases
        ),
        "hybrid review responses do not retain the approved candidate capability",
    )
    _require(
        tuple(case.case_key for case in hybrid_benchmark.cases)
        == tuple(case.case_key for case in definition.cases)
        and tuple(case.question_sha256 for case in hybrid_benchmark.cases)
        == tuple(_question_sha256(case.question) for case in definition.cases)
        and tuple(case.response_sha256 for case in hybrid_benchmark.cases)
        == tuple(case.response_sha256 for case in packet.cases),
        "hybrid benchmark does not bind the reviewed ten-case responses",
    )
    hybrid_pairs = (
        (hybrid_benchmark.corpus_release_key, state.corpus_release_key),
        (hybrid_benchmark.corpus_manifest_sha256, corpus.manifest_sha256),
        (hybrid_benchmark.corpus_receipt_sha256, corpus_sha256),
        (hybrid_benchmark.binding_manifest_sha256, binding.manifest_sha256),
        (hybrid_benchmark.human_review_evaluation_sha256, evaluation.evaluation_sha256),
    )
    _require(
        all(observed == expected for observed, expected in hybrid_pairs),
        "hybrid benchmark corpus, binding, or review identity drifted",
    )

    activation_pairs = (
        (activation.candidate_validation_input_sha256, candidate.input_sha256),
        (activation.structured_benchmark_report_sha256, structured_benchmark.report_sha256),
        (activation.hybrid_benchmark_report_sha256, hybrid_benchmark.report_sha256),
        (activation.human_review_report_sha256, evaluation.evaluation_sha256),
        (activation.clean_rebuild_report_sha256, rebuild.rebuild_sha256),
    )
    _require(
        all(observed == expected for observed, expected in activation_pairs),
        "dataset activation receipt pass flags are not backed by exact typed artifacts",
    )

    try:
        lock_sha256 = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    except OSError as exc:
        raise ActivationStateError("the dependency lock is unavailable") from exc
    rebuild_pairs = (
        (rebuild.release_key, state.release_key),
        (rebuild.release_manifest_sha256, structured.manifest_sha256),
        (rebuild.corpus_release_key, state.corpus_release_key),
        (rebuild.corpus_manifest_sha256, corpus.manifest_sha256),
        (
            rebuild.candidate_validation_input_sha256,
            candidate.input_sha256,
        ),
        (
            rebuild.dataset_validation_request_sha256,
            candidate.validation_request_sha256,
        ),
        (rebuild.dependency_graph_sha256, dataset_receipt.dependency_graph_sha256),
        (rebuild.candidate_capability_sha256, candidate_capability_sha256),
        (rebuild.corpus_receipt_sha256, corpus_sha256),
        (rebuild.corpus_rebuild_report_sha256, corpus_evidence.rebuild_report.rebuild_sha256),
        (rebuild.structured_benchmark_report_sha256, structured_benchmark.report_sha256),
        (rebuild.hybrid_benchmark_report_sha256, hybrid_benchmark.report_sha256),
        (rebuild.human_review_evaluation_sha256, evaluation.evaluation_sha256),
        (rebuild.dependency_lock_sha256, lock_sha256),
        (rebuild.dataset_validator_code_sha256, candidate.validator_code_sha256),
        (rebuild.activation_policy_code_sha256, structured_activation_policy_code_sha256()),
        (
            rebuild.activation_state_validator_code_sha256,
            activation_validator_code_sha256(),
        ),
        (
            rebuild.corpus_validator_code_sha256,
            corpus_evidence.validator_code_sha256,
        ),
        (rebuild.corpus_importer_code_sha256, corpus_export.import_run.code_sha256),
        (
            rebuild.corpus_policy_code_sha256,
            corpus_export.import_run.parameters.policy_code_sha256,
        ),
    )
    _require(
        all(observed == expected for observed, expected in rebuild_pairs),
        "clean activation rebuild identities do not match the evidence graph",
    )
    _require(
        tuple(item.evidence_sha256 for item in rebuild.route_replays)
        == (
            structured_benchmark.report_sha256,
            corpus_evidence.rebuild_report.rebuild_sha256,
            hybrid_benchmark.report_sha256,
        ),
        "clean activation rebuild route replays do not bind the exact reports",
    )

    artifact_hashes = {
        ref.path: ref.file_sha256
        for field_name in refs.__class__.model_fields
        for ref in (getattr(refs, field_name),)
    }
    return ValidatedV0ActivationState(
        state_sha256=state.state_sha256,
        activation_evidence_commit=state.activation_evidence_commit,
        release_key=state.release_key,
        release_manifest_sha256=structured.manifest_sha256,
        corpus_release_key=state.corpus_release_key,
        corpus_manifest_sha256=corpus.manifest_sha256,
        dataset_receipt_key=dataset_key,
        dataset_receipt_sha256=dataset_sha256,
        dataset_publication_sha256=dataset_publication.publication_sha256,
        corpus_receipt_key=corpus_key,
        corpus_receipt_sha256=corpus_sha256,
        corpus_publication_sha256=corpus_publication.publication_sha256,
        structured_benchmark_report_sha256=structured_benchmark.report_sha256,
        hybrid_benchmark_report_sha256=hybrid_benchmark.report_sha256,
        clean_rebuild_sha256=rebuild.rebuild_sha256,
        human_packet_sha256=packet.packet_sha256,
        human_submission_sha256=submission.submission_sha256,
        human_evaluation_sha256=evaluation.evaluation_sha256,
        reviewer_key=submission.reviewer_key,
        reviewer_name=submission.reviewer_name,
        reviewed_at=submission.reviewed_at,
        reviewed_claim_count=evaluation.metrics.reviewed_claim_count,
        source_artifact_sha256s=dict(sorted(artifact_hashes.items())),
    )


__all__ = [
    "ACTIVATION_STATE_PATH",
    "ACTIVATION_STATE_SCHEMA_VERSION",
    "ActivationStateError",
    "CorpusPublicationEvidence",
    "CorpusValidationExport",
    "DatasetPublicationEvidence",
    "EvidenceArtifactRef",
    "V0ActivationArtifacts",
    "V0ActivationStateManifest",
    "V0CleanActivationRebuildReport",
    "V0RouteBenchmarkCase",
    "V0RouteBenchmarkReport",
    "ValidatedV0ActivationState",
    "activation_validator_code_sha256",
    "build_v0_activation_state_manifest",
    "validate_v0_activation_state",
]
