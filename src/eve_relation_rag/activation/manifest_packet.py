"""Strict, candidate-only Checkpoint 2 Activation Manifest Packet.

The packet is an approval object, not a release receipt.  It distinguishes the
semantic self-digest of every typed manifest from the SHA-256 of that manifest's
physical JSON file, verifies the complete candidate evidence graph, and contains
an explicit fail-closed boundary excluding human verdicts, structured receipts,
database writes by packet construction, publication, and published-status claims.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, Field, ValidationError, model_validator

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    AdjudicationCohortManifest,
    AssemblyTaxonAssignmentManifest,
    FlankEvidenceManifest,
    FlankEvidenceRequestPlan,
    FullSequenceBundleManifest,
    IctvArtifactManifest,
    InclusionDecisionManifest,
    NcbiTaxonomyArtifactManifest,
    PublicAssertionMembershipManifest,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StructuredAdjudicationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
)
from eve_relation_rag.activation.corpus import (
    FORMAL_AMPHINTOVIRALES_TERM_KEY,
    STUDY_ORTHOPOLINTOVIRALES_TERM_KEY,
    V0_CORPUS_RELEASE_KEY,
    V0_STRUCTURED_ANCHOR_CURATION_METHOD,
)
from eve_relation_rag.activation.flanks import (
    build_flank_evidence_manifest,
    build_flank_request_plan,
)
from eve_relation_rag.activation.policy import (
    DependencyBindings,
    InclusionEvaluationInput,
    build_adjudication_manifest,
    build_inclusion_manifest,
    build_public_assertion_membership_manifest,
    build_public_locus_membership_manifest,
)
from eve_relation_rag.activation.release_state import CorpusValidationExport
from eve_relation_rag.generation.human_review import (
    HumanBenchmarkDefinition,
    build_human_benchmark_definition,
)
from eve_relation_rag.generation.policy import (
    LocalModelPolicyManifest,
    PromptPolicyManifest,
)
from eve_relation_rag.generation.qualification import (
    ProviderEnvironmentManifestBinding,
    ProviderQualificationDefinition,
    ProviderQualificationError,
    ProviderQualificationReport,
    QualificationFileIdentity,
    verify_provider_qualification_definition,
    verify_provider_qualification_report,
)
from eve_relation_rag.hybrid.contracts import (
    HybridReleaseBindingManifest,
    StrictFrozenSchema,
    canonical_model_json,
    canonical_self_sha256,
)
from eve_relation_rag.literature.anchors import CorpusAnchorManifest
from eve_relation_rag.literature.contracts import (
    CorpusManifest,
    Rfc3339Utc,
    Sha256,
    StableToken,
)

PACKET_SCHEMA_VERSION: Final = "v0-activation-manifest-packet-v1"
PACKET_KEY: Final = "activation-manifest-packet:endoviho-rag:v0:checkpoint-2:a"
MAX_PACKET_INPUT_BYTES: Final = 128 * 1024 * 1024
MAX_PACKET_OUTPUT_BYTES: Final = 4 * 1024 * 1024

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_MD5_RE: Final = re.compile(r"^[0-9a-f]{32}$")
_CANONICAL_DISTRIBUTION_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_READ_CHUNK_SIZE: Final = 1024 * 1024

_NCBI_CAPTURE_SHA256: Final = "8ad8f6f186ca51ec73a5fb8935ecfa17b8cbaad300b7025b381898ab72621869"
_ICTV_CAPTURE_SHA256: Final = "4c8bc175029519fe34003254cc2c01fbac9ba00bb2086cf08a96f03a54efc4df"
_PROPOSAL_CAPTURE_SHA256: Final = "c11d6f496ff610a33862e1993b6f27d967563478e8c24b80b882037ba16bfd62"

_AUTHORITY_SPECS: Final = {
    "authority-capture:ncbi-usage-policy:20260829": (
        "https://www.ncbi.nlm.nih.gov/home/about/policies/",
        "2026-08-29T06:41:28Z",
        _NCBI_CAPTURE_SHA256,
        38_936,
        "text/html",
    ),
    "authority-capture:ictv-taxonomy-cc-by-4.0:20260829": (
        "https://ictv.global/taxonomy",
        "2026-08-29T06:41:28Z",
        _ICTV_CAPTURE_SHA256,
        62_480,
        "text/html",
    ),
    "authority-capture:ictv-proposal-2024.010D": (
        "https://ictv.global/system/files/proposals/approved/"
        "Animal_DNA_viruses_and_Retroviruses/2024.010D.Varidnaviria_reorg.xlsx",
        "2026-08-29T06:41:28Z",
        _PROPOSAL_CAPTURE_SHA256,
        26_852,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


class ActivationManifestPacketError(RuntimeError):
    """Checkpoint 2 evidence is unavailable, drifting, or crosses its authority boundary."""


def _validate_artifact_path(value: str) -> str:
    pure = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or pure.is_absolute()
        or value != pure.as_posix()
        or pure.as_posix() in {".", ".."}
        or ".." in pure.parts
        or any(part in {"", "."} for part in pure.parts)
    ):
        raise ValueError("artifact path must be canonical and repository-relative")
    return value


ArtifactPath = Annotated[
    str,
    Field(min_length=1, max_length=2048),
    AfterValidator(_validate_artifact_path),
]


class RawFileIdentity(StrictFrozenSchema):
    """Physical file identity; never interchangeable with a typed semantic digest."""

    identity_schema_version: Literal["raw-file-sha256-v1"] = "raw-file-sha256-v1"
    path: ArtifactPath
    byte_size: int = Field(gt=0, le=MAX_PACKET_INPUT_BYTES)
    file_sha256: Sha256


class TypedSemanticIdentity(StrictFrozenSchema):
    """Self-digest extracted only after strict validation through a named schema."""

    identity_schema_version: Literal["typed-semantic-self-sha256-v1"] = (
        "typed-semantic-self-sha256-v1"
    )
    schema_version_field: StableToken
    schema_version: StableToken
    digest_field: Literal[
        "manifest_sha256",
        "anchor_manifest_sha256",
        "definition_sha256",
        "report_sha256",
    ]
    semantic_sha256: Sha256


class TypedArtifactIdentity(StrictFrozenSchema):
    """One typed semantic identity plus the distinct file that transported it."""

    raw_file: RawFileIdentity
    semantic: TypedSemanticIdentity


class AuthorityCapture(StrictFrozenSchema):
    """Exact raw capture preregistered by the approved contract errata."""

    capture_key: Literal[
        "authority-capture:ncbi-usage-policy:20260829",
        "authority-capture:ictv-taxonomy-cc-by-4.0:20260829",
        "authority-capture:ictv-proposal-2024.010D",
    ]
    source_uri: str
    retrieved_at: Rfc3339Utc
    media_type: StableToken
    raw_file: RawFileIdentity

    @model_validator(mode="after")
    def validate_preregistered_capture(self) -> Self:
        expected_uri, expected_time, expected_sha256, expected_size, expected_media = (
            _AUTHORITY_SPECS[self.capture_key]
        )
        observed = (
            self.source_uri,
            self.retrieved_at,
            self.raw_file.file_sha256,
            self.raw_file.byte_size,
            self.media_type,
        )
        if observed != (
            expected_uri,
            expected_time,
            expected_sha256,
            expected_size,
            expected_media,
        ):
            raise ValueError("authority capture differs from the approved errata")
        return self


class ContractEvidence(StrictFrozenSchema):
    contract_name: Literal["V0 Activation and Publication Contract — Draft A"]
    contract_status: Literal["approved"]
    approved_on: Literal["2026-08-29"]
    approved_contract: RawFileIdentity
    errata_status: Literal["pending_activation_manifest_packet_approval"]
    errata_ids: tuple[Literal["E1"], Literal["E2"]]
    errata: RawFileIdentity


class ExcludedSourceArtifact(StrictFrozenSchema):
    reason_codes: tuple[
        Literal["publisher_md5_mismatch"],
        Literal["retrieved_byte_size_mismatch"],
    ]
    raw_file: RawFileIdentity
    used_by_candidate: Literal[False]


class FrozenSourceEvidence(StrictFrozenSchema):
    """Raw sources whose hashes are referenced by the typed activation graph."""

    m1_source_manifest: RawFileIdentity
    m1_source_audit: RawFileIdentity
    ncbi_taxdump_archive: RawFileIdentity
    ncbi_taxdump_checksum: RawFileIdentity
    ictv_msl_workbook: RawFileIdentity
    ictv_vmr_workbook: RawFileIdentity
    full_sequence_bundle: RawFileIdentity
    excluded_taxdump_candidates: tuple[
        ExcludedSourceArtifact,
        ExcludedSourceArtifact,
    ]

    @model_validator(mode="after")
    def validate_exclusion_order(self) -> Self:
        paths = tuple(row.raw_file.path for row in self.excluded_taxdump_candidates)
        if paths != tuple(sorted(paths)):
            raise ValueError("excluded taxdump candidates must retain canonical path order")
        return self


class StructuredPacketArtifacts(StrictFrozenSchema):
    ncbi_artifact_manifest: TypedArtifactIdentity
    ncbi_snapshot_manifest: TypedArtifactIdentity
    assembly_taxon_assignment_manifest: TypedArtifactIdentity
    ictv_artifact_manifest: TypedArtifactIdentity
    ictv_snapshot_manifest: TypedArtifactIdentity
    study_formal_mapping_manifest: TypedArtifactIdentity
    adjudication_cohort_manifest: TypedArtifactIdentity
    full_sequence_bundle_manifest: TypedArtifactIdentity
    flank_request_plan_manifest: TypedArtifactIdentity
    flank_evidence_manifest: TypedArtifactIdentity
    inclusion_decision_manifest: TypedArtifactIdentity
    structured_adjudication_manifest: TypedArtifactIdentity
    public_locus_membership_manifest: TypedArtifactIdentity
    public_assertion_membership_manifest: TypedArtifactIdentity
    structured_activation_manifest: TypedArtifactIdentity


class CorpusPacketArtifacts(StrictFrozenSchema):
    corpus_manifest: TypedArtifactIdentity
    anchor_manifest: TypedArtifactIdentity
    corpus_validation_receipt: TypedArtifactIdentity
    hybrid_binding_manifest: TypedArtifactIdentity


class ProviderPacketArtifacts(StrictFrozenSchema):
    provider_environment_verifier: RawFileIdentity
    provider_environment_manifest: TypedArtifactIdentity
    local_model_policy_manifest: TypedArtifactIdentity
    prompt_policy_manifest: TypedArtifactIdentity
    provider_qualification_runner: RawFileIdentity
    provider_qualification_module: RawFileIdentity
    provider_qualification_definition: TypedArtifactIdentity
    provider_qualification_report: TypedArtifactIdentity


class BenchmarkPacketArtifacts(StrictFrozenSchema):
    human_benchmark_definition: TypedArtifactIdentity


class StructuredPacketSummary(StrictFrozenSchema):
    primary_assessed_count: Literal[71]
    expansion_assessed_count: int = Field(ge=0)
    source_low_invoked: bool
    include_count: int = Field(ge=10)
    quarantine_decision_count: int = Field(ge=0)
    review_decision_count: int = Field(ge=0)
    exclude_decision_count: int = Field(ge=0)
    public_locus_count: int = Field(ge=10)
    public_assertion_count: int = Field(ge=1)
    ncbi_term_count: int = Field(gt=0)
    ictv_term_count: int = Field(gt=0)
    assembly_assignment_count: Literal[10]
    study_formal_mapping_count: Literal[1]
    family_mapping_count: Literal[0]
    mapping_relation: Literal["renamed_to"]
    all_ten_assemblies_passing: Literal[True]

    @model_validator(mode="after")
    def validate_expansion_flag(self) -> Self:
        if self.source_low_invoked != (self.expansion_assessed_count > 0):
            raise ValueError("source_low invocation flag disagrees with assessed expansion rows")
        if self.include_count != self.public_locus_count:
            raise ValueError("all and only include decisions must be public loci")
        return self


class CorpusPacketSummary(StrictFrozenSchema):
    document_count: Literal[11]
    anchor_count: int = Field(ge=1)
    structured_lineage_anchor_count: Literal[8]
    corpus_release_status: Literal["validated"]
    receipt_status: Literal["passed"]
    receipt_trusted: Literal[True]
    published_status_claimed: Literal[False]


class ProviderPacketSummary(StrictFrozenSchema):
    provider_key: StableToken
    model_key: StableToken
    model_revision: StableToken
    environment_distribution_count: int = Field(gt=0)
    environment_file_count: int = Field(gt=0)
    network_policy_key: Literal["network:macos-sandbox-v0-ports-only-v2"]
    qualification_candidate_count: Literal[1]
    qualification_status: Literal["passed"]
    qualification_selection: Literal["only_passing_candidate"]
    qualification_request_count: Literal[1]
    qualification_retry_count: Literal[0]
    qualification_hmac_attestation_verified: Literal[True]
    qualification_inner_unauthenticated_status: Literal[401]
    qualification_clean_shutdown: Literal[True]
    external_provider_authorized: Literal[False]


class BenchmarkPacketSummary(StrictFrozenSchema):
    case_count: Literal[10]
    expected_matched_target_count: Literal[10]
    expected_unmatched_target_count: Literal[30]
    assembly_count: Literal[10]
    human_semantic_verdict_included: Literal[False]


class PacketSummary(StrictFrozenSchema):
    structured: StructuredPacketSummary
    corpus: CorpusPacketSummary
    provider: ProviderPacketSummary
    benchmark: BenchmarkPacketSummary


class CandidateApprovalBoundary(StrictFrozenSchema):
    checkpoint: Literal[2]
    owner_approval_required: Literal[True]
    packet_build_database_writes_performed: Literal[False]
    production_database_role_qualified: Literal[False]
    structured_validation_receipt_included: Literal[False]
    human_semantic_verdict_included: Literal[False]
    published_status_claimed: Literal[False]
    publication_authorized: Literal[False]
    external_tag_release_or_image_authorized: Literal[False]


class V0ActivationManifestPacket(StrictFrozenSchema):
    """Exact Checkpoint 2 candidate presented for checksum-level owner approval."""

    packet_schema_version: Literal["v0-activation-manifest-packet-v1"]
    packet_key: Literal["activation-manifest-packet:endoviho-rag:v0:checkpoint-2:a"]
    checkpoint: Literal[2]
    status: Literal["candidate_for_owner_approval"]
    product_version: Literal["V0"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    corpus_release_key: Literal["corpus:endoviho-rag:v0:20260829:001"]
    contract: ContractEvidence
    authority_captures: tuple[AuthorityCapture, AuthorityCapture, AuthorityCapture]
    frozen_sources: FrozenSourceEvidence
    structured: StructuredPacketArtifacts
    corpus: CorpusPacketArtifacts
    provider: ProviderPacketArtifacts
    benchmark: BenchmarkPacketArtifacts
    summary: PacketSummary
    boundary: CandidateApprovalBoundary
    packet_sha256: Sha256

    @model_validator(mode="after")
    def validate_packet(self) -> Self:
        capture_keys = tuple(row.capture_key for row in self.authority_captures)
        if capture_keys != tuple(_AUTHORITY_SPECS):
            raise ValueError("authority captures must use the approved canonical order")
        paths = tuple(reference.path for reference in _all_raw_refs(self))
        if len(paths) != len(set(paths)):
            raise ValueError("activation packet artifact paths must be unique")
        if self.packet_sha256 != canonical_self_sha256(self, "packet_sha256"):
            raise ValueError("activation manifest packet checksum does not match")
        return self


class ProviderEnvironmentDistribution(StrictFrozenSchema):
    canonical_name: str = Field(pattern=_CANONICAL_DISTRIBUTION_RE.pattern)
    version: StableToken
    file_count: int = Field(gt=0)
    record_sha256: Sha256


class ProviderEnvironmentManifest(StrictFrozenSchema):
    """Packaged mirror of the stdlib-only provider environment manifest contract."""

    manifest_schema_version: Literal["v0-provider-environment-manifest-v1"]
    identity_schema_version: Literal["v0-provider-environment-identity-v1"]
    provider_environment_sha256: Sha256
    provider_environment_distribution_count: int = Field(gt=0)
    provider_environment_file_count: int = Field(gt=0)
    distributions: tuple[ProviderEnvironmentDistribution, ...] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        names = tuple(row.canonical_name for row in self.distributions)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("provider distributions must be unique and canonically ordered")
        if self.provider_environment_distribution_count != len(self.distributions):
            raise ValueError("provider environment distribution count does not match")
        if self.provider_environment_file_count != sum(
            row.file_count for row in self.distributions
        ):
            raise ValueError("provider environment file count does not match")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("provider environment manifest checksum does not match")
        return self


@dataclass(frozen=True, slots=True)
class StructuredPacketPaths:
    ncbi_artifact_manifest: Path
    ncbi_snapshot_manifest: Path
    assembly_taxon_assignment_manifest: Path
    ictv_artifact_manifest: Path
    ictv_snapshot_manifest: Path
    study_formal_mapping_manifest: Path
    adjudication_cohort_manifest: Path
    full_sequence_bundle_manifest: Path
    flank_request_plan_manifest: Path
    flank_evidence_manifest: Path
    inclusion_decision_manifest: Path
    structured_adjudication_manifest: Path
    public_locus_membership_manifest: Path
    public_assertion_membership_manifest: Path
    structured_activation_manifest: Path


@dataclass(frozen=True, slots=True)
class CorpusPacketPaths:
    corpus_manifest: Path
    anchor_manifest: Path
    corpus_validation_receipt: Path
    hybrid_binding_manifest: Path


@dataclass(frozen=True, slots=True)
class ProviderPacketPaths:
    provider_environment_verifier: Path
    provider_environment_manifest: Path
    local_model_policy_manifest: Path
    prompt_policy_manifest: Path
    provider_qualification_runner: Path
    provider_qualification_module: Path
    provider_qualification_definition: Path
    provider_qualification_report: Path


@dataclass(frozen=True, slots=True)
class SourcePacketPaths:
    m1_source_manifest: Path
    m1_source_audit: Path
    ncbi_taxdump_archive: Path
    ncbi_taxdump_checksum: Path
    ictv_msl_workbook: Path
    ictv_vmr_workbook: Path
    full_sequence_bundle: Path
    excluded_taxdump_md5: Path
    excluded_taxdump_size: Path


@dataclass(frozen=True, slots=True)
class AuthorityPacketPaths:
    ncbi_usage_policy_capture: Path
    ictv_usage_policy_capture: Path
    ictv_proposal_capture: Path


@dataclass(frozen=True, slots=True)
class ActivationManifestPacketPaths:
    approved_contract: Path
    contract_errata: Path
    authority: AuthorityPacketPaths
    sources: SourcePacketPaths
    structured: StructuredPacketPaths
    corpus: CorpusPacketPaths
    provider: ProviderPacketPaths
    human_benchmark_definition: Path


@dataclass(frozen=True, slots=True)
class WrittenPacketIdentity:
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class _FileObservation:
    raw_file: RawFileIdentity
    content: bytes | None
    md5: str


@dataclass(frozen=True, slots=True)
class _LoadedArtifact[ModelT: BaseModel]:
    model: ModelT
    identity: TypedArtifactIdentity


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ActivationManifestPacketError(message)


def _validated_root(root: Path) -> Path:
    supplied = root.expanduser()
    try:
        root_stat = supplied.lstat()
    except OSError as exc:
        raise ActivationManifestPacketError("packet root is unavailable") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise ActivationManifestPacketError("packet root must be a real directory")
    return supplied.resolve(strict=True)


def _relative_input_path(root: Path, path: Path) -> str:
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ActivationManifestPacketError("packet input escapes its root") from exc
    try:
        return _validate_artifact_path(relative)
    except ValueError as exc:
        raise ActivationManifestPacketError("packet input path is invalid") from exc


def _open_regular_at(root_fd: int, relative_path: str) -> int:
    parts = PurePosixPath(relative_path).parts
    directory_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ActivationManifestPacketError(
            f"packet input is missing or symbolic: {relative_path}"
        ) from exc
    finally:
        os.close(directory_fd)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _observe_file(
    root: Path,
    path: Path,
    *,
    collect_content: bool,
    maximum_bytes: int = MAX_PACKET_INPUT_BYTES,
) -> _FileObservation:
    relative_path = _relative_input_path(root, path)
    root_fd = os.open(
        root,
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        file_fd = _open_regular_at(root_fd, relative_path)
    finally:
        os.close(root_fd)
    try:
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum_bytes
        ):
            raise ActivationManifestPacketError("packet input size or type is invalid")
        sha256 = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        content = bytearray() if collect_content else None
        observed_size = 0
        while chunk := os.read(file_fd, _READ_CHUNK_SIZE):
            observed_size += len(chunk)
            sha256.update(chunk)
            md5.update(chunk)
            if content is not None:
                content.extend(chunk)
        after = os.fstat(file_fd)
    finally:
        os.close(file_fd)
    if _stat_identity(before) != _stat_identity(after) or observed_size != after.st_size:
        raise ActivationManifestPacketError("packet input changed while being read")
    return _FileObservation(
        raw_file=RawFileIdentity(
            path=relative_path,
            byte_size=observed_size,
            file_sha256=sha256.hexdigest(),
        ),
        content=bytes(content) if content is not None else None,
        md5=md5.hexdigest(),
    )


def observe_raw_file(root: Path, path: Path) -> RawFileIdentity:
    """Return a no-symlink physical identity for one explicit packet input."""

    trusted_root = _validated_root(root)
    return _observe_file(trusted_root, path, collect_content=False).raw_file


def verify_raw_file_identity(root: Path, reference: RawFileIdentity) -> None:
    """Re-hash a raw reference and reject any path, byte-size, or digest drift."""

    trusted_root = _validated_root(root)
    observed = _observe_file(
        trusted_root,
        Path(reference.path),
        collect_content=False,
    ).raw_file
    if observed != reference:
        raise ActivationManifestPacketError("raw packet artifact identity drifted")


def _load_typed_artifact[ModelT: BaseModel](
    root: Path,
    path: Path,
    schema: type[ModelT],
    *,
    role: str,
    schema_version_field: str,
    digest_field: Literal[
        "manifest_sha256",
        "anchor_manifest_sha256",
        "definition_sha256",
        "report_sha256",
    ],
    require_canonical_bytes: bool = False,
) -> _LoadedArtifact[ModelT]:
    observation = _observe_file(root, path, collect_content=True)
    assert observation.content is not None
    try:
        model = schema.model_validate_json(observation.content, strict=True)
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ActivationManifestPacketError(
            f"packet artifact is not valid typed evidence: {role}"
        ) from exc
    if require_canonical_bytes and observation.content != (
        canonical_model_json(model) + "\n"
    ).encode("utf-8"):
        raise ActivationManifestPacketError(
            f"packet artifact is not canonical typed evidence: {role}"
        )
    schema_version = getattr(model, schema_version_field, None)
    semantic_sha256 = getattr(model, digest_field, None)
    if (
        not isinstance(schema_version, str)
        or not schema_version
        or not isinstance(semantic_sha256, str)
        or _SHA256_RE.fullmatch(semantic_sha256) is None
    ):
        raise ActivationManifestPacketError(
            f"packet artifact lacks its typed semantic identity: {role}"
        )
    return _LoadedArtifact(
        model=model,
        identity=TypedArtifactIdentity(
            raw_file=observation.raw_file,
            semantic=TypedSemanticIdentity(
                schema_version_field=schema_version_field,
                schema_version=schema_version,
                digest_field=digest_field,
                semantic_sha256=semantic_sha256,
            ),
        ),
    )


def _load_manifest[ModelT: BaseModel](
    root: Path,
    path: Path,
    schema: type[ModelT],
    *,
    role: str,
) -> _LoadedArtifact[ModelT]:
    return _load_typed_artifact(
        root,
        path,
        schema,
        role=role,
        schema_version_field="manifest_schema_version",
        digest_field="manifest_sha256",
    )


def _validate_contract_bytes(contract: bytes, errata: bytes) -> None:
    required_contract = (
        b"# V0 Activation and Publication Contract \xe2\x80\x94 Draft A",
        b"> Status: **APPROVED**",
        b"## 2. Approval model",
        b"**Activation Manifest Packet approval**",
    )
    required_errata = (
        b"# V0 Activation Contract factual errata",
        b"> Status: **PENDING ACTIVATION MANIFEST PACKET APPROVAL**",
        b"## E1 \xe2\x80\x94 MSL41 polinton order name",
        b"## E2 \xe2\x80\x94 ICTV files have no publisher checksum",
        b"`upstream_checksum_verified=false`",
    )
    _require(
        all(fragment in contract for fragment in required_contract),
        "approved activation contract content is incomplete",
    )
    _require(
        all(fragment in errata for fragment in required_errata),
        "activation contract errata E1/E2 content is incomplete",
    )


def _validate_structured_graph(
    *,
    ncbi_artifact: NcbiTaxonomyArtifactManifest,
    ncbi_snapshot: TaxonomySnapshotManifest,
    assignments: AssemblyTaxonAssignmentManifest,
    ictv_artifact: IctvArtifactManifest,
    ictv_snapshot: TaxonomySnapshotManifest,
    mapping: StudyFormalMappingManifest,
    cohort: AdjudicationCohortManifest,
    sequence_bundle: FullSequenceBundleManifest,
    request_plan: FlankEvidenceRequestPlan,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
    activation: StructuredActivationManifest,
) -> StructuredPacketSummary:
    _validate_structured_record_graph(
        ncbi_snapshot=ncbi_snapshot,
        ictv_snapshot=ictv_snapshot,
        mapping=mapping,
        cohort=cohort,
        sequence_bundle=sequence_bundle,
        request_plan=request_plan,
        flanks=flanks,
        inclusions=inclusions,
        adjudication=adjudication,
        public_loci=public_loci,
        public_assertions=public_assertions,
        activation=activation,
    )
    expected_pairs = (
        (activation.ncbi_artifact_manifest_sha256, ncbi_artifact.manifest_sha256),
        (activation.ncbi_snapshot_manifest_sha256, ncbi_snapshot.manifest_sha256),
        (
            activation.assembly_taxon_assignment_manifest_sha256,
            assignments.manifest_sha256,
        ),
        (activation.ictv_artifact_manifest_sha256, ictv_artifact.manifest_sha256),
        (activation.ictv_snapshot_manifest_sha256, ictv_snapshot.manifest_sha256),
        (activation.study_formal_mapping_manifest_sha256, mapping.manifest_sha256),
        (activation.cohort_manifest_sha256, cohort.manifest_sha256),
        (
            activation.full_sequence_bundle_manifest_sha256,
            sequence_bundle.manifest_sha256,
        ),
        (activation.flank_request_plan_manifest_sha256, request_plan.manifest_sha256),
        (activation.flank_manifest_sha256, flanks.manifest_sha256),
        (activation.inclusion_manifest_sha256, inclusions.manifest_sha256),
        (activation.adjudication_manifest_sha256, adjudication.manifest_sha256),
        (
            activation.public_locus_membership_manifest_sha256,
            public_loci.manifest_sha256,
        ),
        (
            activation.public_assertion_membership_manifest_sha256,
            public_assertions.manifest_sha256,
        ),
        (activation.source_manifest_sha256, cohort.source_manifest_sha256),
        (activation.source_audit_sha256, cohort.source_audit_sha256),
        (ncbi_snapshot.artifact_manifest_sha256, ncbi_artifact.manifest_sha256),
        (ictv_snapshot.artifact_manifest_sha256, ictv_artifact.manifest_sha256),
        (assignments.ncbi_snapshot_manifest_sha256, ncbi_snapshot.manifest_sha256),
        (mapping.formal_snapshot_manifest_sha256, ictv_snapshot.manifest_sha256),
        (mapping.formal_snapshot_key, ictv_snapshot.snapshot_key),
        (request_plan.cohort_manifest_sha256, cohort.manifest_sha256),
        (flanks.cohort_manifest_sha256, cohort.manifest_sha256),
        (flanks.request_plan_manifest_sha256, request_plan.manifest_sha256),
        (inclusions.cohort_manifest_sha256, cohort.manifest_sha256),
        (inclusions.flank_manifest_sha256, flanks.manifest_sha256),
        (adjudication.cohort_manifest_sha256, cohort.manifest_sha256),
        (adjudication.flank_manifest_sha256, flanks.manifest_sha256),
        (adjudication.inclusion_manifest_sha256, inclusions.manifest_sha256),
        (public_loci.adjudication_manifest_sha256, adjudication.manifest_sha256),
        (
            public_assertions.locus_membership_manifest_sha256,
            public_loci.manifest_sha256,
        ),
    )
    _require(
        all(observed == expected for observed, expected in expected_pairs),
        "structured packet artifacts do not form one semantic graph",
    )

    decisions = {row.decision_sha256: row for row in inclusions.decisions}
    _require(
        {row.decision_sha256 for row in adjudication.selections} == set(decisions),
        "structured adjudication does not cover the exact decision set",
    )
    included_loci = {row.locus_key for row in inclusions.decisions if row.decision == "include"}
    public_locus_keys = {row.locus_key for row in public_loci.memberships}
    _require(
        included_loci == public_locus_keys,
        "public loci do not equal all include decisions",
    )
    assertions_by_locus: dict[str, set[str]] = defaultdict(set)
    for assertion in public_assertions.memberships:
        _require(
            assertion.locus_key in public_locus_keys,
            "public assertion targets a non-public locus",
        )
        assertions_by_locus[assertion.locus_key].add(assertion.assertion_type)
    expected_assertion_types = {"hcvr", "viral_major_taxon", "vr_type"}
    _require(
        set(assertions_by_locus) == public_locus_keys
        and all(value == expected_assertion_types for value in assertions_by_locus.values()),
        "every public locus must retain exactly the three V0 assertion types",
    )

    counts = activation.counts
    _require(
        counts.adjudicated_records == len(adjudication.selections)
        and counts.included_loci == len(included_loci)
        and counts.public_locus_memberships == public_loci.membership_count
        and counts.public_assertion_memberships == public_assertions.membership_count,
        "structured activation terminal counts differ from component manifests",
    )
    _require(
        all(row.terminal_status == "passing_locus_found" for row in adjudication.assembly_outcomes),
        "one or more approved assemblies has no passing locus",
    )
    mapping_rows = mapping.mappings
    _require(
        len(mapping_rows) == 1
        and mapping_rows[0].study_term_key == STUDY_ORTHOPOLINTOVIRALES_TERM_KEY
        and mapping_rows[0].formal_term_key == FORMAL_AMPHINTOVIRALES_TERM_KEY
        and mapping_rows[0].relation == "renamed_to"
        and mapping_rows[0].evidence_artifact_sha256 == _PROPOSAL_CAPTURE_SHA256,
        "study-to-formal mapping does not implement erratum E1 exactly",
    )
    _require(
        any(term.term_key == FORMAL_AMPHINTOVIRALES_TERM_KEY for term in ictv_snapshot.terms),
        "formal MSL41 snapshot lacks the exact Amphintovirales endpoint",
    )
    _require(
        ictv_artifact.msl.upstream_checksum is None
        and ictv_artifact.msl.upstream_checksum_algorithm is None
        and ictv_artifact.msl.checksum_source_uri is None
        and not ictv_artifact.msl.upstream_checksum_verified
        and ictv_artifact.corrected_vmr.upstream_checksum is None
        and ictv_artifact.corrected_vmr.upstream_checksum_algorithm is None
        and ictv_artifact.corrected_vmr.checksum_source_uri is None
        and not ictv_artifact.corrected_vmr.upstream_checksum_verified,
        "ICTV workbooks must not claim nonexistent publisher checksums",
    )

    decision_counts = Counter(row.decision for row in inclusions.decisions)
    primary_count = sum(row.selection_tier == "primary" for row in adjudication.selections)
    expansion_count = sum(row.selection_tier == "expansion" for row in adjudication.selections)
    _require(primary_count == 71, "all 71 source_high rows must remain assessed")
    return StructuredPacketSummary(
        primary_assessed_count=71,
        expansion_assessed_count=expansion_count,
        source_low_invoked=expansion_count > 0,
        include_count=decision_counts["include"],
        quarantine_decision_count=decision_counts["quarantine"],
        review_decision_count=decision_counts["review"],
        exclude_decision_count=decision_counts["exclude"],
        public_locus_count=public_loci.membership_count,
        public_assertion_count=public_assertions.membership_count,
        ncbi_term_count=len(ncbi_snapshot.terms),
        ictv_term_count=len(ictv_snapshot.terms),
        assembly_assignment_count=10,
        study_formal_mapping_count=1,
        family_mapping_count=0,
        mapping_relation="renamed_to",
        all_ten_assemblies_passing=True,
    )


def _validate_structured_record_graph(
    *,
    ncbi_snapshot: TaxonomySnapshotManifest,
    ictv_snapshot: TaxonomySnapshotManifest,
    mapping: StudyFormalMappingManifest,
    cohort: AdjudicationCohortManifest,
    sequence_bundle: FullSequenceBundleManifest,
    request_plan: FlankEvidenceRequestPlan,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
    activation: StructuredActivationManifest,
) -> None:
    """Replay the deterministic structured projections behind the manifest DAG.

    A valid self-digest proves only that one file is internally sealed.  This
    replay proves that its records are the exact deterministic projection of the
    preceding file, so a caller cannot reseal a mutually referenced but
    scientifically different graph.
    """

    manifests_with_release_key = (
        cohort,
        request_plan,
        flanks,
        inclusions,
        adjudication,
        public_loci,
        public_assertions,
        activation,
    )
    _require(
        all(row.release_key == ACTIVATION_RELEASE_KEY for row in manifests_with_release_key),
        "structured record graph crosses release keys",
    )

    cohort_records = (
        *cohort.primary_records,
        *(record for queue in cohort.expansion_queues for record in queue.records),
    )
    cohort_by_locus = {record.locus_key: record for record in cohort_records}
    _require(
        len(cohort_by_locus) == len(cohort_records),
        "structured cohort locus identities are not unique",
    )

    try:
        selected_records = tuple(
            cohort_by_locus[request.locus_key] for request in request_plan.requests
        )
        replayed_request_plan = build_flank_request_plan(cohort, selected_records)
        _require(
            replayed_request_plan == request_plan,
            "flank request plan is not the exact deterministic cohort projection",
        )

        bundle_by_accession = {
            record.accession_version: record for record in sequence_bundle.records
        }
        _require(
            all(
                request.sequence_accession_version in bundle_by_accession
                and bundle_by_accession[request.sequence_accession_version].sequence_length
                == request.sequence_length
                for request in request_plan.requests
            ),
            "flank request plan is not covered by the exact full-sequence bundle",
        )

        replayed_flanks = build_flank_evidence_manifest(
            cohort,
            request_plan,
            flanks.records,
        )
        _require(
            replayed_flanks == flanks,
            "flank evidence manifest is not the exact request-plan projection",
        )
        request_by_sha = {request.request_sha256: request for request in request_plan.requests}
        _require(
            len(request_by_sha) == len(request_plan.requests),
            "flank request identities are not unique",
        )
        for record in flanks.records:
            request = request_by_sha[record.request_sha256]
            _require(
                (
                    record.source_record_key,
                    record.source_row,
                    record.locus_key,
                    record.interval_key,
                    record.placement_key,
                    record.interval_basis,
                )
                == (
                    request.source_record_key,
                    request.source_row,
                    request.locus_key,
                    request.interval_key,
                    request.placement_key,
                    request.interval_basis,
                ),
                "flank evidence record does not exactly identify its request",
            )
        _require(
            all(record.source_uri == sequence_bundle.source_uri for record in flanks.records),
            "flank evidence source differs from the frozen sequence bundle",
        )

        flank_by_locus = {record.locus_key: record for record in flanks.records}
        _require(
            len(flank_by_locus) == len(flanks.records),
            "flank evidence locus identities are not unique",
        )
        evaluations = tuple(
            InclusionEvaluationInput(
                record=cohort_by_locus[decision.locus_key],
                flank=flank_by_locus[decision.locus_key],
                dependencies=DependencyBindings(
                    ncbi_snapshot_manifest_sha256=(decision.ncbi_snapshot_manifest_sha256),
                    ictv_snapshot_manifest_sha256=(decision.ictv_snapshot_manifest_sha256),
                    mapping_manifest_sha256=decision.mapping_manifest_sha256,
                ),
                m1_gates_pass=decision.m1_gates_pass,
                exact_placement_count=decision.exact_placement_count,
                import_outcome=decision.import_outcome,
                unresolved_issue_codes=decision.unresolved_issue_codes,
                quarantine_issue_codes=decision.quarantine_issue_codes,
                conflict_codes=decision.conflict_codes,
            )
            for decision in inclusions.decisions
        )
        replayed_inclusions = build_inclusion_manifest(
            cohort,
            flanks,
            evaluations,
        )
        _require(
            replayed_inclusions == inclusions,
            "inclusion decisions are not the exact cohort/flank policy projection",
        )
        _require(
            all(
                decision.ncbi_snapshot_manifest_sha256 == ncbi_snapshot.manifest_sha256
                and decision.ictv_snapshot_manifest_sha256 == ictv_snapshot.manifest_sha256
                and decision.mapping_manifest_sha256 == mapping.manifest_sha256
                for decision in inclusions.decisions
            ),
            "inclusion decisions do not bind the packet taxonomy dependencies",
        )

        replayed_adjudication = build_adjudication_manifest(
            cohort,
            flanks,
            inclusions,
        )
        _require(
            replayed_adjudication == adjudication,
            "structured adjudication is not the exact inclusion-policy selection",
        )
        replayed_public_loci = build_public_locus_membership_manifest(
            cohort,
            flanks,
            inclusions,
            adjudication,
        )
        _require(
            replayed_public_loci == public_loci,
            "public loci are not the exact adjudicated include projection",
        )
        replayed_public_assertions = build_public_assertion_membership_manifest(
            public_loci,
            public_assertions.memberships,
        )
        _require(
            replayed_public_assertions == public_assertions,
            "public assertions are not the exact public-locus projection",
        )
    except (LookupError, ValueError) as exc:
        raise ActivationManifestPacketError(
            "structured record graph deterministic replay failed"
        ) from exc

    predicate_by_type = {
        "hcvr": "source:hcvr",
        "viral_major_taxon": "source:viral-major-taxon",
        "vr_type": "source:vr-type",
    }
    _require(
        all(
            assertion.predicate_key == predicate_by_type[assertion.assertion_type]
            for assertion in public_assertions.memberships
        ),
        "public assertion predicates differ from their V0 assertion types",
    )


def _validate_frozen_sources(
    *,
    sources: FrozenSourceEvidence,
    ncbi_artifact: NcbiTaxonomyArtifactManifest,
    ictv_artifact: IctvArtifactManifest,
    sequence_bundle: FullSequenceBundleManifest,
    activation: StructuredActivationManifest,
    taxdump_md5: str,
    checksum_content: bytes,
    excluded_observations: tuple[_FileObservation, _FileObservation],
) -> None:
    _require(
        sources.m1_source_manifest.file_sha256 == activation.source_manifest_sha256
        and sources.m1_source_audit.file_sha256 == activation.source_audit_sha256,
        "M1 source manifest or audit differs from structured activation",
    )
    _require(
        sources.ncbi_taxdump_archive.file_sha256 == ncbi_artifact.archive.sha256
        and sources.ncbi_taxdump_archive.byte_size == ncbi_artifact.archive.byte_size,
        "NCBI taxdump raw identity differs from its artifact manifest",
    )
    _require(
        sources.ictv_msl_workbook.file_sha256 == ictv_artifact.msl.sha256
        and sources.ictv_msl_workbook.byte_size == ictv_artifact.msl.byte_size
        and sources.ictv_vmr_workbook.file_sha256 == ictv_artifact.corrected_vmr.sha256
        and sources.ictv_vmr_workbook.byte_size == ictv_artifact.corrected_vmr.byte_size,
        "ICTV workbook raw identity differs from its artifact manifest",
    )
    _require(
        sources.full_sequence_bundle.file_sha256 == sequence_bundle.artifact_sha256
        and sources.full_sequence_bundle.byte_size == sequence_bundle.artifact_byte_size,
        "full-sequence raw identity differs from its typed bundle manifest",
    )
    try:
        publisher_md5 = checksum_content.decode("ascii").strip().split()[0].lower()
    except (UnicodeError, IndexError) as exc:
        raise ActivationManifestPacketError("NCBI checksum capture is invalid") from exc
    _require(
        _MD5_RE.fullmatch(publisher_md5) is not None
        and publisher_md5 == ncbi_artifact.archive.upstream_checksum
        and publisher_md5 == taxdump_md5,
        "NCBI archive does not match the publisher-provided MD5",
    )
    for evidence, observation in zip(
        sources.excluded_taxdump_candidates,
        excluded_observations,
        strict=True,
    ):
        observed_reasons: list[str] = []
        if observation.md5 != publisher_md5:
            observed_reasons.append("publisher_md5_mismatch")
        if observation.raw_file.byte_size != sources.ncbi_taxdump_archive.byte_size:
            observed_reasons.append("retrieved_byte_size_mismatch")
        _require(
            observation.raw_file == evidence.raw_file
            and tuple(observed_reasons) == evidence.reason_codes,
            "excluded NCBI taxdump rejection reasons do not match observed bytes",
        )


def _qualification_file_identity(reference: RawFileIdentity) -> QualificationFileIdentity:
    return QualificationFileIdentity(
        relative_path=reference.path,
        byte_size=reference.byte_size,
        sha256=reference.file_sha256,
    )


def _validate_qualification_report_binding(
    *,
    definition: ProviderQualificationDefinition,
    definition_file: RawFileIdentity,
    report: ProviderQualificationReport,
) -> None:
    """Require one report to be the exact passing projection of the packet definition."""

    try:
        expected_definition_file = _qualification_file_identity(definition_file)
        _require(
            report.definition_file == expected_definition_file,
            "provider qualification report does not bind the packet definition bytes",
        )
        verify_provider_qualification_report(
            report,
            definition=definition,
            definition_file=expected_definition_file,
        )
    except (ProviderQualificationError, ValidationError, ValueError) as exc:
        raise ActivationManifestPacketError(
            "provider qualification report does not replay against the packet definition"
        ) from exc


def _validate_provider_qualification_graph(
    *,
    project_root: Path,
    environment: ProviderEnvironmentManifest,
    environment_file: RawFileIdentity,
    model: LocalModelPolicyManifest,
    model_file: RawFileIdentity,
    prompt: PromptPolicyManifest,
    prompt_file: RawFileIdentity,
    qualification_runner: RawFileIdentity,
    qualification_module: RawFileIdentity,
    qualification_definition: ProviderQualificationDefinition,
    qualification_definition_file: RawFileIdentity,
    qualification_report: ProviderQualificationReport,
) -> None:
    """Replay the pre-registered fixed provider qualification against packet bytes."""

    try:
        environment_binding = ProviderEnvironmentManifestBinding(
            manifest_schema_version=environment.manifest_schema_version,
            identity_schema_version=environment.identity_schema_version,
            manifest_sha256=environment.manifest_sha256,
            provider_environment_sha256=environment.provider_environment_sha256,
            provider_environment_distribution_count=(
                environment.provider_environment_distribution_count
            ),
            provider_environment_file_count=environment.provider_environment_file_count,
        )
        trusted_definition = verify_provider_qualification_definition(
            qualification_definition,
            model_policy=model,
            prompt_policy=prompt,
            environment_manifest=environment_binding,
            model_policy_file=_qualification_file_identity(model_file),
            prompt_policy_file=_qualification_file_identity(prompt_file),
            environment_manifest_file=_qualification_file_identity(environment_file),
            runner_file=_qualification_file_identity(qualification_runner),
            qualification_module_file=_qualification_file_identity(qualification_module),
            project_root=project_root,
        )
    except (ProviderQualificationError, ValidationError, ValueError) as exc:
        raise ActivationManifestPacketError(
            "provider qualification definition does not replay against packet policy bytes"
        ) from exc

    candidate = trusted_definition.candidate_set[0]
    _require(
        candidate.model_policy_manifest_sha256 == model.manifest_sha256
        and candidate.prompt_policy_manifest_sha256 == prompt.manifest_sha256
        and candidate.provider_environment_manifest_sha256 == environment.manifest_sha256
        and candidate.provider_environment_sha256 == environment.provider_environment_sha256
        and candidate.model_policy_file == _qualification_file_identity(model_file)
        and candidate.prompt_policy_file == _qualification_file_identity(prompt_file)
        and candidate.provider_environment_manifest_file
        == _qualification_file_identity(environment_file),
        "provider qualification candidate differs from packet model/prompt/environment",
    )
    _validate_qualification_report_binding(
        definition=trusted_definition,
        definition_file=qualification_definition_file,
        report=qualification_report,
    )
    observation = qualification_report.observation
    _require(
        len(trusted_definition.candidate_set) == 1
        and qualification_report.candidate_status == "passed"
        and qualification_report.selection == "only_passing_candidate"
        and observation.generation_request_count == 1
        and observation.retry_count == 0
        and observation.hmac_runtime_attestation_verified
        and observation.inner_unauthenticated_status == 401
        and observation.clean_shutdown,
        "provider qualification report does not contain the exact passing result",
    )


def _validate_corpus_provider_benchmark_graph(
    *,
    project_root: Path,
    structured: StructuredActivationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
    ncbi_snapshot: TaxonomySnapshotManifest,
    assignments: AssemblyTaxonAssignmentManifest,
    mapping: StudyFormalMappingManifest,
    corpus: CorpusManifest,
    anchors: CorpusAnchorManifest,
    corpus_receipt: CorpusValidationExport,
    binding: HybridReleaseBindingManifest,
    environment: ProviderEnvironmentManifest,
    environment_verifier: RawFileIdentity,
    environment_file: RawFileIdentity,
    model: LocalModelPolicyManifest,
    model_file: RawFileIdentity,
    prompt: PromptPolicyManifest,
    prompt_file: RawFileIdentity,
    qualification_runner: RawFileIdentity,
    qualification_module: RawFileIdentity,
    qualification_definition: ProviderQualificationDefinition,
    qualification_definition_file: RawFileIdentity,
    qualification_report: ProviderQualificationReport,
    definition: HumanBenchmarkDefinition,
) -> tuple[CorpusPacketSummary, ProviderPacketSummary, BenchmarkPacketSummary]:
    receipt_evidence = corpus_receipt.receipt.validation_report
    _require(
        corpus.corpus_release_key == V0_CORPUS_RELEASE_KEY
        and anchors.corpus_release_key == corpus.corpus_release_key
        and anchors.corpus_manifest_sha256 == corpus.manifest_sha256
        and corpus_receipt.corpus_release.corpus_release_key == corpus.corpus_release_key
        and corpus_receipt.corpus_release.manifest_sha256 == corpus.manifest_sha256
        and corpus_receipt.corpus_release.status == "validated"
        and corpus_receipt.receipt.status == "passed"
        and corpus_receipt.receipt.trusted
        and receipt_evidence.anchor_manifest_sha256 == anchors.anchor_manifest_sha256,
        "corpus, anchors, and validated trusted receipt do not form one graph",
    )
    _require(
        len(binding.bindings) == 1
        and binding.bindings[0].release_key == structured.release_key
        and binding.bindings[0].release_manifest_sha256 == structured.manifest_sha256
        and binding.bindings[0].corpus_release_key == corpus.corpus_release_key
        and binding.bindings[0].corpus_manifest_sha256 == corpus.manifest_sha256,
        "hybrid binding is not the one exact structured/corpus pair",
    )

    environment_versions = {row.canonical_name: row.version for row in environment.distributions}
    _require(
        model.provider_environment_verifier_sha256 == environment_verifier.file_sha256
        and model.provider_environment_manifest_sha256 == environment.manifest_sha256
        and model.provider_environment_sha256 == environment.provider_environment_sha256
        and model.provider_environment_distribution_count
        == environment.provider_environment_distribution_count
        and model.provider_environment_file_count == environment.provider_environment_file_count
        and all(
            environment_versions.get(name) == version
            for name, version in model.runtime_distributions.items()
        ),
        "model policy is not bound to the exact verified provider environment",
    )
    _require(
        model.prompt_policy_manifest_sha256 == prompt.manifest_sha256,
        "local model policy is not bound to the prompt policy",
    )
    prompt.require_approved_v0_policy()
    _validate_provider_qualification_graph(
        project_root=project_root,
        environment=environment,
        environment_file=environment_file,
        model=model,
        model_file=model_file,
        prompt=prompt,
        prompt_file=prompt_file,
        qualification_runner=qualification_runner,
        qualification_module=qualification_module,
        qualification_definition=qualification_definition,
        qualification_definition_file=qualification_definition_file,
        qualification_report=qualification_report,
    )
    try:
        rebuilt_definition = build_human_benchmark_definition(
            structured_manifest=structured,
            public_locus_manifest=public_loci,
            public_assertion_manifest=public_assertions,
            ncbi_snapshot_manifest=ncbi_snapshot,
            assembly_assignment_manifest=assignments,
            study_formal_mapping_manifest=mapping,
            corpus_manifest=corpus,
            anchor_manifest=anchors,
            binding_manifest=binding,
            model_policy_manifest=model,
            prompt_policy_manifest=prompt,
        )
    except Exception as exc:
        raise ActivationManifestPacketError(
            "human benchmark definition inputs do not form one candidate graph"
        ) from exc
    _require(
        rebuilt_definition == definition,
        "human benchmark definition does not reproduce from packet artifacts",
    )

    structured_anchor_count = sum(
        row.curation_method == V0_STRUCTURED_ANCHOR_CURATION_METHOD for row in anchors.anchors
    )
    matched_target_count = sum(len(row.expected_matched_targets) for row in definition.cases)
    unmatched_target_count = sum(len(row.expected_unmatched_targets) for row in definition.cases)
    _require(
        corpus.document_count == 11
        and structured_anchor_count == 8
        and matched_target_count == 10
        and unmatched_target_count == 30,
        "corpus anchors or preregistered benchmark target counts drifted",
    )
    return (
        CorpusPacketSummary(
            document_count=11,
            anchor_count=anchors.anchor_count,
            structured_lineage_anchor_count=8,
            corpus_release_status="validated",
            receipt_status="passed",
            receipt_trusted=True,
            published_status_claimed=False,
        ),
        ProviderPacketSummary(
            provider_key=model.provider_key,
            model_key=model.model_key,
            model_revision=model.model_revision,
            environment_distribution_count=environment.provider_environment_distribution_count,
            environment_file_count=environment.provider_environment_file_count,
            network_policy_key=model.network_policy_key,
            qualification_candidate_count=1,
            qualification_status=qualification_report.candidate_status,
            qualification_selection=qualification_report.selection,
            qualification_request_count=1,
            qualification_retry_count=0,
            qualification_hmac_attestation_verified=True,
            qualification_inner_unauthenticated_status=401,
            qualification_clean_shutdown=True,
            external_provider_authorized=False,
        ),
        BenchmarkPacketSummary(
            case_count=10,
            expected_matched_target_count=10,
            expected_unmatched_target_count=30,
            assembly_count=10,
            human_semantic_verdict_included=False,
        ),
    )


def _authority_capture(
    capture_key: Literal[
        "authority-capture:ncbi-usage-policy:20260829",
        "authority-capture:ictv-taxonomy-cc-by-4.0:20260829",
        "authority-capture:ictv-proposal-2024.010D",
    ],
    raw_file: RawFileIdentity,
) -> AuthorityCapture:
    source_uri, retrieved_at, _sha256, _size, media_type = _AUTHORITY_SPECS[capture_key]
    return AuthorityCapture(
        capture_key=capture_key,
        source_uri=source_uri,
        retrieved_at=retrieved_at,
        media_type=media_type,
        raw_file=raw_file,
    )


def build_activation_manifest_packet(
    root: Path,
    paths: ActivationManifestPacketPaths,
) -> V0ActivationManifestPacket:
    """Load, cross-validate, and seal one Checkpoint 2 candidate without side effects."""

    root = _validated_root(root)
    contract = _observe_file(root, paths.approved_contract, collect_content=True)
    errata = _observe_file(root, paths.contract_errata, collect_content=True)
    assert contract.content is not None and errata.content is not None
    _validate_contract_bytes(contract.content, errata.content)

    ncbi_capture = _observe_file(
        root, paths.authority.ncbi_usage_policy_capture, collect_content=False
    )
    ictv_capture = _observe_file(
        root, paths.authority.ictv_usage_policy_capture, collect_content=False
    )
    proposal_capture = _observe_file(
        root, paths.authority.ictv_proposal_capture, collect_content=False
    )
    authority_captures = (
        _authority_capture(
            "authority-capture:ncbi-usage-policy:20260829",
            ncbi_capture.raw_file,
        ),
        _authority_capture(
            "authority-capture:ictv-taxonomy-cc-by-4.0:20260829",
            ictv_capture.raw_file,
        ),
        _authority_capture(
            "authority-capture:ictv-proposal-2024.010D",
            proposal_capture.raw_file,
        ),
    )

    m1_manifest = _observe_file(root, paths.sources.m1_source_manifest, collect_content=False)
    m1_audit = _observe_file(root, paths.sources.m1_source_audit, collect_content=False)
    taxdump = _observe_file(root, paths.sources.ncbi_taxdump_archive, collect_content=False)
    taxdump_checksum = _observe_file(
        root, paths.sources.ncbi_taxdump_checksum, collect_content=True
    )
    ictv_msl = _observe_file(root, paths.sources.ictv_msl_workbook, collect_content=False)
    ictv_vmr = _observe_file(root, paths.sources.ictv_vmr_workbook, collect_content=False)
    full_sequence = _observe_file(root, paths.sources.full_sequence_bundle, collect_content=False)
    excluded_md5 = _observe_file(root, paths.sources.excluded_taxdump_md5, collect_content=False)
    excluded_size = _observe_file(root, paths.sources.excluded_taxdump_size, collect_content=False)
    frozen_sources = FrozenSourceEvidence(
        m1_source_manifest=m1_manifest.raw_file,
        m1_source_audit=m1_audit.raw_file,
        ncbi_taxdump_archive=taxdump.raw_file,
        ncbi_taxdump_checksum=taxdump_checksum.raw_file,
        ictv_msl_workbook=ictv_msl.raw_file,
        ictv_vmr_workbook=ictv_vmr.raw_file,
        full_sequence_bundle=full_sequence.raw_file,
        excluded_taxdump_candidates=(
            ExcludedSourceArtifact(
                reason_codes=(
                    "publisher_md5_mismatch",
                    "retrieved_byte_size_mismatch",
                ),
                raw_file=excluded_md5.raw_file,
                used_by_candidate=False,
            ),
            ExcludedSourceArtifact(
                reason_codes=(
                    "publisher_md5_mismatch",
                    "retrieved_byte_size_mismatch",
                ),
                raw_file=excluded_size.raw_file,
                used_by_candidate=False,
            ),
        ),
    )

    structured_paths = paths.structured
    ncbi_artifact = _load_manifest(
        root,
        structured_paths.ncbi_artifact_manifest,
        NcbiTaxonomyArtifactManifest,
        role="ncbi_artifact_manifest",
    )
    ncbi_snapshot = _load_manifest(
        root,
        structured_paths.ncbi_snapshot_manifest,
        TaxonomySnapshotManifest,
        role="ncbi_snapshot_manifest",
    )
    assignments = _load_manifest(
        root,
        structured_paths.assembly_taxon_assignment_manifest,
        AssemblyTaxonAssignmentManifest,
        role="assembly_taxon_assignment_manifest",
    )
    ictv_artifact = _load_manifest(
        root,
        structured_paths.ictv_artifact_manifest,
        IctvArtifactManifest,
        role="ictv_artifact_manifest",
    )
    ictv_snapshot = _load_manifest(
        root,
        structured_paths.ictv_snapshot_manifest,
        TaxonomySnapshotManifest,
        role="ictv_snapshot_manifest",
    )
    mapping = _load_manifest(
        root,
        structured_paths.study_formal_mapping_manifest,
        StudyFormalMappingManifest,
        role="study_formal_mapping_manifest",
    )
    cohort = _load_manifest(
        root,
        structured_paths.adjudication_cohort_manifest,
        AdjudicationCohortManifest,
        role="adjudication_cohort_manifest",
    )
    sequence_bundle = _load_manifest(
        root,
        structured_paths.full_sequence_bundle_manifest,
        FullSequenceBundleManifest,
        role="full_sequence_bundle_manifest",
    )
    request_plan = _load_manifest(
        root,
        structured_paths.flank_request_plan_manifest,
        FlankEvidenceRequestPlan,
        role="flank_request_plan_manifest",
    )
    flanks = _load_manifest(
        root,
        structured_paths.flank_evidence_manifest,
        FlankEvidenceManifest,
        role="flank_evidence_manifest",
    )
    inclusions = _load_manifest(
        root,
        structured_paths.inclusion_decision_manifest,
        InclusionDecisionManifest,
        role="inclusion_decision_manifest",
    )
    adjudication = _load_manifest(
        root,
        structured_paths.structured_adjudication_manifest,
        StructuredAdjudicationManifest,
        role="structured_adjudication_manifest",
    )
    public_loci = _load_manifest(
        root,
        structured_paths.public_locus_membership_manifest,
        PublicLocusMembershipManifest,
        role="public_locus_membership_manifest",
    )
    public_assertions = _load_manifest(
        root,
        structured_paths.public_assertion_membership_manifest,
        PublicAssertionMembershipManifest,
        role="public_assertion_membership_manifest",
    )
    activation = _load_manifest(
        root,
        structured_paths.structured_activation_manifest,
        StructuredActivationManifest,
        role="structured_activation_manifest",
    )
    structured_summary = _validate_structured_graph(
        ncbi_artifact=ncbi_artifact.model,
        ncbi_snapshot=ncbi_snapshot.model,
        assignments=assignments.model,
        ictv_artifact=ictv_artifact.model,
        ictv_snapshot=ictv_snapshot.model,
        mapping=mapping.model,
        cohort=cohort.model,
        sequence_bundle=sequence_bundle.model,
        request_plan=request_plan.model,
        flanks=flanks.model,
        inclusions=inclusions.model,
        adjudication=adjudication.model,
        public_loci=public_loci.model,
        public_assertions=public_assertions.model,
        activation=activation.model,
    )
    assert taxdump_checksum.content is not None
    _validate_frozen_sources(
        sources=frozen_sources,
        ncbi_artifact=ncbi_artifact.model,
        ictv_artifact=ictv_artifact.model,
        sequence_bundle=sequence_bundle.model,
        activation=activation.model,
        taxdump_md5=taxdump.md5,
        checksum_content=taxdump_checksum.content,
        excluded_observations=(excluded_md5, excluded_size),
    )
    _require(
        ncbi_artifact.model.usage_policy.local_capture_sha256 == ncbi_capture.raw_file.file_sha256
        and ictv_artifact.model.usage_policy.local_capture_sha256
        == ictv_capture.raw_file.file_sha256,
        "taxonomy usage-policy manifests differ from authority captures",
    )

    corpus_paths = paths.corpus
    corpus = _load_manifest(
        root, corpus_paths.corpus_manifest, CorpusManifest, role="corpus_manifest"
    )
    anchors = _load_typed_artifact(
        root,
        corpus_paths.anchor_manifest,
        CorpusAnchorManifest,
        role="anchor_manifest",
        schema_version_field="anchor_manifest_schema_version",
        digest_field="anchor_manifest_sha256",
    )
    corpus_receipt = _load_typed_artifact(
        root,
        corpus_paths.corpus_validation_receipt,
        CorpusValidationExport,
        role="corpus_validation_receipt",
        schema_version_field="export_schema_version",
        digest_field="manifest_sha256",
    )
    binding = _load_typed_artifact(
        root,
        corpus_paths.hybrid_binding_manifest,
        HybridReleaseBindingManifest,
        role="hybrid_binding_manifest",
        schema_version_field="binding_schema_version",
        digest_field="manifest_sha256",
    )

    provider_paths = paths.provider
    environment_verifier = _observe_file(
        root, provider_paths.provider_environment_verifier, collect_content=False
    )
    environment = _load_manifest(
        root,
        provider_paths.provider_environment_manifest,
        ProviderEnvironmentManifest,
        role="provider_environment_manifest",
    )
    model = _load_manifest(
        root,
        provider_paths.local_model_policy_manifest,
        LocalModelPolicyManifest,
        role="local_model_policy_manifest",
    )
    prompt = _load_manifest(
        root,
        provider_paths.prompt_policy_manifest,
        PromptPolicyManifest,
        role="prompt_policy_manifest",
    )
    qualification_runner = _observe_file(
        root,
        provider_paths.provider_qualification_runner,
        collect_content=False,
    )
    qualification_module = _observe_file(
        root,
        provider_paths.provider_qualification_module,
        collect_content=False,
    )
    qualification_definition = _load_typed_artifact(
        root,
        provider_paths.provider_qualification_definition,
        ProviderQualificationDefinition,
        role="provider_qualification_definition",
        schema_version_field="definition_schema_version",
        digest_field="definition_sha256",
        require_canonical_bytes=True,
    )
    qualification_report = _load_typed_artifact(
        root,
        provider_paths.provider_qualification_report,
        ProviderQualificationReport,
        role="provider_qualification_report",
        schema_version_field="report_schema_version",
        digest_field="report_sha256",
        require_canonical_bytes=True,
    )
    definition = _load_typed_artifact(
        root,
        paths.human_benchmark_definition,
        HumanBenchmarkDefinition,
        role="human_benchmark_definition",
        schema_version_field="definition_schema_version",
        digest_field="definition_sha256",
    )
    corpus_summary, provider_summary, benchmark_summary = _validate_corpus_provider_benchmark_graph(
        project_root=root,
        structured=activation.model,
        public_loci=public_loci.model,
        public_assertions=public_assertions.model,
        ncbi_snapshot=ncbi_snapshot.model,
        assignments=assignments.model,
        mapping=mapping.model,
        corpus=corpus.model,
        anchors=anchors.model,
        corpus_receipt=corpus_receipt.model,
        binding=binding.model,
        environment=environment.model,
        environment_verifier=environment_verifier.raw_file,
        environment_file=environment.identity.raw_file,
        model=model.model,
        model_file=model.identity.raw_file,
        prompt=prompt.model,
        prompt_file=prompt.identity.raw_file,
        qualification_runner=qualification_runner.raw_file,
        qualification_module=qualification_module.raw_file,
        qualification_definition=qualification_definition.model,
        qualification_definition_file=qualification_definition.identity.raw_file,
        qualification_report=qualification_report.model,
        definition=definition.model,
    )

    payload: dict[str, object] = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "packet_key": PACKET_KEY,
        "checkpoint": 2,
        "status": "candidate_for_owner_approval",
        "product_version": "V0",
        "release_key": ACTIVATION_RELEASE_KEY,
        "corpus_release_key": V0_CORPUS_RELEASE_KEY,
        "contract": ContractEvidence(
            contract_name="V0 Activation and Publication Contract — Draft A",
            contract_status="approved",
            approved_on="2026-08-29",
            approved_contract=contract.raw_file,
            errata_status="pending_activation_manifest_packet_approval",
            errata_ids=("E1", "E2"),
            errata=errata.raw_file,
        ),
        "authority_captures": authority_captures,
        "frozen_sources": frozen_sources,
        "structured": StructuredPacketArtifacts(
            ncbi_artifact_manifest=ncbi_artifact.identity,
            ncbi_snapshot_manifest=ncbi_snapshot.identity,
            assembly_taxon_assignment_manifest=assignments.identity,
            ictv_artifact_manifest=ictv_artifact.identity,
            ictv_snapshot_manifest=ictv_snapshot.identity,
            study_formal_mapping_manifest=mapping.identity,
            adjudication_cohort_manifest=cohort.identity,
            full_sequence_bundle_manifest=sequence_bundle.identity,
            flank_request_plan_manifest=request_plan.identity,
            flank_evidence_manifest=flanks.identity,
            inclusion_decision_manifest=inclusions.identity,
            structured_adjudication_manifest=adjudication.identity,
            public_locus_membership_manifest=public_loci.identity,
            public_assertion_membership_manifest=public_assertions.identity,
            structured_activation_manifest=activation.identity,
        ),
        "corpus": CorpusPacketArtifacts(
            corpus_manifest=corpus.identity,
            anchor_manifest=anchors.identity,
            corpus_validation_receipt=corpus_receipt.identity,
            hybrid_binding_manifest=binding.identity,
        ),
        "provider": ProviderPacketArtifacts(
            provider_environment_verifier=environment_verifier.raw_file,
            provider_environment_manifest=environment.identity,
            local_model_policy_manifest=model.identity,
            prompt_policy_manifest=prompt.identity,
            provider_qualification_runner=qualification_runner.raw_file,
            provider_qualification_module=qualification_module.raw_file,
            provider_qualification_definition=qualification_definition.identity,
            provider_qualification_report=qualification_report.identity,
        ),
        "benchmark": BenchmarkPacketArtifacts(
            human_benchmark_definition=definition.identity,
        ),
        "summary": PacketSummary(
            structured=structured_summary,
            corpus=corpus_summary,
            provider=provider_summary,
            benchmark=benchmark_summary,
        ),
        "boundary": CandidateApprovalBoundary(
            checkpoint=2,
            owner_approval_required=True,
            packet_build_database_writes_performed=False,
            production_database_role_qualified=False,
            structured_validation_receipt_included=False,
            human_semantic_verdict_included=False,
            published_status_claimed=False,
            publication_authorized=False,
            external_tag_release_or_image_authorized=False,
        ),
        "packet_sha256": "0" * 64,
    }
    payload["packet_sha256"] = canonical_self_sha256(payload, "packet_sha256")
    return V0ActivationManifestPacket.model_validate(payload)


def _all_raw_refs(packet: V0ActivationManifestPacket) -> tuple[RawFileIdentity, ...]:
    structured = packet.structured
    corpus = packet.corpus
    provider = packet.provider
    return (
        packet.contract.approved_contract,
        packet.contract.errata,
        *(row.raw_file for row in packet.authority_captures),
        packet.frozen_sources.m1_source_manifest,
        packet.frozen_sources.m1_source_audit,
        packet.frozen_sources.ncbi_taxdump_archive,
        packet.frozen_sources.ncbi_taxdump_checksum,
        packet.frozen_sources.ictv_msl_workbook,
        packet.frozen_sources.ictv_vmr_workbook,
        packet.frozen_sources.full_sequence_bundle,
        *(row.raw_file for row in packet.frozen_sources.excluded_taxdump_candidates),
        structured.ncbi_artifact_manifest.raw_file,
        structured.ncbi_snapshot_manifest.raw_file,
        structured.assembly_taxon_assignment_manifest.raw_file,
        structured.ictv_artifact_manifest.raw_file,
        structured.ictv_snapshot_manifest.raw_file,
        structured.study_formal_mapping_manifest.raw_file,
        structured.adjudication_cohort_manifest.raw_file,
        structured.full_sequence_bundle_manifest.raw_file,
        structured.flank_request_plan_manifest.raw_file,
        structured.flank_evidence_manifest.raw_file,
        structured.inclusion_decision_manifest.raw_file,
        structured.structured_adjudication_manifest.raw_file,
        structured.public_locus_membership_manifest.raw_file,
        structured.public_assertion_membership_manifest.raw_file,
        structured.structured_activation_manifest.raw_file,
        corpus.corpus_manifest.raw_file,
        corpus.anchor_manifest.raw_file,
        corpus.corpus_validation_receipt.raw_file,
        corpus.hybrid_binding_manifest.raw_file,
        provider.provider_environment_verifier,
        provider.provider_environment_manifest.raw_file,
        provider.local_model_policy_manifest.raw_file,
        provider.prompt_policy_manifest.raw_file,
        provider.provider_qualification_runner,
        provider.provider_qualification_module,
        provider.provider_qualification_definition.raw_file,
        provider.provider_qualification_report.raw_file,
        packet.benchmark.human_benchmark_definition.raw_file,
    )


def _paths_from_packet(packet: V0ActivationManifestPacket) -> ActivationManifestPacketPaths:
    structured = packet.structured
    corpus = packet.corpus
    provider = packet.provider
    sources = packet.frozen_sources
    return ActivationManifestPacketPaths(
        approved_contract=Path(packet.contract.approved_contract.path),
        contract_errata=Path(packet.contract.errata.path),
        authority=AuthorityPacketPaths(
            ncbi_usage_policy_capture=Path(packet.authority_captures[0].raw_file.path),
            ictv_usage_policy_capture=Path(packet.authority_captures[1].raw_file.path),
            ictv_proposal_capture=Path(packet.authority_captures[2].raw_file.path),
        ),
        sources=SourcePacketPaths(
            m1_source_manifest=Path(sources.m1_source_manifest.path),
            m1_source_audit=Path(sources.m1_source_audit.path),
            ncbi_taxdump_archive=Path(sources.ncbi_taxdump_archive.path),
            ncbi_taxdump_checksum=Path(sources.ncbi_taxdump_checksum.path),
            ictv_msl_workbook=Path(sources.ictv_msl_workbook.path),
            ictv_vmr_workbook=Path(sources.ictv_vmr_workbook.path),
            full_sequence_bundle=Path(sources.full_sequence_bundle.path),
            excluded_taxdump_md5=Path(sources.excluded_taxdump_candidates[0].raw_file.path),
            excluded_taxdump_size=Path(sources.excluded_taxdump_candidates[1].raw_file.path),
        ),
        structured=StructuredPacketPaths(
            ncbi_artifact_manifest=Path(structured.ncbi_artifact_manifest.raw_file.path),
            ncbi_snapshot_manifest=Path(structured.ncbi_snapshot_manifest.raw_file.path),
            assembly_taxon_assignment_manifest=Path(
                structured.assembly_taxon_assignment_manifest.raw_file.path
            ),
            ictv_artifact_manifest=Path(structured.ictv_artifact_manifest.raw_file.path),
            ictv_snapshot_manifest=Path(structured.ictv_snapshot_manifest.raw_file.path),
            study_formal_mapping_manifest=Path(
                structured.study_formal_mapping_manifest.raw_file.path
            ),
            adjudication_cohort_manifest=Path(
                structured.adjudication_cohort_manifest.raw_file.path
            ),
            full_sequence_bundle_manifest=Path(
                structured.full_sequence_bundle_manifest.raw_file.path
            ),
            flank_request_plan_manifest=Path(structured.flank_request_plan_manifest.raw_file.path),
            flank_evidence_manifest=Path(structured.flank_evidence_manifest.raw_file.path),
            inclusion_decision_manifest=Path(structured.inclusion_decision_manifest.raw_file.path),
            structured_adjudication_manifest=Path(
                structured.structured_adjudication_manifest.raw_file.path
            ),
            public_locus_membership_manifest=Path(
                structured.public_locus_membership_manifest.raw_file.path
            ),
            public_assertion_membership_manifest=Path(
                structured.public_assertion_membership_manifest.raw_file.path
            ),
            structured_activation_manifest=Path(
                structured.structured_activation_manifest.raw_file.path
            ),
        ),
        corpus=CorpusPacketPaths(
            corpus_manifest=Path(corpus.corpus_manifest.raw_file.path),
            anchor_manifest=Path(corpus.anchor_manifest.raw_file.path),
            corpus_validation_receipt=Path(corpus.corpus_validation_receipt.raw_file.path),
            hybrid_binding_manifest=Path(corpus.hybrid_binding_manifest.raw_file.path),
        ),
        provider=ProviderPacketPaths(
            provider_environment_verifier=Path(provider.provider_environment_verifier.path),
            provider_environment_manifest=Path(
                provider.provider_environment_manifest.raw_file.path
            ),
            local_model_policy_manifest=Path(provider.local_model_policy_manifest.raw_file.path),
            prompt_policy_manifest=Path(provider.prompt_policy_manifest.raw_file.path),
            provider_qualification_runner=Path(provider.provider_qualification_runner.path),
            provider_qualification_module=Path(provider.provider_qualification_module.path),
            provider_qualification_definition=Path(
                provider.provider_qualification_definition.raw_file.path
            ),
            provider_qualification_report=Path(
                provider.provider_qualification_report.raw_file.path
            ),
        ),
        human_benchmark_definition=Path(packet.benchmark.human_benchmark_definition.raw_file.path),
    )


def verify_activation_manifest_packet(
    root: Path,
    packet_path: Path,
    *,
    expected_packet_sha256: str,
    expected_file_sha256: str,
) -> V0ActivationManifestPacket:
    """Verify exact approved bytes and reproduce every referenced packet input."""

    root = _validated_root(root)
    observation = _observe_file(
        root,
        packet_path,
        collect_content=True,
        maximum_bytes=MAX_PACKET_OUTPUT_BYTES,
    )
    assert observation.content is not None
    try:
        packet = V0ActivationManifestPacket.model_validate_json(
            observation.content,
            strict=True,
        )
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ActivationManifestPacketError("activation manifest packet is invalid") from exc
    canonical_bytes = (canonical_model_json(packet) + "\n").encode("utf-8")
    _require(
        hmac.compare_digest(observation.content, canonical_bytes),
        "activation manifest packet is not canonical JSON bytes",
    )
    _require(
        _SHA256_RE.fullmatch(expected_packet_sha256) is not None
        and hmac.compare_digest(packet.packet_sha256, expected_packet_sha256),
        "activation manifest packet is not the approved semantic checksum",
    )
    _require(
        _SHA256_RE.fullmatch(expected_file_sha256) is not None
        and hmac.compare_digest(
            observation.raw_file.file_sha256,
            expected_file_sha256,
        ),
        "activation manifest packet is not the approved physical file checksum",
    )
    rebuilt = build_activation_manifest_packet(root, _paths_from_packet(packet))
    _require(rebuilt == packet, "activation manifest packet does not reproduce exactly")
    return packet


def write_activation_manifest_packet(
    output_path: Path,
    packet: V0ActivationManifestPacket,
) -> WrittenPacketIdentity:
    """Write one new canonical packet with O_EXCL; never replace prior evidence."""

    output = output_path.expanduser()
    parent = output.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise ActivationManifestPacketError("packet output directory is unavailable") from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise ActivationManifestPacketError("packet output directory must be real")
    raw = (canonical_model_json(packet) + "\n").encode("utf-8")
    if len(raw) > MAX_PACKET_OUTPUT_BYTES:
        raise ActivationManifestPacketError("packet output exceeds its size bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(output, flags, 0o644)
    except FileExistsError:
        raise ActivationManifestPacketError("packet output already exists") from None
    except OSError as exc:
        raise ActivationManifestPacketError("packet output cannot be created") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            output.unlink(missing_ok=True)
        finally:
            raise
    return WrittenPacketIdentity(
        byte_size=len(raw),
        file_sha256=hashlib.sha256(raw).hexdigest(),
    )


__all__ = [
    "MAX_PACKET_INPUT_BYTES",
    "PACKET_KEY",
    "PACKET_SCHEMA_VERSION",
    "ActivationManifestPacketError",
    "ActivationManifestPacketPaths",
    "AuthorityPacketPaths",
    "BenchmarkPacketArtifacts",
    "CandidateApprovalBoundary",
    "ContractEvidence",
    "CorpusPacketArtifacts",
    "CorpusPacketPaths",
    "ProviderEnvironmentManifest",
    "ProviderPacketArtifacts",
    "ProviderPacketPaths",
    "RawFileIdentity",
    "SourcePacketPaths",
    "StructuredPacketArtifacts",
    "StructuredPacketPaths",
    "TypedArtifactIdentity",
    "TypedSemanticIdentity",
    "V0ActivationManifestPacket",
    "WrittenPacketIdentity",
    "build_activation_manifest_packet",
    "observe_raw_file",
    "verify_activation_manifest_packet",
    "verify_raw_file_identity",
    "write_activation_manifest_packet",
]
