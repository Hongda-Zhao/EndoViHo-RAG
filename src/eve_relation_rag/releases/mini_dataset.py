"""Portable, self-verifying public mini DatasetRelease artifacts.

The repository release is intentionally distinct from live PostgreSQL activation.  It is a
small public truth-layer package: every member binds one assembly-local interval to source,
flank, inclusion, NCBI-taxonomy, and ICTV evidence without assigning a relation class.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.activation.contracts import canonical_self_sha256

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCUS_KEY_RE = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")
_ASSEMBLY_RE = re.compile(r"^GC[AF]_[0-9]+\.[1-9][0-9]*$")
_CONTIG_RE = re.compile(r"^[A-Z]{1,6}[0-9]{5,}(?:\.[1-9][0-9]*)$")

type Sha256 = Annotated[str, Field(pattern=_SHA256_RE.pattern)]
type LocusKey = Annotated[str, Field(pattern=_LOCUS_KEY_RE.pattern)]
type AssemblyAccession = Annotated[str, Field(pattern=_ASSEMBLY_RE.pattern)]
type ContigAccession = Annotated[str, Field(pattern=_CONTIG_RE.pattern)]


class MiniReleaseSchema(BaseModel):
    """Strict, immutable base for the portable release contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AssemblySourceTaxon(MiniReleaseSchema):
    assembly_accession_version: AssemblyAccession
    assembly_status_at_snapshot: Literal["current"]
    ncbi_tax_id: int = Field(gt=0)
    organism_name: str = Field(min_length=1)
    host_family_name: str = Field(min_length=1)
    host_family_term_key: str = Field(min_length=1)
    ncbi_taxonomy_snapshot_key: str = Field(min_length=1)


class EveLocusOrReportedViralRegion(MiniReleaseSchema):
    locus_key: LocusKey
    contig_accession_version: ContigAccession
    coordinate_system: Literal["0-based-half-open"]
    start0: int = Field(ge=0)
    end0: int = Field(gt=0)
    length: int = Field(gt=0)
    placement_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end0 <= self.start0 or self.length != self.end0 - self.start0:
            raise ValueError("locus coordinates and length are inconsistent")
        return self


class ViralLineageAffinity(MiniReleaseSchema):
    source_label: Literal["Orthopolintovirales"]
    source_term_key: Literal["study-viral-major-taxon:orthopolintovirales"]
    source_role: Literal["study_defined"]
    formal_mapping_relation: Literal["renamed_to"]
    formal_label: Literal["Amphintovirales"]
    formal_term_key: str = Field(min_length=1)
    formal_rank: Literal["order"]
    ictv_snapshot_key: str = Field(min_length=1)
    mapping_key: str = Field(min_length=1)


class SourceAnnotations(MiniReleaseSchema):
    hcvr: Literal["Yes"]
    vr_type: Literal["Integration"]
    viral_major_taxon: Literal["Orthopolintovirales"]
    native_vr_token: str = Field(pattern=r"^vr[1-9][0-9]*$")
    annotated_viral_proportion: str = Field(min_length=1)
    unique_rate: str = Field(min_length=1)
    conserved_og: str = Field(min_length=1)
    busco_score: str = Field(min_length=1)


class FlankSideEvidence(MiniReleaseSchema):
    verdict: Literal["supported"]
    inspected_bp: Literal[20000]
    ambiguous_bp: Literal[0]
    ambiguity_fraction: Literal["0.000000"]
    longest_ambiguity_run: Literal[0]
    boundary_base: Literal["A", "C", "G", "T"]


class EvidenceAndSource(MiniReleaseSchema):
    source_snapshot_key: str = Field(min_length=1)
    source_artifact_sha256: Sha256
    source_worksheet: Literal["S3"]
    source_row: int = Field(ge=2)
    source_record_key: str = Field(min_length=1)
    detection_call_key: str = Field(min_length=1)
    ncbi_assembly_report_sha256: Sha256
    ncbi_assembly_report_line: int = Field(ge=1)
    ncbi_sequence_report_sha256: Sha256
    ncbi_sequence_report_line: int = Field(ge=1)
    flank_policy_key: Literal["policy:v0-flank-context-20000-v1"]
    flank_assessed_by: str = Field(min_length=1)
    flank_assessed_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*Z$")
    flank_record_sha256: Sha256
    flank_response_sha256: Sha256
    normalized_sequence_sha256: Sha256
    left_flank: FlankSideEvidence
    right_flank: FlankSideEvidence
    inclusion_policy_key: str = Field(min_length=1)
    inclusion_decision_sha256: Sha256
    inclusion_reason_codes: tuple[str, ...] = Field(min_length=1)


class MiniLocusRecord(MiniReleaseSchema):
    """One exact public member expressed through the four-dimensional v1 association."""

    record_schema_version: Literal["endoviho-mini-locus-v1"]
    record_sha256: Sha256
    release_key: str = Field(pattern=r"^release:endoviho-rag:mini:v1:[0-9]{8}:[0-9]{3}$")
    public_membership: Literal[True]
    inclusion_decision: Literal["include"]
    relation_class_assigned: Literal[False]
    assembly_source_taxon: AssemblySourceTaxon
    eve_locus_or_reported_viral_region: EveLocusOrReportedViralRegion
    viral_lineage_affinity: ViralLineageAffinity
    evidence_and_source: EvidenceAndSource
    source_annotations: SourceAnnotations

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.source_annotations.viral_major_taxon != self.viral_lineage_affinity.source_label:
            raise ValueError("source viral label and lineage affinity differ")
        if self.record_sha256 != canonical_self_sha256(self, "record_sha256"):
            raise ValueError("mini locus record checksum does not match")
        return self


class AuthorityArtifactBinding(MiniReleaseSchema):
    role: Literal["taxdump", "master_species_list", "corrected_virus_metadata_resource"]
    version: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: Sha256
    source_uri: str = Field(pattern=r"^https://")
    retrieved_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*Z$")
    license_key: str = Field(min_length=1)


class AuthoritySnapshotBinding(MiniReleaseSchema):
    authority: Literal["NCBI Taxonomy", "ICTV"]
    version: str = Field(min_length=1)
    snapshot_key: str = Field(min_length=1)
    manifest_sha256: Sha256
    coverage: str = Field(min_length=1)
    merged_history_included: bool
    deleted_history_included: bool
    artifacts: tuple[AuthorityArtifactBinding, ...] = Field(min_length=1)


class PublicMembershipFile(MiniReleaseSchema):
    path: Literal["loci.jsonl"]
    media_type: Literal["application/x-ndjson"]
    byte_size: int = Field(gt=0)
    sha256: Sha256
    record_count: int = Field(gt=0)


class SourcePublicationBinding(MiniReleaseSchema):
    authors_short: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doi: str = Field(pattern=r"^10\.")
    version: str = Field(min_length=1)
    posted_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    artifact_label: str = Field(min_length=1)
    artifact_filename: str = Field(min_length=1)
    worksheet: Literal["S3"]
    artifact_sha256: Sha256
    license_key: str = Field(min_length=1)
    provenance_uri: str = Field(pattern=r"^https://")


class MiniReleaseCounts(MiniReleaseSchema):
    assemblies: int = Field(ge=3, le=5)
    loci: int = Field(ge=10, le=30)
    host_lineages: int = Field(ge=1, le=2)
    source_viral_lineages: int = Field(ge=1, le=3)
    formal_viral_lineages: int = Field(ge=1, le=3)
    include: int = Field(ge=10, le=30)
    exclude: Literal[0]
    review: Literal[0]


class MiniDatasetReleaseManifest(MiniReleaseSchema):
    """Top-level public artifact manifest; live database activation remains separate."""

    manifest_schema_version: Literal["endoviho-mini-dataset-release-v1"]
    manifest_sha256: Sha256
    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: str = Field(pattern=r"^release:endoviho-rag:mini:v1:[0-9]{8}:[0-9]{3}$")
    title: str = Field(min_length=1)
    release_status: Literal["published"]
    publication_channel: Literal["version-controlled-portable-release"]
    published_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*Z$")
    database_activation_status: Literal["not_performed"]
    coordinate_system: Literal["0-based-half-open"]
    selection_policy_key: Literal["policy:mini-v1-source-high-full-flanks-v1"]
    inclusion_policy_key: Literal["policy:v0-pilot-inclusion-v1"]
    relation_class_policy: Literal[
        "HCVR and VR Type are source annotations; no Transferred gene or "
        "Integrated virus class is assigned"
    ]
    scope_statement: str = Field(min_length=1)
    host_lineage: Literal["Unionidae"]
    source_viral_lineage: Literal["Orthopolintovirales"]
    formal_viral_lineage: Literal["Amphintovirales"]
    source_manifest_sha256: Sha256
    source_audit_sha256: Sha256
    source: SourcePublicationBinding
    parent_cohort_manifest_sha256: Sha256
    parent_flank_manifest_sha256: Sha256
    parent_inclusion_manifest_sha256: Sha256
    authority_snapshots: tuple[AuthoritySnapshotBinding, AuthoritySnapshotBinding]
    counts: MiniReleaseCounts
    public_membership_file: PublicMembershipFile
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if tuple(binding.authority for binding in self.authority_snapshots) != (
            "NCBI Taxonomy",
            "ICTV",
        ):
            raise ValueError("authority snapshots must be ordered NCBI Taxonomy then ICTV")
        if self.counts.loci != self.public_membership_file.record_count:
            raise ValueError("manifest locus count differs from membership file")
        if self.counts.include != self.counts.loci:
            raise ValueError("every published mini member must have an include decision")
        if self.manifest_sha256 != canonical_self_sha256(self, "manifest_sha256"):
            raise ValueError("mini DatasetRelease manifest checksum does not match")
        return self


@dataclass(frozen=True, slots=True)
class LoadedMiniDatasetRelease:
    manifest: MiniDatasetReleaseManifest
    loci: tuple[MiniLocusRecord, ...]


def load_mini_dataset_release(release_root: str | Path) -> LoadedMiniDatasetRelease:
    """Load and cross-check one portable mini DatasetRelease directory."""

    root = Path(release_root)
    manifest_path = root / "dataset_release.json"
    manifest = MiniDatasetReleaseManifest.model_validate_json(manifest_path.read_bytes())
    loci_path = root / manifest.public_membership_file.path
    loci_bytes = loci_path.read_bytes()
    if len(loci_bytes) != manifest.public_membership_file.byte_size:
        raise ValueError("public membership file byte size differs from manifest")
    if hashlib.sha256(loci_bytes).hexdigest() != manifest.public_membership_file.sha256:
        raise ValueError("public membership file checksum differs from manifest")

    loci = tuple(
        MiniLocusRecord.model_validate_json(line)
        for line in loci_bytes.splitlines()
        if line.strip()
    )
    if len(loci) != manifest.counts.loci:
        raise ValueError("public membership record count differs from manifest")
    if any(row.release_key != manifest.release_key for row in loci):
        raise ValueError("public member belongs to another release")

    locus_keys = tuple(row.eve_locus_or_reported_viral_region.locus_key for row in loci)
    source_rows = tuple(row.evidence_and_source.source_row for row in loci)
    if len(locus_keys) != len(set(locus_keys)) or len(source_rows) != len(set(source_rows)):
        raise ValueError("public membership contains duplicate locus or source-row identity")
    if locus_keys != tuple(sorted(locus_keys)):
        raise ValueError("public membership records are not canonically ordered by locus key")

    assemblies = {row.assembly_source_taxon.assembly_accession_version for row in loci}
    host_lineages = {row.assembly_source_taxon.host_family_name for row in loci}
    source_viral = {row.viral_lineage_affinity.source_label for row in loci}
    formal_viral = {row.viral_lineage_affinity.formal_label for row in loci}
    observed_counts = (
        len(assemblies),
        len(loci),
        len(host_lineages),
        len(source_viral),
        len(formal_viral),
    )
    expected_counts = (
        manifest.counts.assemblies,
        manifest.counts.loci,
        manifest.counts.host_lineages,
        manifest.counts.source_viral_lineages,
        manifest.counts.formal_viral_lineages,
    )
    if observed_counts != expected_counts:
        raise ValueError("public membership diversity counts differ from manifest")
    return LoadedMiniDatasetRelease(manifest=manifest, loci=loci)


def compact_json_line(value: BaseModel) -> bytes:
    """Serialize one validated model as deterministic sorted compact JSONL bytes."""

    payload = value.model_dump(mode="json")
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


__all__ = [
    "AssemblySourceTaxon",
    "AuthorityArtifactBinding",
    "AuthoritySnapshotBinding",
    "EvidenceAndSource",
    "EveLocusOrReportedViralRegion",
    "FlankSideEvidence",
    "LoadedMiniDatasetRelease",
    "MiniDatasetReleaseManifest",
    "MiniLocusRecord",
    "MiniReleaseCounts",
    "PublicMembershipFile",
    "SourceAnnotations",
    "SourcePublicationBinding",
    "ViralLineageAffinity",
    "compact_json_line",
    "load_mini_dataset_release",
]
