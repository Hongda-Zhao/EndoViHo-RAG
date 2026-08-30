"""Strict, self-checksummed contracts for structured V0 activation.

These schemas are deliberately authorization-neutral.  Passing schema and checksum
validation means only that an artifact is internally coherent.  It does not approve
scientific decisions, mutate a release, or authorize publication.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import PurePath
from typing import Annotated, Any, Final, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.domain.keys import (
    is_release_key,
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
    stable_key,
)
from eve_relation_rag.importers.data_s1 import DATA_S1_ASSEMBLY_ALLOWLIST
from eve_relation_rag.literature.hashing import canonical_json_sha256

ACTIVATION_RELEASE_KEY: Final = "release:endoviho-rag:v0:20260826:001"
INCLUSION_POLICY_KEY: Final = "policy:v0-pilot-inclusion-v1"
FLANK_ASSESSMENT_POLICY_KEY: Final = "policy:v0-flank-context-20000-v1"
FLANK_WINDOW_BP: Final = 20_000
APPROVED_ASSEMBLIES: Final = tuple(sorted(DATA_S1_ASSEMBLY_ALLOWLIST))

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_MD5_RE: Final = re.compile(r"^[0-9a-f]{32}$")
_LOCUS_KEY_RE: Final = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_RFC3339_UTC_RE: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_CANONICAL_FRACTION_RE: Final = re.compile(r"^(?:0|1)\.[0-9]{6}$")
_IUPAC_DNA_RE: Final = re.compile(r"^[ACGTRYSWKMBDHVN]*$")


class ActivationSchema(BaseModel):
    """Strict immutable base for every activation packet object."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_non_empty_nfc(value: str) -> str:
    if not value or not value.strip() or value != value.strip():
        raise ValueError("value must be non-empty and have no outer whitespace")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("value must already be Unicode NFC")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("value must not contain control or format characters")
    return value


def _validate_stable_token(value: str) -> str:
    _validate_non_empty_nfc(value)
    if any(character.isspace() for character in value):
        raise ValueError("stable token must not contain whitespace")
    return value


def _validate_https_uri(value: str) -> str:
    _validate_non_empty_nfc(value)
    if not value.startswith("https://"):
        raise ValueError("upstream URI must use HTTPS")
    return value


def _validate_rfc3339_utc(value: str) -> str:
    if _RFC3339_UTC_RE.fullmatch(value) is None:
        raise ValueError("timestamp must be canonical RFC3339 UTC ending in Z")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return value


def _validate_release_key(value: str) -> str:
    if not is_release_key(value):
        raise ValueError("release_key is not an exact versioned release key")
    return value


def _validate_assembly_accession(value: str) -> str:
    if not is_versioned_assembly_accession(value):
        raise ValueError("assembly accession must include an exact GCA_/GCF_ version")
    return value


def _validate_sequence_accession(value: str) -> str:
    if not is_versioned_contig_accession(value):
        raise ValueError("sequence accession must include an exact INSDC version")
    return value


def _validate_locus_key(value: str) -> str:
    if _LOCUS_KEY_RE.fullmatch(value) is None:
        raise ValueError("locus_key must match locus:eve:v1:sha256:<digest>")
    return value


def _validate_filename(value: str) -> str:
    _validate_non_empty_nfc(value)
    path = PurePath(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise ValueError("filename must be one safe basename")
    return value


def _validate_iupac_sequence(value: str) -> str:
    if _IUPAC_DNA_RE.fullmatch(value) is None:
        raise ValueError("normalized_sequence must be uppercase IUPAC DNA with no whitespace")
    return value


type Sha256 = Annotated[str, Field(pattern=_SHA256_RE.pattern)]
type NonEmptyText = Annotated[str, Field(min_length=1), AfterValidator(_validate_non_empty_nfc)]
type StableToken = Annotated[
    str,
    Field(min_length=1, max_length=255),
    AfterValidator(_validate_stable_token),
]
type HttpsUri = Annotated[str, Field(min_length=9), AfterValidator(_validate_https_uri)]
type Rfc3339Utc = Annotated[str, AfterValidator(_validate_rfc3339_utc)]
type ReleaseKey = Annotated[str, Field(max_length=255), AfterValidator(_validate_release_key)]
type AssemblyAccession = Annotated[
    str,
    Field(max_length=64),
    AfterValidator(_validate_assembly_accession),
]
type SequenceAccession = Annotated[
    str,
    Field(max_length=128),
    AfterValidator(_validate_sequence_accession),
]
type LocusKey = Annotated[str, Field(max_length=255), AfterValidator(_validate_locus_key)]
type SafeFilename = Annotated[str, Field(max_length=255), AfterValidator(_validate_filename)]
type IupacSequence = Annotated[str, AfterValidator(_validate_iupac_sequence)]
type CanonicalFraction = Annotated[str, Field(pattern=_CANONICAL_FRACTION_RE.pattern)]


def canonical_model_sha256(value: object) -> str:
    """Hash a model through JSON mode and the project canonical JSON encoder."""

    return canonical_json_sha256(_json_compatible(value))


def _json_compatible(value: object) -> object:
    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {key: _json_compatible(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(child) for child in value]
    return value


def canonical_self_sha256(
    value: BaseModel | Mapping[str, object], digest_field: str = "manifest_sha256"
) -> str:
    """Hash an object after removing exactly one self-digest field."""

    payload: dict[str, Any]
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = dict(value)
    if digest_field not in payload:
        raise ValueError(f"payload is missing self-digest field {digest_field}")
    del payload[digest_field]
    return canonical_model_sha256(payload)


def seal_manifest_payload(
    payload: Mapping[str, object], digest_field: str = "manifest_sha256"
) -> dict[str, object]:
    """Return a copy with a computed self digest, rejecting a pre-populated digest."""

    sealed = dict(payload)
    if digest_field in sealed:
        raise ValueError(f"payload already contains {digest_field}")
    sealed[digest_field] = canonical_model_sha256(sealed)
    return sealed


def canonical_revalidate[ModelT: BaseModel](value: ModelT) -> ModelT:
    """Re-run validation through JSON at public trust boundaries.

    Pydantic's ``model_copy(update=...)`` deliberately skips validation.  Activation
    tooling therefore must not trust even an apparently typed model received from a
    caller.  A JSON round-trip also normalizes tuples and other transport details to
    the exact representation covered by canonical digests.
    """

    validated = type(value).model_validate_json(value.model_dump_json(), strict=True)
    return validated


class SelfHashedManifest(ActivationSchema):
    """Base for manifests whose digest excludes only ``manifest_sha256``."""

    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_self_digest(self) -> Self:
        if self.manifest_sha256 != canonical_self_sha256(self):
            raise ValueError("manifest_sha256 does not match canonical manifest payload")
        return self


class FrozenUpstreamArtifact(ActivationSchema):
    """One downloaded public artifact with an independently observed upstream digest."""

    artifact_key: StableToken
    filename: SafeFilename
    media_type: StableToken
    byte_size: int = Field(gt=0)
    sha256: Sha256
    upstream_checksum_algorithm: Literal["sha256", "md5"] | None
    upstream_checksum: StableToken | None
    upstream_checksum_verified: bool
    source_uri: HttpsUri
    checksum_source_uri: HttpsUri | None
    retrieved_at: Rfc3339Utc
    license_key: StableToken

    @model_validator(mode="after")
    def validate_upstream_digest(self) -> Self:
        supplied = (
            self.upstream_checksum_algorithm is not None,
            self.upstream_checksum is not None,
            self.checksum_source_uri is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError("upstream checksum provenance must be supplied as one unit")
        if not any(supplied):
            if self.upstream_checksum_verified:
                raise ValueError("an absent upstream checksum cannot be marked verified")
            return self
        if not self.upstream_checksum_verified:
            raise ValueError("a supplied upstream checksum must be locally verified")
        assert self.upstream_checksum_algorithm is not None
        assert self.upstream_checksum is not None
        pattern = _SHA256_RE if self.upstream_checksum_algorithm == "sha256" else _MD5_RE
        if pattern.fullmatch(self.upstream_checksum) is None:
            raise ValueError("upstream checksum does not match its named algorithm")
        if self.upstream_checksum_algorithm == "sha256" and self.sha256 != self.upstream_checksum:
            raise ValueError("downloaded artifact does not match the upstream SHA-256")
        return self


class UsagePolicyEvidence(ActivationSchema):
    """Checksum-frozen local capture of the applicable public usage policy."""

    usage_basis_key: StableToken
    source_uri: HttpsUri
    retrieved_at: Rfc3339Utc
    local_capture_sha256: Sha256


class TaxdumpMember(ActivationSchema):
    """One required file verified inside the NCBI taxdump archive."""

    filename: Literal["delnodes.dmp", "merged.dmp", "names.dmp", "nodes.dmp"]
    byte_size: int = Field(gt=0)
    sha256: Sha256


class NcbiTaxonomyArtifactManifest(SelfHashedManifest):
    """Exact NCBI Taxonomy package, including merged and deleted TaxId history."""

    manifest_schema_version: Literal["ncbi-taxonomy-artifact-manifest-v1"]
    snapshot_key: StableToken
    authority_namespace: Literal["ncbi-taxonomy"]
    version: NonEmptyText
    archive: FrozenUpstreamArtifact
    members: tuple[TaxdumpMember, ...] = Field(min_length=4, max_length=4)
    usage_policy: UsagePolicyEvidence

    @model_validator(mode="after")
    def validate_required_members(self) -> Self:
        expected_snapshot_key = stable_key(
            "lineage-snapshot:ncbi-taxonomy",
            {"archive_sha256": self.archive.sha256, "filename": self.archive.filename},
        )
        if self.snapshot_key != expected_snapshot_key:
            raise ValueError("NCBI snapshot_key must derive from the exact archive identity")
        names = tuple(member.filename for member in self.members)
        required = ("delnodes.dmp", "merged.dmp", "names.dmp", "nodes.dmp")
        if names != required:
            raise ValueError("taxdump members must be the four required files in canonical order")
        if self.archive.filename != "taxdump.tar.gz":
            raise ValueError("the canonical NCBI taxdump filename is required")
        if self.archive.license_key != "NCBI-PUBLIC-DOMAIN-US-GOVERNMENT-WORK":
            raise ValueError("unexpected NCBI taxonomy license identity")
        if (
            self.archive.upstream_checksum_algorithm != "md5"
            or not self.archive.upstream_checksum_verified
        ):
            raise ValueError("NCBI taxdump requires its publisher-supplied MD5 checksum")
        if self.usage_policy.usage_basis_key != "NCBI-MOLECULAR-DATA-USAGE-POLICY":
            raise ValueError("unexpected NCBI usage-policy identity")
        return self


class IctvArtifactManifest(SelfHashedManifest):
    """Exact ICTV MSL41 plus the corrected 2026-07-29 VMR artifact."""

    manifest_schema_version: Literal["ictv-msl41-artifact-manifest-v1"]
    snapshot_key: StableToken
    authority_namespace: Literal["ictv"]
    msl_version: Literal["MSL41 v1"]
    msl: FrozenUpstreamArtifact
    corrected_vmr: FrozenUpstreamArtifact
    vmr_revision: Literal["MSL41.v1.20260729"]
    usage_policy: UsagePolicyEvidence

    @model_validator(mode="after")
    def validate_approved_files(self) -> Self:
        expected_snapshot_key = stable_key(
            "lineage-snapshot:ictv-msl41",
            {
                "msl_sha256": self.msl.sha256,
                "vmr_revision": self.vmr_revision,
                "vmr_sha256": self.corrected_vmr.sha256,
            },
        )
        if self.snapshot_key != expected_snapshot_key:
            raise ValueError("ICTV snapshot_key must derive from both approved workbooks")
        if self.msl.filename != "ICTV_Master_Species_List_2025_MSL41.v1.xlsx":
            raise ValueError("the exact ICTV MSL41 filename is required")
        if self.corrected_vmr.filename != "VMR_MSL41.v1.20260729.xlsx":
            raise ValueError("the corrected 2026-07-29 VMR is required")
        if self.msl.license_key != "CC-BY-4.0" or self.corrected_vmr.license_key != "CC-BY-4.0":
            raise ValueError("ICTV artifacts must use the approved CC-BY-4.0 identity")
        if self.usage_policy.usage_basis_key != "ICTV-CC-BY-4.0":
            raise ValueError("unexpected ICTV usage-policy identity")
        return self


class TaxonomySourceLocator(ActivationSchema):
    """Direct locator into a taxdump member or XLSX worksheet row."""

    artifact_key: StableToken
    member_name: SafeFilename | None = None
    worksheet: NonEmptyText | None = None
    row_number: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_one_container_locator(self) -> Self:
        if (self.member_name is None) == (self.worksheet is None):
            raise ValueError("exactly one of member_name or worksheet is required")
        return self


class TaxonomyAliasSpec(ActivationSchema):
    alias: NonEmptyText
    normalized_alias: NonEmptyText
    alias_type: StableToken
    locale: Literal["en", "la", "und"] = "und"


class TaxonomyTermSpec(ActivationSchema):
    term_key: StableToken
    canonical_name: NonEmptyText
    rank: NonEmptyText | None
    authority_local_id: StableToken | None
    parent_term_key: StableToken | None
    source_locator: TaxonomySourceLocator
    aliases: tuple[TaxonomyAliasSpec, ...] = ()

    @model_validator(mode="after")
    def validate_alias_order(self) -> Self:
        keys = tuple((row.normalized_alias, row.alias_type, row.locale) for row in self.aliases)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("taxonomy aliases must be unique and canonically ordered")
        return self


class NcbiHistorySummary(ActivationSchema):
    merged_tax_id_count: int = Field(ge=0)
    deleted_tax_id_count: int = Field(ge=0)
    merged_rows_sha256: Sha256
    deleted_rows_sha256: Sha256


class TaxonomySnapshotManifest(SelfHashedManifest):
    """Normalized queryable hierarchy derived from one frozen upstream package."""

    manifest_schema_version: Literal["taxonomy-snapshot-manifest-v1"]
    snapshot_key: StableToken
    domain: Literal["host", "viral"]
    scheme_kind: Literal["formal_taxonomy"]
    authority_namespace: Literal["ncbi-taxonomy", "ictv"]
    version: NonEmptyText
    release_role: Literal["assembly_source_taxonomy", "formal_viral_taxonomy"]
    artifact_manifest_sha256: Sha256
    primary_artifact_key: StableToken
    coverage: Literal[
        "required-taxa-and-ancestors-complete-history-bound",
        "complete-msl41-hierarchy",
    ]
    terms: tuple[TaxonomyTermSpec, ...] = Field(min_length=1)
    ncbi_history: NcbiHistorySummary | None = None

    @model_validator(mode="after")
    def validate_namespace_and_tree(self) -> Self:
        if self.authority_namespace == "ncbi-taxonomy":
            if (
                self.domain != "host"
                or self.release_role != "assembly_source_taxonomy"
                or self.coverage != "required-taxa-and-ancestors-complete-history-bound"
                or self.ncbi_history is None
            ):
                raise ValueError("NCBI host snapshot fields are inconsistent")
        elif (
            self.domain != "viral"
            or self.release_role != "formal_viral_taxonomy"
            or self.coverage != "complete-msl41-hierarchy"
            or self.ncbi_history is not None
        ):
            raise ValueError("ICTV viral snapshot fields are inconsistent")

        keys = tuple(term.term_key for term in self.terms)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("taxonomy terms must be unique and sorted by term_key")
        key_set = set(keys)
        parent_by_key = {term.term_key: term.parent_term_key for term in self.terms}
        for term_key, parent_key in parent_by_key.items():
            if parent_key is not None and parent_key not in key_set:
                raise ValueError(f"taxonomy parent is absent from snapshot: {term_key}")
            if parent_key == term_key:
                raise ValueError("taxonomy term cannot parent itself")
            seen: set[str] = set()
            cursor: str | None = term_key
            while cursor is not None:
                if cursor in seen:
                    raise ValueError("taxonomy hierarchy contains a cycle")
                seen.add(cursor)
                cursor = parent_by_key[cursor]
        return self


class StudyFormalMappingRow(ActivationSchema):
    mapping_key: StableToken
    study_snapshot_key: StableToken
    study_term_key: StableToken
    formal_snapshot_key: StableToken
    formal_term_key: StableToken
    relation: Literal["renamed_to", "curated_equivalent_to"]
    curation_method_key: StableToken
    evidence_artifact_sha256: Sha256
    evidence_locator: NonEmptyText

    @model_validator(mode="after")
    def validate_mapping_key(self) -> Self:
        expected = stable_key(
            "study-formal-mapping",
            {
                "formal_snapshot_key": self.formal_snapshot_key,
                "formal_term_key": self.formal_term_key,
                "relation": self.relation,
                "study_snapshot_key": self.study_snapshot_key,
                "study_term_key": self.study_term_key,
            },
        )
        if self.mapping_key != expected:
            raise ValueError("mapping_key does not bind the exact mapping endpoints")
        return self


class StudyFormalMappingManifest(SelfHashedManifest):
    """Explicit curated bridge; lexical similarity never creates a mapping."""

    manifest_schema_version: Literal["study-formal-mapping-manifest-v1"]
    release_key: ReleaseKey
    study_snapshot_key: StableToken
    formal_snapshot_key: StableToken
    formal_snapshot_manifest_sha256: Sha256
    mappings: tuple[StudyFormalMappingRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mapping_scope(self) -> Self:
        keys = tuple((row.study_term_key, row.formal_term_key) for row in self.mappings)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("mapping rows must be unique and canonically ordered")
        for row in self.mappings:
            if row.study_snapshot_key != self.study_snapshot_key:
                raise ValueError("mapping row uses a different study snapshot")
            if row.formal_snapshot_key != self.formal_snapshot_key:
                raise ValueError("mapping row uses a different formal snapshot")
        return self


class AssemblyTaxonAssignmentSpec(ActivationSchema):
    assembly_accession_version: AssemblyAccession
    reported_ncbi_tax_id: int = Field(gt=0)
    resolved_ncbi_tax_id: int = Field(gt=0)
    term_key: StableToken
    assignment_policy_key: Literal["ncbi-taxdump-assembly-taxid-v1"]
    source_artifact_key: StableToken
    source_locator: NonEmptyText


class AssemblyTaxonAssignmentManifest(SelfHashedManifest):
    manifest_schema_version: Literal["assembly-taxon-assignment-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    ncbi_snapshot_manifest_sha256: Sha256
    assignments: tuple[AssemblyTaxonAssignmentSpec, ...] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_assignments(self) -> Self:
        assemblies = tuple(row.assembly_accession_version for row in self.assignments)
        if assemblies != APPROVED_ASSEMBLIES:
            raise ValueError("one canonical host-taxonomy assignment is required per assembly")
        if any(
            row.term_key != f"ncbi-taxonomy:taxid:{row.resolved_ncbi_tax_id}"
            for row in self.assignments
        ):
            raise ValueError("assignment term_key must identify its resolved NCBI TaxId")
        return self


class CohortRecord(ActivationSchema):
    """One exact-placement source occurrence eligible for flank adjudication."""

    source_record_key: StableToken
    source_row: int = Field(ge=1)
    locus_key: LocusKey
    interval_key: StableToken
    placement_key: StableToken | None
    placement_sha256: Sha256 | None
    assembly_accession_version: AssemblyAccession
    sequence_accession_version: SequenceAccession
    sequence_length: int = Field(gt=0)
    start0: int = Field(ge=0)
    end0: int = Field(gt=0)
    coordinate_system: Literal["0-based-half-open"]
    interval_basis: Literal[
        "canonical_exact_placement",
        "validated_source_quarantine_interval",
    ]
    source_assessment: Literal["source_high", "source_low"]
    import_outcome: Literal["normalized_candidate", "quarantine"]
    quarantine_issue_codes: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if not self.start0 < self.end0 <= self.sequence_length:
            raise ValueError("cohort placement is outside the exact sequence bounds")
        if self.quarantine_issue_codes != tuple(sorted(self.quarantine_issue_codes)) or len(
            self.quarantine_issue_codes
        ) != len(set(self.quarantine_issue_codes)):
            raise ValueError("quarantine issue codes must be unique and sorted")
        if self.import_outcome == "normalized_candidate":
            if (
                self.interval_basis != "canonical_exact_placement"
                or self.placement_key is None
                or self.placement_sha256 is None
                or self.interval_key != self.placement_key
                or self.quarantine_issue_codes
            ):
                raise ValueError("normalized cohort row must bind one exact placement")
        elif (
            self.source_assessment != "source_high"
            or self.interval_basis != "validated_source_quarantine_interval"
            or self.placement_key is not None
            or self.placement_sha256 is not None
            or "viral_contig_policy_quarantine" not in self.quarantine_issue_codes
        ):
            raise ValueError("quarantine cohort row must be the accounted source-high interval")
        return self


class AssemblyExpansionQueue(ActivationSchema):
    assembly_accession_version: AssemblyAccession
    records: tuple[CohortRecord, ...]

    @model_validator(mode="after")
    def validate_queue(self) -> Self:
        for record in self.records:
            if record.assembly_accession_version != self.assembly_accession_version:
                raise ValueError("expansion record belongs to a different assembly")
            if record.source_assessment != "source_low":
                raise ValueError("expansion queue may contain only source_low records")
            if record.import_outcome != "normalized_candidate":
                raise ValueError("expansion queue may contain only normalized candidates")
        rows = tuple(record.source_row for record in self.records)
        if rows != tuple(sorted(rows)) or len(rows) != len(set(rows)):
            raise ValueError("expansion records must be in unique ascending source-row order")
        return self


class AdjudicationCohortManifest(SelfHashedManifest):
    """Preregistered primary cohort plus deterministic per-assembly expansion queues."""

    manifest_schema_version: Literal["structured-adjudication-cohort-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    source_manifest_sha256: Sha256
    source_audit_sha256: Sha256
    selection_policy_key: Literal["policy:v0-adjudication-cohort-v1"]
    primary_records: tuple[CohortRecord, ...] = Field(min_length=71, max_length=71)
    expansion_queues: tuple[AssemblyExpansionQueue, ...] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_preregistered_cohort(self) -> Self:
        if any(record.source_assessment != "source_high" for record in self.primary_records):
            raise ValueError("all 71 primary records must be source_high")
        if sum(record.import_outcome == "quarantine" for record in self.primary_records) != 1:
            raise ValueError("the primary cohort must retain the one accounted viral-contig row")
        primary_rows = tuple(record.source_row for record in self.primary_records)
        if primary_rows != tuple(sorted(primary_rows)) or len(primary_rows) != len(
            set(primary_rows)
        ):
            raise ValueError("primary records must use unique ascending physical source rows")
        assemblies = tuple(queue.assembly_accession_version for queue in self.expansion_queues)
        if assemblies != APPROVED_ASSEMBLIES:
            raise ValueError("one canonical expansion queue is required for each approved assembly")
        all_records = list(self.primary_records)
        for queue in self.expansion_queues:
            all_records.extend(queue.records)
        for field_name in ("source_record_key", "locus_key", "interval_key", "source_row"):
            values = tuple(getattr(record, field_name) for record in all_records)
            if len(values) != len(set(values)):
                raise ValueError(f"cohort contains duplicate {field_name}")
        return self


class FlankEvidenceRequest(ActivationSchema):
    """Exact NCBI nuccore range request derived from one 0-based locus placement."""

    request_sha256: Sha256
    cohort_manifest_sha256: Sha256
    selection_tier: Literal["primary", "expansion"]
    source_record_key: StableToken
    source_row: int = Field(ge=1)
    locus_key: LocusKey
    interval_key: StableToken
    placement_key: StableToken | None
    interval_basis: Literal[
        "canonical_exact_placement",
        "validated_source_quarantine_interval",
    ]
    assembly_accession_version: AssemblyAccession
    sequence_accession_version: SequenceAccession
    sequence_length: int = Field(gt=0)
    locus_start0: int = Field(ge=0)
    locus_end0: int = Field(gt=0)
    request_start0: int = Field(ge=0)
    request_end0: int = Field(gt=0)
    ncbi_range_start1: int = Field(ge=1)
    ncbi_range_end1: int = Field(ge=1)
    expected_left_bp: int = Field(ge=0, le=FLANK_WINDOW_BP)
    expected_right_bp: int = Field(ge=0, le=FLANK_WINDOW_BP)
    inspection_window_bp: Literal[20000]
    database: Literal["nuccore"]
    rettype: Literal["fasta"]
    retmode: Literal["text"]
    strand: Literal["plus"]

    @model_validator(mode="after")
    def validate_coordinates_and_digest(self) -> Self:
        if (self.interval_basis == "canonical_exact_placement") != (
            self.placement_key is not None and self.interval_key == self.placement_key
        ):
            raise ValueError("request interval basis disagrees with placement identity")
        if not (
            0
            <= self.request_start0
            <= self.locus_start0
            < self.locus_end0
            <= self.request_end0
            <= self.sequence_length
        ):
            raise ValueError("flank request interval does not contain the bounded locus")
        if self.request_start0 != max(0, self.locus_start0 - FLANK_WINDOW_BP):
            raise ValueError("request_start0 does not implement the approved left window")
        if self.request_end0 != min(self.sequence_length, self.locus_end0 + FLANK_WINDOW_BP):
            raise ValueError("request_end0 does not implement the approved right window")
        if self.ncbi_range_start1 != self.request_start0 + 1:
            raise ValueError("NCBI range start must convert 0-based start to 1-based start")
        if self.ncbi_range_end1 != self.request_end0:
            raise ValueError("NCBI inclusive range end must equal the 0-based exclusive end")
        if self.expected_left_bp != self.locus_start0 - self.request_start0:
            raise ValueError("expected_left_bp is inconsistent")
        if self.expected_right_bp != self.request_end0 - self.locus_end0:
            raise ValueError("expected_right_bp is inconsistent")
        if self.request_sha256 != canonical_self_sha256(self, "request_sha256"):
            raise ValueError("request_sha256 does not match the canonical request")
        return self


class FlankEvidenceRequestPlan(SelfHashedManifest):
    manifest_schema_version: Literal["flank-evidence-request-plan-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    cohort_manifest_sha256: Sha256
    inspection_window_bp: Literal[20000]
    requests: tuple[FlankEvidenceRequest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_request_set(self) -> Self:
        order = tuple(
            (
                request.assembly_accession_version,
                request.source_row,
                request.locus_key,
            )
            for request in self.requests
        )
        if order != tuple(sorted(order)) or len(order) != len(set(order)):
            raise ValueError("flank requests must be unique and canonically ordered")
        if any(
            request.cohort_manifest_sha256 != self.cohort_manifest_sha256
            for request in self.requests
        ):
            raise ValueError("flank request refers to a different cohort manifest")
        return self


class FetchToolIdentity(ActivationSchema):
    tool_name: Literal["ncbi-sequence-fetch"]
    tool_version: StableToken
    parser_policy_key: Literal[
        "parser:ncbi-fasta-range-wrapper-v1",
        "parser:ncbi-full-sequence-bundle-v1",
    ]


class FullSequenceBundleRecord(ActivationSchema):
    """One strict record in the downloaded aggregate full-sequence JSON array."""

    accession: SequenceAccession
    header: NonEmptyText
    sequence: IupacSequence
    length: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if len(self.sequence) != self.length:
            raise ValueError("full-sequence record length does not match sequence bytes")
        first_token = self.header.removeprefix(">").split(" ", maxsplit=1)[0]
        if first_token != self.accession:
            raise ValueError("FASTA header does not identify the exact accession.version")
        return self


class FullSequenceBundleIndexRow(ActivationSchema):
    accession_version: SequenceAccession
    sequence_length: int = Field(gt=0)
    normalized_sequence_sha256: Sha256


class FullSequenceBundleManifest(SelfHashedManifest):
    """Provenance sidecar for a raw aggregate sequence wrapper."""

    manifest_schema_version: Literal["ncbi-full-sequence-bundle-manifest-v1"]
    artifact_sha256: Sha256
    artifact_byte_size: int = Field(gt=0)
    source_uri: HttpsUri
    retrieved_at: Rfc3339Utc
    http_status: Literal[200]
    acquisition_requests_per_second: Literal[3]
    api_key_used: Literal[False]
    tool: FetchToolIdentity
    record_count: int = Field(gt=0)
    total_sequence_bp: int = Field(gt=0)
    records: tuple[FullSequenceBundleIndexRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_index(self) -> Self:
        if self.record_count != len(self.records):
            raise ValueError("bundle record_count does not match records")
        if self.total_sequence_bp != sum(row.sequence_length for row in self.records):
            raise ValueError("bundle total_sequence_bp does not match records")
        accessions = tuple(row.accession_version for row in self.records)
        if accessions != tuple(sorted(accessions)) or len(accessions) != len(set(accessions)):
            raise ValueError("bundle index records must be unique and sorted by accession")
        return self


class NcbiSequenceFetchWrapper(ActivationSchema):
    """Strict offline handoff produced after an independently authorized download."""

    wrapper_schema_version: Literal["ncbi-sequence-fetch-wrapper-v1"]
    wrapper_sha256: Sha256
    request_sha256: Sha256
    status: Literal["success", "not_found", "retrieval_error"]
    requested_accession_version: SequenceAccession
    resolved_accession_version: SequenceAccession | None
    ncbi_range_start1: int = Field(ge=1)
    ncbi_range_end1: int = Field(ge=1)
    full_sequence_length: int | None = Field(default=None, gt=0)
    retrieved_at: Rfc3339Utc
    source_uri: HttpsUri
    http_status: int = Field(ge=100, le=599)
    response_byte_size: int | None = Field(default=None, ge=0)
    response_sha256: Sha256 | None
    normalized_sequence: IupacSequence | None
    normalized_sequence_sha256: Sha256 | None
    error_code: StableToken | None
    tool: FetchToolIdentity

    @model_validator(mode="after")
    def validate_status_and_digest(self) -> Self:
        if self.ncbi_range_start1 > self.ncbi_range_end1:
            raise ValueError("NCBI range is reversed")
        if self.status == "success":
            required = (
                self.resolved_accession_version,
                self.full_sequence_length,
                self.response_byte_size,
                self.response_sha256,
                self.normalized_sequence,
                self.normalized_sequence_sha256,
            )
            if any(value is None for value in required) or self.error_code is not None:
                raise ValueError("successful wrapper is missing evidence or has an error code")
            if not 200 <= self.http_status < 300:
                raise ValueError("successful wrapper requires a 2xx HTTP status")
            assert self.normalized_sequence is not None
            assert self.normalized_sequence_sha256 is not None
            expected_length = self.ncbi_range_end1 - self.ncbi_range_start1 + 1
            if len(self.normalized_sequence) != expected_length:
                raise ValueError("normalized sequence length does not match requested NCBI range")
            observed = hashlib.sha256(self.normalized_sequence.encode("ascii")).hexdigest()
            if observed != self.normalized_sequence_sha256:
                raise ValueError("normalized_sequence_sha256 does not match sequence bytes")
        else:
            forbidden = (
                self.resolved_accession_version,
                self.full_sequence_length,
                self.response_sha256,
                self.normalized_sequence,
                self.normalized_sequence_sha256,
            )
            if any(value is not None for value in forbidden) or self.error_code is None:
                raise ValueError("failed wrapper must contain only sanitized failure metadata")
        if self.wrapper_sha256 != canonical_self_sha256(self, "wrapper_sha256"):
            raise ValueError("wrapper_sha256 does not match canonical wrapper payload")
        return self


class FlankSideEvidence(ActivationSchema):
    side: Literal["left", "right"]
    verdict: Literal["supported", "insufficient", "contradicted", "not_assessed"]
    reason_code: StableToken
    available_bp: int = Field(ge=0)
    inspected_bp: int = Field(ge=0)
    ambiguous_bp: int = Field(ge=0)
    ambiguity_fraction: CanonicalFraction
    longest_ambiguity_run: int = Field(ge=0)
    boundary_base: (
        Literal["A", "C", "G", "T", "R", "Y", "S", "W", "K", "M", "B", "D", "H", "V", "N"] | None
    )

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        if not self.inspected_bp <= self.available_bp:
            raise ValueError("inspected_bp exceeds available_bp")
        if self.ambiguous_bp > self.inspected_bp:
            raise ValueError("ambiguous_bp exceeds inspected_bp")
        if self.longest_ambiguity_run > self.inspected_bp:
            raise ValueError("ambiguity run exceeds inspected sequence")
        if self.verdict == "supported" and (
            self.inspected_bp == 0 or self.boundary_base not in {"A", "C", "G", "T"}
        ):
            raise ValueError("supported flank requires an inspected unambiguous boundary base")
        return self


class FlankEvidenceRecord(ActivationSchema):
    record_sha256: Sha256
    request_sha256: Sha256
    wrapper_sha256: Sha256
    wrapper_file_sha256: Sha256
    source_record_key: StableToken
    source_row: int = Field(ge=1)
    locus_key: LocusKey
    interval_key: StableToken
    placement_key: StableToken | None
    interval_basis: Literal[
        "canonical_exact_placement",
        "validated_source_quarantine_interval",
    ]
    assessment_policy_key: Literal["policy:v0-flank-context-20000-v1"]
    assessed_by: StableToken
    assessed_at: Rfc3339Utc
    source_uri: HttpsUri
    response_sha256: Sha256 | None
    normalized_sequence_sha256: Sha256 | None
    left: FlankSideEvidence
    right: FlankSideEvidence

    @model_validator(mode="after")
    def validate_sides_and_digest(self) -> Self:
        if (self.interval_basis == "canonical_exact_placement") != (
            self.placement_key is not None and self.interval_key == self.placement_key
        ):
            raise ValueError("flank evidence interval basis disagrees with placement identity")
        if self.left.side != "left" or self.right.side != "right":
            raise ValueError("flank evidence must contain exact left and right sides")
        if self.record_sha256 != canonical_self_sha256(self, "record_sha256"):
            raise ValueError("record_sha256 does not match flank evidence payload")
        return self


class FlankEvidenceManifest(SelfHashedManifest):
    manifest_schema_version: Literal["flank-evidence-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    cohort_manifest_sha256: Sha256
    request_plan_manifest_sha256: Sha256
    records: tuple[FlankEvidenceRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_records(self) -> Self:
        order = tuple((row.source_row, row.locus_key) for row in self.records)
        if order != tuple(sorted(order)) or len(order) != len(set(order)):
            raise ValueError("flank records must be unique and ordered by source row")
        return self


class InclusionDecisionRecord(ActivationSchema):
    decision_sha256: Sha256
    source_record_key: StableToken
    source_row: int = Field(ge=1)
    locus_key: LocusKey
    interval_key: StableToken
    placement_key: StableToken | None
    import_outcome: Literal["normalized_candidate", "review", "quarantine", "exclude"]
    exact_placement_count: int = Field(ge=0)
    m1_gates_pass: bool
    dependency_snapshots_bound: bool
    ncbi_snapshot_manifest_sha256: Sha256 | None
    ictv_snapshot_manifest_sha256: Sha256 | None
    mapping_manifest_sha256: Sha256 | None
    flank_record_sha256: Sha256 | None
    left_flank_verdict: Literal["supported", "insufficient", "contradicted", "not_assessed"]
    right_flank_verdict: Literal["supported", "insufficient", "contradicted", "not_assessed"]
    unresolved_issue_codes: tuple[StableToken, ...] = ()
    quarantine_issue_codes: tuple[StableToken, ...] = ()
    conflict_codes: tuple[StableToken, ...] = ()
    decision: Literal["include", "review", "quarantine", "exclude"]
    reason_codes: tuple[StableToken, ...] = Field(min_length=1)
    policy_key: Literal["policy:v0-pilot-inclusion-v1"]
    authorized_by: Literal["policy:v0-pilot-inclusion-v1"]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        for field_name in (
            "unresolved_issue_codes",
            "quarantine_issue_codes",
            "conflict_codes",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique and sorted")
        dependencies = (
            self.ncbi_snapshot_manifest_sha256,
            self.ictv_snapshot_manifest_sha256,
            self.mapping_manifest_sha256,
        )
        if self.dependency_snapshots_bound != all(value is not None for value in dependencies):
            raise ValueError("dependency binding flag disagrees with dependency manifests")
        include_ok = (
            self.import_outcome == "normalized_candidate"
            and self.placement_key is not None
            and self.exact_placement_count == 1
            and self.m1_gates_pass
            and self.dependency_snapshots_bound
            and self.flank_record_sha256 is not None
            and self.left_flank_verdict == "supported"
            and self.right_flank_verdict == "supported"
            and not self.unresolved_issue_codes
            and not self.quarantine_issue_codes
            and not self.conflict_codes
        )
        if (self.decision == "include") != include_ok:
            raise ValueError("include decision does not match policy:v0-pilot-inclusion-v1")
        if self.decision_sha256 != canonical_self_sha256(self, "decision_sha256"):
            raise ValueError("decision_sha256 does not match canonical decision payload")
        return self


class InclusionDecisionManifest(SelfHashedManifest):
    manifest_schema_version: Literal["inclusion-decision-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    cohort_manifest_sha256: Sha256
    flank_manifest_sha256: Sha256
    policy_key: Literal["policy:v0-pilot-inclusion-v1"]
    decisions: tuple[InclusionDecisionRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision_set(self) -> Self:
        order = tuple((row.source_row, row.locus_key) for row in self.decisions)
        if order != tuple(sorted(order)) or len(order) != len(set(order)):
            raise ValueError("decisions must be unique and ordered by source row")
        return self


class AdjudicationSelectionRecord(ActivationSchema):
    source_record_key: StableToken
    source_row: int = Field(ge=1)
    locus_key: LocusKey
    assembly_accession_version: AssemblyAccession
    selection_tier: Literal["primary", "expansion"]
    expansion_ordinal: int | None = Field(default=None, ge=1)
    decision_sha256: Sha256

    @model_validator(mode="after")
    def validate_tier(self) -> Self:
        if (self.selection_tier == "primary") != (self.expansion_ordinal is None):
            raise ValueError("only expansion rows carry an expansion ordinal")
        return self


class AssemblyAdjudicationOutcome(ActivationSchema):
    assembly_accession_version: AssemblyAccession
    assessed_count: int = Field(ge=0)
    include_count: int = Field(ge=0)
    terminal_status: Literal["passing_locus_found", "assembly_exhausted_without_pass"]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.include_count > self.assessed_count:
            raise ValueError("assembly include count exceeds assessed count")
        if (self.terminal_status == "passing_locus_found") != (self.include_count > 0):
            raise ValueError("assembly terminal status disagrees with include count")
        return self


class StructuredAdjudicationManifest(SelfHashedManifest):
    manifest_schema_version: Literal["structured-adjudication-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    cohort_manifest_sha256: Sha256
    flank_manifest_sha256: Sha256
    inclusion_manifest_sha256: Sha256
    selections: tuple[AdjudicationSelectionRecord, ...] = Field(min_length=71)
    assembly_outcomes: tuple[AssemblyAdjudicationOutcome, ...] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        selection_keys = tuple((row.source_row, row.locus_key) for row in self.selections)
        if len(selection_keys) != len(set(selection_keys)):
            raise ValueError("adjudication selections contain duplicates")
        assemblies = tuple(row.assembly_accession_version for row in self.assembly_outcomes)
        if assemblies != APPROVED_ASSEMBLIES:
            raise ValueError("adjudication must report all approved assemblies canonically")
        return self


class PublicLocusMembershipRecord(ActivationSchema):
    locus_key: LocusKey
    placement_key: StableToken
    assembly_accession_version: AssemblyAccession
    sequence_accession_version: SequenceAccession
    start0: int = Field(ge=0)
    end0: int = Field(gt=0)
    coordinate_system: Literal["0-based-half-open"]
    left_flank_record_sha256: Sha256
    right_flank_record_sha256: Sha256
    inclusion_decision_sha256: Sha256

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start0 >= self.end0:
            raise ValueError("public locus interval is invalid")
        return self


class PublicLocusMembershipManifest(SelfHashedManifest):
    manifest_schema_version: Literal["public-locus-membership-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    adjudication_manifest_sha256: Sha256
    membership_count: int = Field(ge=10)
    memberships: tuple[PublicLocusMembershipRecord, ...] = Field(min_length=10)

    @model_validator(mode="after")
    def validate_memberships(self) -> Self:
        if self.membership_count != len(self.memberships):
            raise ValueError("membership_count does not match memberships")
        keys = tuple(row.locus_key for row in self.memberships)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("public locus memberships must be unique and canonical")
        if {row.assembly_accession_version for row in self.memberships} != set(APPROVED_ASSEMBLIES):
            raise ValueError("public memberships require at least one locus per assembly")
        return self


class PublicAssertionMembershipRecord(ActivationSchema):
    assertion_key: StableToken
    locus_key: LocusKey
    assertion_type: Literal["hcvr", "viral_major_taxon", "vr_type"]
    predicate_key: StableToken
    evidence_sha256s: tuple[Sha256, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.evidence_sha256s != tuple(sorted(self.evidence_sha256s)) or len(
            self.evidence_sha256s
        ) != len(set(self.evidence_sha256s)):
            raise ValueError("assertion evidence digests must be unique and sorted")
        return self


class PublicAssertionMembershipManifest(SelfHashedManifest):
    manifest_schema_version: Literal["public-assertion-membership-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    locus_membership_manifest_sha256: Sha256
    membership_count: int = Field(ge=1)
    memberships: tuple[PublicAssertionMembershipRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_memberships(self) -> Self:
        if self.membership_count != len(self.memberships):
            raise ValueError("membership_count does not match memberships")
        keys = tuple(row.assertion_key for row in self.memberships)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("public assertion memberships must be unique and canonical")
        return self


class StructuredActivationCounts(ActivationSchema):
    audited_source_records: Literal[39495]
    exact_placements: Literal[38968]
    accounted_quarantine: Literal[527]
    adjudicated_records: int = Field(ge=71)
    included_loci: int = Field(ge=10)
    public_locus_memberships: int = Field(ge=10)
    public_assertion_memberships: int = Field(ge=1)


class StructuredActivationManifest(SelfHashedManifest):
    """Top-level candidate packet; approval and publication remain separate actions."""

    manifest_schema_version: Literal["structured-activation-manifest-v1"]
    release_key: Literal["release:endoviho-rag:v0:20260826:001"]
    source_manifest_sha256: Sha256
    source_audit_sha256: Sha256
    ncbi_artifact_manifest_sha256: Sha256
    ncbi_snapshot_manifest_sha256: Sha256
    assembly_taxon_assignment_manifest_sha256: Sha256
    ictv_artifact_manifest_sha256: Sha256
    ictv_snapshot_manifest_sha256: Sha256
    study_formal_mapping_manifest_sha256: Sha256
    cohort_manifest_sha256: Sha256
    full_sequence_bundle_manifest_sha256: Sha256
    flank_request_plan_manifest_sha256: Sha256
    adjudication_manifest_sha256: Sha256
    flank_manifest_sha256: Sha256
    inclusion_manifest_sha256: Sha256
    public_locus_membership_manifest_sha256: Sha256
    public_assertion_membership_manifest_sha256: Sha256
    counts: StructuredActivationCounts


__all__ = [
    "ACTIVATION_RELEASE_KEY",
    "APPROVED_ASSEMBLIES",
    "FLANK_ASSESSMENT_POLICY_KEY",
    "FLANK_WINDOW_BP",
    "INCLUSION_POLICY_KEY",
    "ActivationSchema",
    "AdjudicationCohortManifest",
    "AdjudicationSelectionRecord",
    "AssemblyAdjudicationOutcome",
    "AssemblyExpansionQueue",
    "AssemblyTaxonAssignmentManifest",
    "AssemblyTaxonAssignmentSpec",
    "CohortRecord",
    "FetchToolIdentity",
    "FlankEvidenceManifest",
    "FlankEvidenceRecord",
    "FlankEvidenceRequest",
    "FlankEvidenceRequestPlan",
    "FlankSideEvidence",
    "FullSequenceBundleIndexRow",
    "FullSequenceBundleManifest",
    "FullSequenceBundleRecord",
    "FrozenUpstreamArtifact",
    "IctvArtifactManifest",
    "InclusionDecisionManifest",
    "InclusionDecisionRecord",
    "NcbiHistorySummary",
    "NcbiSequenceFetchWrapper",
    "NcbiTaxonomyArtifactManifest",
    "PublicAssertionMembershipManifest",
    "PublicAssertionMembershipRecord",
    "PublicLocusMembershipManifest",
    "PublicLocusMembershipRecord",
    "SelfHashedManifest",
    "StructuredActivationCounts",
    "StructuredActivationManifest",
    "StructuredAdjudicationManifest",
    "StudyFormalMappingManifest",
    "StudyFormalMappingRow",
    "TaxdumpMember",
    "TaxonomyAliasSpec",
    "TaxonomySnapshotManifest",
    "TaxonomySourceLocator",
    "TaxonomyTermSpec",
    "UsagePolicyEvidence",
    "canonical_model_sha256",
    "canonical_revalidate",
    "canonical_self_sha256",
    "seal_manifest_payload",
]
