"""Checksum-first NCBI/ICTV taxonomy loaders and existing-schema importers."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tarfile
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    APPROVED_ASSEMBLIES,
    AssemblyTaxonAssignmentManifest,
    AssemblyTaxonAssignmentSpec,
    FrozenUpstreamArtifact,
    IctvArtifactManifest,
    NcbiHistorySummary,
    NcbiTaxonomyArtifactManifest,
    StudyFormalMappingManifest,
    StudyFormalMappingRow,
    TaxdumpMember,
    TaxonomyAliasSpec,
    TaxonomySnapshotManifest,
    TaxonomySourceLocator,
    TaxonomyTermSpec,
    canonical_model_sha256,
    canonical_revalidate,
    seal_manifest_payload,
)
from eve_relation_rag.db.models import (
    AssemblyTaxonAssignment,
    DatasetRelease,
    GenomeAssembly,
    LineageAlias,
    LineageClosure,
    LineageSnapshot,
    LineageTerm,
    ReleaseAssemblyMembership,
    ReleaseLineageSnapshot,
    ReleaseSourceSnapshot,
    SourceArtifact,
    SourceSnapshot,
)
from eve_relation_rag.domain.keys import stable_key
from eve_relation_rag.importers.data_s1 import verify_file_bytes

type TaxdumpFilename = Literal["delnodes.dmp", "merged.dmp", "names.dmp", "nodes.dmp"]
_REQUIRED_TAXDUMP_MEMBERS: tuple[TaxdumpFilename, ...] = (
    "delnodes.dmp",
    "merged.dmp",
    "names.dmp",
    "nodes.dmp",
)
_ICTV_RANK_COLUMNS = (
    "Realm",
    "Subrealm",
    "Kingdom",
    "Subkingdom",
    "Phylum",
    "Subphylum",
    "Class",
    "Subclass",
    "Order",
    "Suborder",
    "Family",
    "Subfamily",
    "Genus",
    "Subgenus",
    "Species",
)
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_XLSX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_CELL_REFERENCE_RE = re.compile(r"^([A-Z]+)[1-9][0-9]*$")
# Six-column term batches stay below PostgreSQL's 65,535 bind-parameter ceiling.
_MULTIROW_INSERT_BATCH_SIZE = 10_000


class TaxonomyArtifactError(ValueError):
    """Raised when frozen taxonomy bytes or their parsed semantics drift."""


class TaxonomyImportError(RuntimeError):
    """Raised when an exact taxonomy snapshot cannot be staged idempotently."""


@dataclass(frozen=True, slots=True)
class LoadedNcbiTaxonomy:
    manifest: TaxonomySnapshotManifest
    resolved_tax_ids: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class TaxonomyImportReport:
    release_key: str
    snapshot_key: str
    snapshot_manifest_sha256: str
    term_count: int
    alias_count: int
    closure_count: int
    assignment_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class MappingValidationReport:
    release_key: str
    manifest_sha256: str
    mapping_count: int
    study_snapshot_key: str
    formal_snapshot_key: str


@dataclass(frozen=True, slots=True)
class _NcbiNode:
    tax_id: int
    parent_tax_id: int
    rank: str
    line_number: int


@dataclass(frozen=True, slots=True)
class _XlsxRow:
    row_number: int
    values: Mapping[str, str]


def observe_taxdump_members(path: str | Path) -> tuple[TaxdumpMember, ...]:
    """Measure the four required members without extracting them to disk."""

    archive_path = Path(path)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = _required_tar_members(archive)
            observed = tuple(
                _observe_tar_member(archive, members[filename], filename)
                for filename in _REQUIRED_TAXDUMP_MEMBERS
            )
    except (OSError, tarfile.TarError) as exc:
        raise TaxonomyArtifactError("NCBI taxdump archive is unreadable") from exc
    return observed


def build_ncbi_taxonomy_artifact_manifest(
    archive_path: str | Path,
    *,
    expected_sha256: str,
    expected_byte_size: int,
    upstream_md5: str,
    version: str,
    source_uri: str,
    checksum_source_uri: str,
    retrieved_at: str,
    usage_policy_source_uri: str,
    usage_policy_retrieved_at: str,
    usage_policy_capture_path: str | Path,
    expected_usage_policy_sha256: str,
) -> NcbiTaxonomyArtifactManifest:
    """Build an exact NCBI taxdump artifact manifest from local frozen bytes.

    The publisher MD5 and the locally approved SHA-256/size must all match.  The
    usage-policy hash is measured from a separate local capture, so callers cannot
    substitute a bare digest without retaining the evidence it identifies.
    """

    artifact = _build_frozen_artifact(
        archive_path,
        artifact_prefix="source-artifact:ncbi-taxonomy",
        media_type="application/gzip",
        expected_sha256=expected_sha256,
        expected_byte_size=expected_byte_size,
        source_uri=source_uri,
        retrieved_at=retrieved_at,
        license_key="NCBI-PUBLIC-DOMAIN-US-GOVERNMENT-WORK",
        upstream_checksum_algorithm="md5",
        upstream_checksum=upstream_md5,
        checksum_source_uri=checksum_source_uri,
    )
    if artifact.filename != "taxdump.tar.gz":
        raise TaxonomyArtifactError("NCBI taxonomy archive must use the canonical filename")
    usage = verify_file_bytes(
        usage_policy_capture_path,
        expected_sha256=expected_usage_policy_sha256,
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "ncbi-taxonomy-artifact-manifest-v1",
        "snapshot_key": stable_key(
            "lineage-snapshot:ncbi-taxonomy",
            {"archive_sha256": artifact.sha256, "filename": artifact.filename},
        ),
        "authority_namespace": "ncbi-taxonomy",
        "version": version,
        "archive": artifact,
        "members": observe_taxdump_members(archive_path),
        "usage_policy": {
            "usage_basis_key": "NCBI-MOLECULAR-DATA-USAGE-POLICY",
            "source_uri": usage_policy_source_uri,
            "retrieved_at": usage_policy_retrieved_at,
            "local_capture_sha256": usage.sha256,
        },
    }
    return NcbiTaxonomyArtifactManifest.model_validate(seal_manifest_payload(payload))


def build_ictv_artifact_manifest(
    *,
    msl_path: str | Path,
    corrected_vmr_path: str | Path,
    expected_msl_sha256: str,
    expected_msl_byte_size: int,
    expected_vmr_sha256: str,
    expected_vmr_byte_size: int,
    msl_source_uri: str,
    vmr_source_uri: str,
    retrieved_at: str,
    usage_policy_source_uri: str,
    usage_policy_retrieved_at: str,
    usage_policy_capture_path: str | Path,
    expected_usage_policy_sha256: str,
    msl_upstream_sha256: str | None = None,
    msl_checksum_source_uri: str | None = None,
    vmr_upstream_sha256: str | None = None,
    vmr_checksum_source_uri: str | None = None,
) -> IctvArtifactManifest:
    """Build the exact MSL41/corrected-VMR package without inventing checksums.

    ICTV does not have to publish a checksum for this builder to retain the local
    SHA-256.  If a publisher checksum is supplied, its source URI is mandatory and
    the bytes must match it.  Otherwise the manifest explicitly records that remote
    checksum verification was unavailable.
    """

    msl = _build_frozen_artifact(
        msl_path,
        artifact_prefix="source-artifact:ictv-msl41",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        expected_sha256=expected_msl_sha256,
        expected_byte_size=expected_msl_byte_size,
        source_uri=msl_source_uri,
        retrieved_at=retrieved_at,
        license_key="CC-BY-4.0",
        upstream_checksum_algorithm=("sha256" if msl_upstream_sha256 is not None else None),
        upstream_checksum=msl_upstream_sha256,
        checksum_source_uri=msl_checksum_source_uri,
    )
    vmr = _build_frozen_artifact(
        corrected_vmr_path,
        artifact_prefix="source-artifact:ictv-vmr-msl41",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        expected_sha256=expected_vmr_sha256,
        expected_byte_size=expected_vmr_byte_size,
        source_uri=vmr_source_uri,
        retrieved_at=retrieved_at,
        license_key="CC-BY-4.0",
        upstream_checksum_algorithm=("sha256" if vmr_upstream_sha256 is not None else None),
        upstream_checksum=vmr_upstream_sha256,
        checksum_source_uri=vmr_checksum_source_uri,
    )
    usage = verify_file_bytes(
        usage_policy_capture_path,
        expected_sha256=expected_usage_policy_sha256,
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "ictv-msl41-artifact-manifest-v1",
        "snapshot_key": stable_key(
            "lineage-snapshot:ictv-msl41",
            {
                "msl_sha256": msl.sha256,
                "vmr_revision": "MSL41.v1.20260729",
                "vmr_sha256": vmr.sha256,
            },
        ),
        "authority_namespace": "ictv",
        "msl_version": "MSL41 v1",
        "msl": msl,
        "corrected_vmr": vmr,
        "vmr_revision": "MSL41.v1.20260729",
        "usage_policy": {
            "usage_basis_key": "ICTV-CC-BY-4.0",
            "source_uri": usage_policy_source_uri,
            "retrieved_at": usage_policy_retrieved_at,
            "local_capture_sha256": usage.sha256,
        },
    }
    return IctvArtifactManifest.model_validate(seal_manifest_payload(payload))


def build_assembly_taxon_assignment_manifest(
    ncbi_snapshot: LoadedNcbiTaxonomy,
    *,
    assembly_report_path: str | Path,
    expected_assembly_report_sha256: str,
    expected_assembly_report_byte_size: int,
    assembly_report_artifact_key: str,
) -> AssemblyTaxonAssignmentManifest:
    """Bind all ten assembly-report TaxIds to their exact resolved NCBI terms."""

    snapshot = canonical_revalidate(ncbi_snapshot.manifest)
    if snapshot.authority_namespace != "ncbi-taxonomy":
        raise TaxonomyArtifactError("assembly assignments require an NCBI snapshot")
    snapshot_term_keys = {row.term_key for row in snapshot.terms}
    observation = verify_file_bytes(
        assembly_report_path,
        expected_sha256=expected_assembly_report_sha256,
        expected_byte_size=expected_assembly_report_byte_size,
    )
    assignments: dict[str, AssemblyTaxonAssignmentSpec] = {}
    for accession, reported_tax_id, line_number in _approved_assembly_taxon_rows(observation.path):
        resolved_tax_id = ncbi_snapshot.resolved_tax_ids.get(reported_tax_id)
        if resolved_tax_id is None:
            raise TaxonomyArtifactError(
                "assembly report TaxId was not resolved by the frozen taxdump"
            )
        if f"ncbi-taxonomy:taxid:{resolved_tax_id}" not in snapshot_term_keys:
            raise TaxonomyArtifactError("resolved assembly TaxId is absent from snapshot")
        assignments[accession] = AssemblyTaxonAssignmentSpec(
            assembly_accession_version=accession,
            reported_ncbi_tax_id=reported_tax_id,
            resolved_ncbi_tax_id=resolved_tax_id,
            term_key=f"ncbi-taxonomy:taxid:{resolved_tax_id}",
            assignment_policy_key="ncbi-taxdump-assembly-taxid-v1",
            source_artifact_key=assembly_report_artifact_key,
            source_locator=f"{observation.path.name}:line:{line_number}:$.organism.tax_id",
        )
    if set(assignments) != set(APPROVED_ASSEMBLIES):
        raise TaxonomyArtifactError("assembly report must contain exactly the ten assemblies")
    payload: dict[str, object] = {
        "manifest_schema_version": "assembly-taxon-assignment-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "ncbi_snapshot_manifest_sha256": snapshot.manifest_sha256,
        "assignments": tuple(assignments[key] for key in APPROVED_ASSEMBLIES),
    }
    return AssemblyTaxonAssignmentManifest.model_validate(seal_manifest_payload(payload))


def load_approved_assembly_tax_ids(
    assembly_report_path: str | Path,
    *,
    expected_sha256: str,
    expected_byte_size: int,
) -> Mapping[str, int]:
    """Read the ten exact assembly TaxIds from the checksum-frozen M1 JSONL report."""

    observation = verify_file_bytes(
        assembly_report_path,
        expected_sha256=expected_sha256,
        expected_byte_size=expected_byte_size,
    )
    return MappingProxyType(
        {
            accession: tax_id
            for accession, tax_id, _ in _approved_assembly_taxon_rows(observation.path)
        }
    )


def build_polintovirus_rename_mapping_manifest(
    formal_snapshot: TaxonomySnapshotManifest,
    *,
    study_snapshot_key: str,
    study_term_keys: Mapping[str, str],
    evidence_artifact_sha256: str,
    evidence_locator: str,
) -> StudyFormalMappingManifest:
    """Build the proposal-backed historical-name mappings, never synonyms.

    The caller supplies exact study term keys.  Formal endpoints are selected by the
    two names and ranks explicitly approved from ICTV proposal 2024.010D; no fuzzy or
    same-string inference is performed.  The old order is mandatory.  The old family
    is mapped only when that term actually exists in the study-defined snapshot.
    """

    formal = canonical_revalidate(formal_snapshot)
    if formal.authority_namespace != "ictv" or formal.coverage != "complete-msl41-hierarchy":
        raise TaxonomyArtifactError("rename mapping requires the complete MSL41 snapshot")
    approved_renames = {
        "Adintoviridae": ("Eupolintoviridae", "family"),
        "Orthopolintovirales": ("Amphintovirales", "order"),
    }
    if "Orthopolintovirales" not in study_term_keys or not set(study_term_keys).issubset(
        approved_renames
    ):
        raise TaxonomyArtifactError("the exact old-order key and only approved renames are allowed")
    rows: list[StudyFormalMappingRow] = []
    for old_name in sorted(study_term_keys):
        current_name, rank = approved_renames[old_name]
        matches = tuple(
            row for row in formal.terms if row.canonical_name == current_name and row.rank == rank
        )
        if len(matches) != 1:
            raise TaxonomyArtifactError(
                f"MSL41 must contain one exact {rank} endpoint for {current_name}"
            )
        rows.append(
            StudyFormalMappingRow(
                mapping_key=stable_key(
                    "study-formal-mapping",
                    {
                        "formal_snapshot_key": formal.snapshot_key,
                        "formal_term_key": matches[0].term_key,
                        "relation": "renamed_to",
                        "study_snapshot_key": study_snapshot_key,
                        "study_term_key": study_term_keys[old_name],
                    },
                ),
                study_snapshot_key=study_snapshot_key,
                study_term_key=study_term_keys[old_name],
                formal_snapshot_key=formal.snapshot_key,
                formal_term_key=matches[0].term_key,
                relation="renamed_to",
                curation_method_key="curation:ictv-proposal-2024.010D",
                evidence_artifact_sha256=evidence_artifact_sha256,
                evidence_locator=f"{evidence_locator}: {old_name} -> {current_name}",
            )
        )
    canonical_rows = tuple(sorted(rows, key=lambda row: (row.study_term_key, row.formal_term_key)))
    payload: dict[str, object] = {
        "manifest_schema_version": "study-formal-mapping-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "study_snapshot_key": study_snapshot_key,
        "formal_snapshot_key": formal.snapshot_key,
        "formal_snapshot_manifest_sha256": formal.manifest_sha256,
        "mappings": canonical_rows,
    }
    return StudyFormalMappingManifest.model_validate(seal_manifest_payload(payload))


def load_ncbi_taxonomy_snapshot(
    manifest: NcbiTaxonomyArtifactManifest,
    archive_path: str | Path,
    *,
    required_tax_ids: Iterable[int],
) -> LoadedNcbiTaxonomy:
    """Load required host taxa plus ancestors while binding complete history files."""

    manifest = canonical_revalidate(manifest)
    _verify_artifact_file(manifest.archive, archive_path)
    observed_members = observe_taxdump_members(archive_path)
    if observed_members != manifest.members:
        raise TaxonomyArtifactError("taxdump member byte identities differ from the manifest")

    requested = tuple(sorted(set(required_tax_ids)))
    if not requested or any(type(tax_id) is not int or tax_id <= 0 for tax_id in requested):
        raise TaxonomyArtifactError("required_tax_ids must contain positive exact integers")

    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = _required_tar_members(archive)
        merged_rows = _parse_merged(archive, members["merged.dmp"])
        deleted_rows = _parse_deleted(archive, members["delnodes.dmp"])
        merged = dict(merged_rows)
        deleted = set(deleted_rows)
        resolved = {tax_id: _resolve_tax_id(tax_id, merged, deleted) for tax_id in requested}
        nodes = _parse_nodes(archive, members["nodes.dmp"])
        selected_ids = _selected_ancestors(tuple(resolved.values()), nodes)
        names = _parse_selected_names(archive, members["names.dmp"], selected_ids)

    terms: list[TaxonomyTermSpec] = []
    for tax_id in sorted(selected_ids):
        node = nodes[tax_id]
        canonical_name, aliases = names.get(tax_id, (None, ()))
        if canonical_name is None:
            raise TaxonomyArtifactError(f"selected TaxId {tax_id} lacks a scientific name")
        parent_key = (
            None if node.parent_tax_id == tax_id else f"ncbi-taxonomy:taxid:{node.parent_tax_id}"
        )
        terms.append(
            TaxonomyTermSpec(
                term_key=f"ncbi-taxonomy:taxid:{tax_id}",
                canonical_name=canonical_name,
                rank=node.rank,
                authority_local_id=str(tax_id),
                parent_term_key=parent_key,
                source_locator=TaxonomySourceLocator(
                    artifact_key=manifest.archive.artifact_key,
                    member_name="nodes.dmp",
                    worksheet=None,
                    row_number=node.line_number,
                ),
                aliases=aliases,
            )
        )

    payload: dict[str, object] = {
        "manifest_schema_version": "taxonomy-snapshot-manifest-v1",
        "snapshot_key": manifest.snapshot_key,
        "domain": "host",
        "scheme_kind": "formal_taxonomy",
        "authority_namespace": "ncbi-taxonomy",
        "version": manifest.version,
        "release_role": "assembly_source_taxonomy",
        "artifact_manifest_sha256": manifest.manifest_sha256,
        "primary_artifact_key": manifest.archive.artifact_key,
        "coverage": "required-taxa-and-ancestors-complete-history-bound",
        "terms": tuple(sorted(terms, key=lambda row: row.term_key)),
        "ncbi_history": NcbiHistorySummary(
            merged_tax_id_count=len(merged_rows),
            deleted_tax_id_count=len(deleted_rows),
            merged_rows_sha256=canonical_model_sha256(merged_rows),
            deleted_rows_sha256=canonical_model_sha256(deleted_rows),
        ),
    }
    snapshot = TaxonomySnapshotManifest.model_validate(seal_manifest_payload(payload))
    return LoadedNcbiTaxonomy(
        manifest=snapshot,
        resolved_tax_ids=MappingProxyType(resolved),
    )


def load_ictv_taxonomy_snapshot(
    manifest: IctvArtifactManifest,
    *,
    msl_path: str | Path,
    corrected_vmr_path: str | Path,
) -> TaxonomySnapshotManifest:
    """Load the complete MSL41 hierarchy and verify the corrected VMR coverage."""

    manifest = canonical_revalidate(manifest)
    _verify_artifact_file(manifest.msl, msl_path)
    _verify_artifact_file(manifest.corrected_vmr, corrected_vmr_path)
    msl_rows = tuple(_xlsx_table(msl_path, "MSL"))
    if not msl_rows:
        raise TaxonomyArtifactError("ICTV MSL worksheet has no taxonomy rows")
    required_headers = {*_ICTV_RANK_COLUMNS, "ICTV_ID"}
    if not required_headers.issubset(msl_rows[0].values):
        raise TaxonomyArtifactError("ICTV MSL worksheet is missing required columns")

    terms_by_key: dict[str, TaxonomyTermSpec] = {}
    msl_species_ids: set[str] = set()
    for row in msl_rows:
        parent_key: str | None = None
        for header in _ICTV_RANK_COLUMNS:
            name = _clean_cell(row.values.get(header, ""))
            if not name:
                continue
            rank = header.casefold()
            authority_local_id: str | None = None
            if header == "Species":
                authority_local_id = _clean_cell(row.values.get("ICTV_ID", ""))
                if not authority_local_id:
                    raise TaxonomyArtifactError(f"ICTV species row {row.row_number} lacks ICTV_ID")
                msl_species_ids.add(authority_local_id)
            term_key = stable_key(
                "lineage-term:ictv-msl41",
                {
                    "authority_local_id": authority_local_id,
                    "canonical_name": name,
                    "parent_term_key": parent_key,
                    "rank": rank,
                    "snapshot_key": manifest.snapshot_key,
                },
            )
            candidate = TaxonomyTermSpec(
                term_key=term_key,
                canonical_name=name,
                rank=rank,
                authority_local_id=authority_local_id,
                parent_term_key=parent_key,
                source_locator=TaxonomySourceLocator(
                    artifact_key=manifest.msl.artifact_key,
                    member_name=None,
                    worksheet="MSL",
                    row_number=row.row_number,
                ),
                aliases=(),
            )
            existing = terms_by_key.setdefault(term_key, candidate)
            if (
                existing.canonical_name != candidate.canonical_name
                or existing.rank != candidate.rank
                or existing.parent_term_key != candidate.parent_term_key
                or existing.authority_local_id != candidate.authority_local_id
            ):
                raise TaxonomyArtifactError("ICTV term-key collision")
            parent_key = term_key

    order_names = {row.canonical_name for row in terms_by_key.values() if row.rank == "order"}
    if "Orthopolintovirales" in order_names or "Amphintovirales" not in order_names:
        raise TaxonomyArtifactError(
            "MSL41 must contain current Amphintovirales and not old Orthopolintovirales"
        )

    vmr_rows = tuple(_xlsx_table(corrected_vmr_path, "VMR MSL41"))
    if not vmr_rows or "ICTV_ID" not in vmr_rows[0].values:
        raise TaxonomyArtifactError("corrected VMR worksheet is missing ICTV_ID")
    vmr_species_ids = {
        value for row in vmr_rows if (value := _clean_cell(row.values.get("ICTV_ID", "")))
    }
    missing_vmr = msl_species_ids.difference(vmr_species_ids)
    if missing_vmr:
        raise TaxonomyArtifactError(
            f"corrected VMR lacks {len(missing_vmr)} MSL41 species identifiers"
        )

    payload: dict[str, object] = {
        "manifest_schema_version": "taxonomy-snapshot-manifest-v1",
        "snapshot_key": manifest.snapshot_key,
        "domain": "viral",
        "scheme_kind": "formal_taxonomy",
        "authority_namespace": "ictv",
        "version": manifest.msl_version,
        "release_role": "formal_viral_taxonomy",
        "artifact_manifest_sha256": manifest.manifest_sha256,
        "primary_artifact_key": manifest.msl.artifact_key,
        "coverage": "complete-msl41-hierarchy",
        "terms": tuple(sorted(terms_by_key.values(), key=lambda row: row.term_key)),
        "ncbi_history": None,
    }
    return TaxonomySnapshotManifest.model_validate(seal_manifest_payload(payload))


def import_taxonomy_snapshot(
    session: Session,
    *,
    artifact_manifest: NcbiTaxonomyArtifactManifest | IctvArtifactManifest,
    snapshot_manifest: TaxonomySnapshotManifest,
    assignment_manifest: AssemblyTaxonAssignmentManifest | None = None,
    release_key: str = ACTIVATION_RELEASE_KEY,
    replace_candidate_placeholder: bool = False,
) -> TaxonomyImportReport:
    """Atomically stage one exact taxonomy snapshot into the existing M1 schema.

    The function never commits and never promotes the release.  Callers own the
    surrounding transaction.  Exact replay is idempotent; partial or drifting state
    fails instead of updating rows in place.
    """

    artifact_manifest = canonical_revalidate(artifact_manifest)
    snapshot_manifest = canonical_revalidate(snapshot_manifest)
    if assignment_manifest is not None:
        assignment_manifest = canonical_revalidate(assignment_manifest)
    if release_key != ACTIVATION_RELEASE_KEY:
        raise TaxonomyImportError("the approved activation release key is required")
    release = session.scalar(
        select(DatasetRelease).where(DatasetRelease.release_key == release_key).with_for_update()
    )
    if release is None or release.status != "candidate":
        raise TaxonomyImportError("taxonomy import requires the exact candidate release")
    if snapshot_manifest.artifact_manifest_sha256 != artifact_manifest.manifest_sha256:
        raise TaxonomyImportError("taxonomy snapshot does not bind the artifact manifest")

    source_snapshot, artifacts = _stage_source_artifacts(session, artifact_manifest)
    primary_artifact = artifacts.get(snapshot_manifest.primary_artifact_key)
    if primary_artifact is None:
        raise TaxonomyImportError("taxonomy primary artifact is absent from source package")

    snapshot, created = _stage_lineage_snapshot(
        session,
        snapshot_manifest,
        primary_artifact=primary_artifact,
    )
    _bind_release_source(session, release, source_snapshot, snapshot_manifest.release_role)
    _bind_release_lineage(
        session,
        release,
        snapshot,
        snapshot_manifest,
        replace_candidate_placeholder=replace_candidate_placeholder,
    )
    term_by_key, alias_count, closure_count = _stage_terms(session, snapshot, snapshot_manifest)
    assignment_count = _stage_assignments(
        session,
        release,
        snapshot_manifest,
        term_by_key,
        artifacts,
        assignment_manifest,
    )
    return TaxonomyImportReport(
        release_key=release_key,
        snapshot_key=snapshot.snapshot_key,
        snapshot_manifest_sha256=snapshot_manifest.manifest_sha256,
        term_count=len(term_by_key),
        alias_count=alias_count,
        closure_count=closure_count,
        assignment_count=assignment_count,
        created=created,
    )


def validate_study_formal_mapping(
    session: Session,
    manifest: StudyFormalMappingManifest,
) -> MappingValidationReport:
    """Resolve every explicit mapping endpoint; never infer a row from names."""

    manifest = canonical_revalidate(manifest)
    if manifest.release_key != ACTIVATION_RELEASE_KEY:
        raise TaxonomyImportError("mapping manifest belongs to a different release")
    snapshots = {
        row.snapshot_key: row
        for row in session.scalars(
            select(LineageSnapshot).where(
                LineageSnapshot.snapshot_key.in_(
                    (manifest.study_snapshot_key, manifest.formal_snapshot_key)
                )
            )
        ).all()
    }
    if set(snapshots) != {manifest.study_snapshot_key, manifest.formal_snapshot_key}:
        raise TaxonomyImportError("mapping snapshot endpoint is absent")
    if snapshots[manifest.study_snapshot_key].scheme_kind != "study_defined":
        raise TaxonomyImportError("mapping source is not a study-defined snapshot")
    if (
        snapshots[manifest.formal_snapshot_key].scheme_kind != "formal_taxonomy"
        or snapshots[manifest.formal_snapshot_key].domain != "viral"
        or snapshots[manifest.formal_snapshot_key].snapshot_sha256
        != manifest.formal_snapshot_manifest_sha256
    ):
        raise TaxonomyImportError("mapping target is not the exact formal viral snapshot")

    terms_by_snapshot: dict[str, set[str]] = {}
    for snapshot_key, snapshot in snapshots.items():
        terms_by_snapshot[snapshot_key] = set(
            session.scalars(
                select(LineageTerm.term_key).where(LineageTerm.snapshot_id == snapshot.id)
            ).all()
        )
    for row in manifest.mappings:
        if row.study_term_key not in terms_by_snapshot[manifest.study_snapshot_key]:
            raise TaxonomyImportError("mapping study term is absent")
        if row.formal_term_key not in terms_by_snapshot[manifest.formal_snapshot_key]:
            raise TaxonomyImportError("mapping formal term is absent")
    return MappingValidationReport(
        release_key=manifest.release_key,
        manifest_sha256=manifest.manifest_sha256,
        mapping_count=len(manifest.mappings),
        study_snapshot_key=manifest.study_snapshot_key,
        formal_snapshot_key=manifest.formal_snapshot_key,
    )


def _verify_artifact_file(artifact: FrozenUpstreamArtifact, path: str | Path) -> None:
    observation = verify_file_bytes(
        path,
        expected_sha256=artifact.sha256,
        expected_byte_size=artifact.byte_size,
    )
    if Path(path).name != artifact.filename:
        raise TaxonomyArtifactError("artifact filename differs from the approved manifest")
    if observation.sha256 != artifact.sha256:
        raise TaxonomyArtifactError("artifact checksum verification failed")


def _approved_assembly_taxon_rows(path: Path) -> tuple[tuple[str, int, int], ...]:
    rows: dict[str, tuple[str, int, int]] = {}
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise TaxonomyArtifactError("assembly report row must be a JSON object")
                accession = raw.get("accession")
                organism = raw.get("organism")
                if accession not in APPROVED_ASSEMBLIES or not isinstance(organism, dict):
                    raise TaxonomyArtifactError("assembly report contains an unapproved row")
                reported_tax_id = organism.get("tax_id")
                if type(reported_tax_id) is not int or reported_tax_id <= 0:
                    raise TaxonomyArtifactError("assembly report TaxId is not a positive integer")
                if accession in rows:
                    raise TaxonomyArtifactError("assembly report contains a duplicate accession")
                assert isinstance(accession, str)
                rows[accession] = (accession, reported_tax_id, line_number)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TaxonomyArtifactError("assembly report is unreadable or invalid JSONL") from exc
    if set(rows) != set(APPROVED_ASSEMBLIES):
        raise TaxonomyArtifactError("assembly report must contain exactly the ten assemblies")
    return tuple(rows[key] for key in APPROVED_ASSEMBLIES)


def _build_frozen_artifact(
    path: str | Path,
    *,
    artifact_prefix: str,
    media_type: str,
    expected_sha256: str,
    expected_byte_size: int,
    source_uri: str,
    retrieved_at: str,
    license_key: str,
    upstream_checksum_algorithm: Literal["sha256", "md5"] | None,
    upstream_checksum: str | None,
    checksum_source_uri: str | None,
) -> FrozenUpstreamArtifact:
    observation = verify_file_bytes(
        path,
        expected_sha256=expected_sha256,
        expected_byte_size=expected_byte_size,
    )
    supplied = (
        upstream_checksum_algorithm is not None,
        upstream_checksum is not None,
        checksum_source_uri is not None,
    )
    if any(supplied) and not all(supplied):
        raise TaxonomyArtifactError("upstream checksum provenance must be complete")
    if upstream_checksum_algorithm == "sha256":
        actual_upstream_digest = observation.sha256
    elif upstream_checksum_algorithm == "md5":
        actual_upstream_digest = _stream_md5(observation.path)
    else:
        actual_upstream_digest = None
    if actual_upstream_digest != upstream_checksum:
        raise TaxonomyArtifactError("local artifact differs from the upstream checksum")
    return FrozenUpstreamArtifact(
        artifact_key=stable_key(
            artifact_prefix,
            {"filename": observation.path.name, "sha256": observation.sha256},
        ),
        filename=observation.path.name,
        media_type=media_type,
        byte_size=observation.byte_size,
        sha256=observation.sha256,
        upstream_checksum_algorithm=upstream_checksum_algorithm,
        upstream_checksum=upstream_checksum,
        upstream_checksum_verified=upstream_checksum_algorithm is not None,
        source_uri=source_uri,
        checksum_source_uri=checksum_source_uri,
        retrieved_at=retrieved_at,
        license_key=license_key,
    )


def _stream_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise TaxonomyArtifactError("cannot verify upstream MD5") from exc
    return digest.hexdigest()


def _required_tar_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        basename = PurePosixPath(member.name).name
        if basename not in _REQUIRED_TAXDUMP_MEMBERS:
            continue
        if not member.isfile() or basename in result:
            raise TaxonomyArtifactError("taxdump required member is duplicated or not regular")
        if PurePosixPath(member.name).is_absolute() or ".." in PurePosixPath(member.name).parts:
            raise TaxonomyArtifactError("unsafe taxdump member path")
        result[basename] = member
    if set(result) != set(_REQUIRED_TAXDUMP_MEMBERS):
        raise TaxonomyArtifactError("taxdump is missing a required history or taxonomy member")
    return result


def _observe_tar_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    filename: TaxdumpFilename,
) -> TaxdumpMember:
    stream = archive.extractfile(member)
    if stream is None:
        raise TaxonomyArtifactError(f"cannot read taxdump member {filename}")
    digest = hashlib.sha256()
    byte_size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        byte_size += len(chunk)
    return TaxdumpMember(filename=filename, byte_size=byte_size, sha256=digest.hexdigest())


def _member_lines(archive: tarfile.TarFile, member: tarfile.TarInfo) -> Iterator[tuple[int, str]]:
    stream = archive.extractfile(member)
    if stream is None:
        raise TaxonomyArtifactError(f"cannot read taxdump member {member.name}")
    for line_number, raw_line in enumerate(stream, start=1):
        try:
            yield line_number, raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise TaxonomyArtifactError(f"non-UTF-8 taxdump line in {member.name}") from exc


def _dmp_fields(line: str) -> tuple[str, ...]:
    fields = tuple(field.strip() for field in line.split("\t|"))
    if fields and fields[-1] == "":
        fields = fields[:-1]
    return fields


def _parse_merged(archive: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[tuple[int, int], ...]:
    rows: list[tuple[int, int]] = []
    for _, line in _member_lines(archive, member):
        fields = _dmp_fields(line)
        if len(fields) < 2:
            raise TaxonomyArtifactError("malformed merged.dmp row")
        rows.append((_positive_int(fields[0]), _positive_int(fields[1])))
    if len(rows) != len({old for old, _ in rows}):
        raise TaxonomyArtifactError("merged.dmp contains a duplicate old TaxId")
    return tuple(sorted(rows))


def _parse_deleted(archive: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[int, ...]:
    rows: list[int] = []
    for _, line in _member_lines(archive, member):
        fields = _dmp_fields(line)
        if not fields:
            raise TaxonomyArtifactError("malformed delnodes.dmp row")
        rows.append(_positive_int(fields[0]))
    if len(rows) != len(set(rows)):
        raise TaxonomyArtifactError("delnodes.dmp contains a duplicate TaxId")
    return tuple(sorted(rows))


def _parse_nodes(archive: tarfile.TarFile, member: tarfile.TarInfo) -> dict[int, _NcbiNode]:
    result: dict[int, _NcbiNode] = {}
    for line_number, line in _member_lines(archive, member):
        fields = _dmp_fields(line)
        if len(fields) < 3:
            raise TaxonomyArtifactError("malformed nodes.dmp row")
        tax_id = _positive_int(fields[0])
        node = _NcbiNode(
            tax_id=tax_id,
            parent_tax_id=_positive_int(fields[1]),
            rank=_clean_cell(fields[2]),
            line_number=line_number,
        )
        if not node.rank or tax_id in result:
            raise TaxonomyArtifactError("nodes.dmp has an invalid or duplicate TaxId")
        result[tax_id] = node
    return result


def _selected_ancestors(required: Sequence[int], nodes: Mapping[int, _NcbiNode]) -> set[int]:
    selected: set[int] = set()
    for tax_id in required:
        cursor = tax_id
        path: set[int] = set()
        while cursor not in selected:
            if cursor in path:
                raise TaxonomyArtifactError("NCBI taxonomy contains a parent cycle")
            path.add(cursor)
            node = nodes.get(cursor)
            if node is None:
                raise TaxonomyArtifactError(f"required TaxId {tax_id} has no nodes.dmp lineage")
            selected.add(cursor)
            if node.parent_tax_id == cursor:
                break
            cursor = node.parent_tax_id
    return selected


def _parse_selected_names(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    selected_ids: set[int],
) -> dict[int, tuple[str | None, tuple[TaxonomyAliasSpec, ...]]]:
    canonical: dict[int, str] = {}
    aliases: dict[int, dict[tuple[str, str, str], TaxonomyAliasSpec]] = {
        tax_id: {} for tax_id in selected_ids
    }
    for _, line in _member_lines(archive, member):
        fields = _dmp_fields(line)
        if len(fields) < 4:
            raise TaxonomyArtifactError("malformed names.dmp row")
        tax_id = _positive_int(fields[0])
        if tax_id not in selected_ids:
            continue
        name = _clean_cell(fields[1])
        name_class = _clean_cell(fields[3]).casefold()
        if not name or not name_class:
            raise TaxonomyArtifactError("selected names.dmp row is empty")
        if name_class == "scientific name":
            if tax_id in canonical and canonical[tax_id] != name:
                raise TaxonomyArtifactError("TaxId has multiple scientific names")
            canonical[tax_id] = name
            continue
        alias_type = re.sub(r"[^a-z0-9]+", "-", name_class).strip("-")
        normalized = unicodedata.normalize("NFC", name.casefold())
        key = (normalized, alias_type, "und")
        aliases[tax_id][key] = TaxonomyAliasSpec(
            alias=name,
            normalized_alias=normalized,
            alias_type=alias_type,
            locale="und",
        )
    return {
        tax_id: (
            canonical.get(tax_id),
            tuple(aliases[tax_id][key] for key in sorted(aliases[tax_id])),
        )
        for tax_id in selected_ids
    }


def _resolve_tax_id(tax_id: int, merged: Mapping[int, int], deleted: set[int]) -> int:
    seen: set[int] = set()
    cursor = tax_id
    while cursor in merged:
        if cursor in seen:
            raise TaxonomyArtifactError("NCBI merged TaxId history contains a cycle")
        seen.add(cursor)
        cursor = merged[cursor]
    if cursor in deleted:
        raise TaxonomyArtifactError(f"required TaxId {tax_id} resolves to a deleted TaxId")
    return cursor


def _positive_int(value: str) -> int:
    if not value.isdigit() or value.startswith("0"):
        raise TaxonomyArtifactError(f"invalid canonical positive integer: {value!r}")
    return int(value)


def _xlsx_table(path: str | Path, worksheet: str) -> Iterator[_XlsxRow]:
    try:
        with ZipFile(path) as archive:
            _validate_xlsx_container(archive)
            shared_strings = _xlsx_shared_strings(archive)
            worksheet_path = _xlsx_worksheet_path(archive, worksheet)
            stream = archive.open(worksheet_path)
            context = ET.iterparse(stream, events=("end",))
            headers: dict[int, str] | None = None
            for _, element in context:
                if element.tag != f"{{{_SPREADSHEET_NS}}}row":
                    continue
                row_number = int(element.attrib["r"])
                cells = _xlsx_row_cells(element, shared_strings)
                element.clear()
                if headers is None:
                    headers = {
                        column: _clean_cell(value) for column, value in cells.items() if value
                    }
                    continue
                assert headers is not None
                values = {
                    header: _clean_cell(cells.get(column, "")) for column, header in headers.items()
                }
                if any(values.values()):
                    yield _XlsxRow(row_number=row_number, values=MappingProxyType(values))
    except (OSError, BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        raise TaxonomyArtifactError(f"invalid XLSX taxonomy worksheet {worksheet!r}") from exc


def _validate_xlsx_container(archive: ZipFile) -> None:
    total = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or info.flag_bits & 0x1:
            raise TaxonomyArtifactError("unsafe or encrypted XLSX member")
        total += info.file_size
    if total > _MAX_XLSX_UNCOMPRESSED_BYTES:
        raise TaxonomyArtifactError("XLSX exceeds the approved uncompressed-size limit")


def _xlsx_shared_strings(archive: ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return tuple(
        "".join(node.text or "" for node in item.iter(f"{{{_SPREADSHEET_NS}}}t"))
        for item in root.findall(f"{{{_SPREADSHEET_NS}}}si")
    )


def _xlsx_worksheet_path(archive: ZipFile, worksheet: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        node.attrib["Id"]: node.attrib["Target"]
        for node in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }
    relationship_id: str | None = None
    for sheet in workbook.findall(f".//{{{_SPREADSHEET_NS}}}sheet"):
        if sheet.attrib.get("name") == worksheet:
            relationship_id = sheet.attrib[f"{{{_OFFICE_REL_NS}}}id"]
            break
    if relationship_id is None or relationship_id not in targets:
        raise TaxonomyArtifactError(f"XLSX worksheet not found: {worksheet}")
    target = targets[relationship_id]
    normalized = posixpath.normpath(posixpath.join("xl", target)).lstrip("/")
    if normalized.startswith("../") or normalized not in archive.namelist():
        raise TaxonomyArtifactError("unsafe XLSX worksheet target")
    return normalized


def _xlsx_row_cells(element: ET.Element, shared_strings: Sequence[str]) -> dict[int, str]:
    values: dict[int, str] = {}
    for cell in element.findall(f"{{{_SPREADSHEET_NS}}}c"):
        reference = cell.attrib.get("r", "")
        match = _CELL_REFERENCE_RE.fullmatch(reference)
        if match is None:
            raise TaxonomyArtifactError("invalid XLSX cell reference")
        column = _column_number(match.group(1))
        cell_type = cell.attrib.get("t")
        value_node = cell.find(f"{{{_SPREADSHEET_NS}}}v")
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter(f"{{{_SPREADSHEET_NS}}}t"))
        elif value_node is None:
            value = ""
        elif cell_type == "s":
            index = int(value_node.text or "")
            value = shared_strings[index]
        else:
            value = value_node.text or ""
        values[column] = value
    return values


def _column_number(token: str) -> int:
    result = 0
    for character in token:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _clean_cell(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


ArtifactManifest = NcbiTaxonomyArtifactManifest | IctvArtifactManifest


def _stage_source_artifacts(
    session: Session, manifest: ArtifactManifest
) -> tuple[SourceSnapshot, dict[str, SourceArtifact]]:
    artifacts: tuple[FrozenUpstreamArtifact, ...]
    if isinstance(manifest, NcbiTaxonomyArtifactManifest):
        source_name = "NCBI Taxonomy"
        version = manifest.version
        artifacts = (manifest.archive,)
    else:
        source_name = "ICTV"
        version = manifest.msl_version
        artifacts = (manifest.msl, manifest.corrected_vmr)
    snapshot_values = {
        "snapshot_key": manifest.snapshot_key,
        "source_name": source_name,
        "source_version": version,
        "source_uri": artifacts[0].source_uri,
        "retrieved_at": _timestamp(artifacts[0].retrieved_at),
        "declared_manifest_sha256": manifest.manifest_sha256,
        "verified_manifest_sha256": manifest.manifest_sha256,
        "declared_license_key": artifacts[0].license_key,
        "verified_license_key": artifacts[0].license_key,
    }
    source_snapshot, _ = _get_or_create(
        session,
        SourceSnapshot,
        SourceSnapshot.snapshot_key == manifest.snapshot_key,
        snapshot_values,
    )
    result: dict[str, SourceArtifact] = {}
    for artifact in artifacts:
        values = {
            "snapshot_id": source_snapshot.id,
            "artifact_key": artifact.artifact_key,
            "filename": artifact.filename,
            "media_type": artifact.media_type,
            "byte_size": artifact.byte_size,
            "declared_sha256": artifact.upstream_checksum
            if artifact.upstream_checksum_algorithm == "sha256"
            else None,
            "verified_sha256": artifact.sha256,
            "source_uri": artifact.source_uri,
            "retrieved_at": _timestamp(artifact.retrieved_at),
            "declared_license_key": artifact.license_key,
            "verified_license_key": artifact.license_key,
            "remote_checksum_verified": artifact.upstream_checksum_verified,
            "remote_verification_at": (
                _timestamp(artifact.retrieved_at) if artifact.upstream_checksum_verified else None
            ),
            "remote_verification_uri": artifact.checksum_source_uri,
        }
        row, _ = _get_or_create(
            session,
            SourceArtifact,
            SourceArtifact.artifact_key == artifact.artifact_key,
            values,
        )
        result[artifact.artifact_key] = row
    return source_snapshot, result


def _stage_lineage_snapshot(
    session: Session,
    manifest: TaxonomySnapshotManifest,
    *,
    primary_artifact: SourceArtifact,
) -> tuple[LineageSnapshot, bool]:
    values = {
        "snapshot_key": manifest.snapshot_key,
        "domain": manifest.domain,
        "scheme_kind": manifest.scheme_kind,
        "authority_namespace": manifest.authority_namespace,
        "version": manifest.version,
        "source_artifact_id": primary_artifact.id,
        "snapshot_sha256": manifest.manifest_sha256,
    }
    return _get_or_create(
        session,
        LineageSnapshot,
        LineageSnapshot.snapshot_key == manifest.snapshot_key,
        values,
    )


def _bind_release_source(
    session: Session,
    release: DatasetRelease,
    snapshot: SourceSnapshot,
    role: str,
) -> None:
    _get_or_create(
        session,
        ReleaseSourceSnapshot,
        (ReleaseSourceSnapshot.release_id == release.id)
        & (ReleaseSourceSnapshot.source_snapshot_id == snapshot.id)
        & (ReleaseSourceSnapshot.role == role),
        {
            "release_id": release.id,
            "source_snapshot_id": snapshot.id,
            "role": role,
        },
    )


def _bind_release_lineage(
    session: Session,
    release: DatasetRelease,
    snapshot: LineageSnapshot,
    manifest: TaxonomySnapshotManifest,
    *,
    replace_candidate_placeholder: bool,
) -> None:
    existing = session.scalar(
        select(ReleaseLineageSnapshot).where(
            ReleaseLineageSnapshot.release_id == release.id,
            ReleaseLineageSnapshot.role == manifest.release_role,
        )
    )
    if existing is not None and existing.snapshot_id != snapshot.id:
        existing_snapshot = session.get(LineageSnapshot, existing.snapshot_id)
        replaceable = (
            manifest.release_role == "assembly_source_taxonomy"
            and existing_snapshot is not None
            and existing_snapshot.version.endswith(":assembly-report-leaves")
        )
        if not replace_candidate_placeholder or not replaceable:
            raise TaxonomyImportError("release lineage role is already bound to another snapshot")
        session.execute(
            delete(AssemblyTaxonAssignment).where(
                AssemblyTaxonAssignment.release_id == release.id,
                AssemblyTaxonAssignment.snapshot_id == existing.snapshot_id,
            )
        )
        session.delete(existing)
        session.flush()
    _get_or_create(
        session,
        ReleaseLineageSnapshot,
        (ReleaseLineageSnapshot.release_id == release.id)
        & (ReleaseLineageSnapshot.snapshot_id == snapshot.id)
        & (ReleaseLineageSnapshot.role == manifest.release_role),
        {
            "release_id": release.id,
            "snapshot_id": snapshot.id,
            "role": manifest.release_role,
            "domain": manifest.domain,
            "scheme_kind": manifest.scheme_kind,
        },
    )


def _stage_terms(
    session: Session,
    snapshot: LineageSnapshot,
    manifest: TaxonomySnapshotManifest,
) -> tuple[dict[str, LineageTerm], int, int]:
    existing = {
        row.term_key: row
        for row in session.scalars(
            select(LineageTerm).where(LineageTerm.snapshot_id == snapshot.id)
        ).all()
    }
    if existing and set(existing) != {row.term_key for row in manifest.terms}:
        raise TaxonomyImportError("taxonomy snapshot has partial or drifting term rows")
    if not existing:
        _insert_multirow(
            session,
            LineageTerm,
            (
                {
                    "snapshot_id": snapshot.id,
                    "term_key": spec.term_key,
                    "canonical_name": spec.canonical_name,
                    "rank": spec.rank,
                    "authority_local_id": spec.authority_local_id,
                    "source_locator": spec.source_locator.model_dump(mode="json"),
                }
                for spec in manifest.terms
            ),
        )
        session.flush()
        existing = {
            row.term_key: row
            for row in session.scalars(
                select(LineageTerm).where(LineageTerm.snapshot_id == snapshot.id)
            ).all()
        }
    for spec in manifest.terms:
        row = existing[spec.term_key]
        _assert_values(
            row,
            {
                "snapshot_id": snapshot.id,
                "term_key": spec.term_key,
                "canonical_name": spec.canonical_name,
                "rank": spec.rank,
                "authority_local_id": spec.authority_local_id,
                "source_locator": spec.source_locator.model_dump(mode="json"),
            },
        )

    expected_aliases = tuple(
        (spec.term_key, alias) for spec in manifest.terms for alias in spec.aliases
    )
    existing_aliases = session.scalars(
        select(LineageAlias).where(LineageAlias.snapshot_id == snapshot.id)
    ).all()
    expected_alias_values = {
        (
            existing[term_key].id,
            alias.alias,
            alias.normalized_alias,
            alias.alias_type,
            alias.locale,
        )
        for term_key, alias in expected_aliases
    }
    if not existing_aliases:
        for term_key, alias in expected_aliases:
            session.add(
                LineageAlias(
                    snapshot_id=snapshot.id,
                    term_id=existing[term_key].id,
                    alias=alias.alias,
                    normalized_alias=alias.normalized_alias,
                    alias_type=alias.alias_type,
                    locale=alias.locale,
                )
            )
        session.flush()
    elif {
        (row.term_id, row.alias, row.normalized_alias, row.alias_type, row.locale)
        for row in existing_aliases
    } != expected_alias_values:
        raise TaxonomyImportError("taxonomy snapshot has partial or drifting alias rows")

    expected_closure = _closure_rows(manifest, existing)
    existing_closure = session.scalars(
        select(LineageClosure).where(LineageClosure.snapshot_id == snapshot.id)
    ).all()
    if not existing_closure:
        _insert_multirow(
            session,
            LineageClosure,
            (
                {
                    "snapshot_id": snapshot.id,
                    "ancestor_term_id": ancestor,
                    "descendant_term_id": descendant,
                    "depth": depth,
                }
                for ancestor, descendant, depth in expected_closure
            ),
        )
        session.flush()
    elif {
        (row.ancestor_term_id, row.descendant_term_id, row.depth) for row in existing_closure
    } != set(expected_closure):
        raise TaxonomyImportError("taxonomy snapshot has partial or drifting closure rows")
    return existing, len(expected_aliases), len(expected_closure)


def _insert_multirow(
    session: Session,
    model: type[LineageTerm] | type[LineageClosure],
    values: Iterable[dict[str, object]],
) -> None:
    """Insert immutable taxonomy rows in bounded PostgreSQL multi-value batches."""

    batch: list[dict[str, object]] = []
    for value in values:
        batch.append(value)
        if len(batch) == _MULTIROW_INSERT_BATCH_SIZE:
            session.execute(insert(model).values(batch))
            batch.clear()
    if batch:
        session.execute(insert(model).values(batch))


def _closure_rows(
    manifest: TaxonomySnapshotManifest,
    terms: Mapping[str, LineageTerm],
) -> tuple[tuple[int, int, int], ...]:
    parent = {row.term_key: row.parent_term_key for row in manifest.terms}
    closure: list[tuple[int, int, int]] = []
    for descendant_key in sorted(parent):
        cursor: str | None = descendant_key
        depth = 0
        while cursor is not None:
            closure.append((terms[cursor].id, terms[descendant_key].id, depth))
            cursor = parent[cursor]
            depth += 1
    return tuple(closure)


def _stage_assignments(
    session: Session,
    release: DatasetRelease,
    snapshot_manifest: TaxonomySnapshotManifest,
    terms: Mapping[str, LineageTerm],
    artifacts: Mapping[str, SourceArtifact],
    assignment_manifest: AssemblyTaxonAssignmentManifest | None,
) -> int:
    if assignment_manifest is None:
        return 0
    if snapshot_manifest.authority_namespace != "ncbi-taxonomy":
        raise TaxonomyImportError("assembly assignments require the NCBI host snapshot")
    if assignment_manifest.ncbi_snapshot_manifest_sha256 != snapshot_manifest.manifest_sha256:
        raise TaxonomyImportError("assembly assignments bind a different NCBI snapshot")
    for spec in assignment_manifest.assignments:
        assembly = session.scalar(
            select(GenomeAssembly)
            .join(
                ReleaseAssemblyMembership,
                (ReleaseAssemblyMembership.assembly_id == GenomeAssembly.id)
                & (ReleaseAssemblyMembership.release_id == release.id),
            )
            .where(GenomeAssembly.accession_version == spec.assembly_accession_version)
        )
        term = terms.get(spec.term_key)
        artifact = artifacts.get(spec.source_artifact_key) or session.scalar(
            select(SourceArtifact).where(SourceArtifact.artifact_key == spec.source_artifact_key)
        )
        if assembly is None or term is None or artifact is None:
            raise TaxonomyImportError("assembly assignment endpoint is absent")
        assignment_key = stable_key(
            "assembly-taxon-assignment:ncbi",
            {
                "assembly_accession_version": spec.assembly_accession_version,
                "assignment_policy_key": spec.assignment_policy_key,
                "release_key": release.release_key,
                "snapshot_manifest_sha256": snapshot_manifest.manifest_sha256,
                "reported_tax_id": spec.reported_ncbi_tax_id,
                "resolved_tax_id": spec.resolved_ncbi_tax_id,
            },
        )
        _get_or_create(
            session,
            AssemblyTaxonAssignment,
            AssemblyTaxonAssignment.assignment_key == assignment_key,
            {
                "assignment_key": assignment_key,
                "release_id": release.id,
                "assembly_id": assembly.id,
                "snapshot_id": term.snapshot_id,
                "snapshot_role": "assembly_source_taxonomy",
                "term_id": term.id,
                "assignment_policy_key": spec.assignment_policy_key,
                "source_artifact_id": artifact.id,
                "source_locator": {
                    "locator": spec.source_locator,
                    "reported_ncbi_tax_id": spec.reported_ncbi_tax_id,
                    "resolved_ncbi_tax_id": spec.resolved_ncbi_tax_id,
                    "snapshot_manifest_sha256": snapshot_manifest.manifest_sha256,
                },
            },
        )
    return len(assignment_manifest.assignments)


def _get_or_create[ModelT](
    session: Session,
    model: type[ModelT],
    predicate: Any,
    values: Mapping[str, object],
) -> tuple[ModelT, bool]:
    row = session.scalar(select(model).where(predicate))
    if row is None:
        row = model(**values)
        session.add(row)
        session.flush()
        return row, True
    _assert_values(row, values)
    return row, False


def _assert_values(row: object, values: Mapping[str, object]) -> None:
    for field_name, expected in values.items():
        if getattr(row, field_name) != expected:
            raise TaxonomyImportError(
                f"existing {type(row).__name__}.{field_name} differs from frozen input"
            )


def _timestamp(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


__all__ = [
    "LoadedNcbiTaxonomy",
    "MappingValidationReport",
    "TaxonomyArtifactError",
    "TaxonomyImportError",
    "TaxonomyImportReport",
    "build_assembly_taxon_assignment_manifest",
    "build_ictv_artifact_manifest",
    "build_ncbi_taxonomy_artifact_manifest",
    "build_polintovirus_rename_mapping_manifest",
    "import_taxonomy_snapshot",
    "load_ictv_taxonomy_snapshot",
    "load_ncbi_taxonomy_snapshot",
    "load_approved_assembly_tax_ids",
    "observe_taxdump_members",
    "validate_study_formal_mapping",
]
