"""Streaming importer for the frozen Zhao et al. Data S1 workbook.

The adapter reads the worksheet row-by-row with the Python standard library.
Only the XLSX shared-string table is retained in memory; the roughly 594 MB
uncompressed worksheet XML is never materialized.  Rows outside the approved
ten-assembly Bivalvia x Orthopolintovirales scope are intentionally filtered.
Every row inside that scope is yielded exactly once as either a normalized
candidate or a structured quarantine record.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Literal
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from eve_relation_rag.domain.keys import (
    StableKeyError,
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
    locus_key,
    stable_key,
)

DATA_S1_WORKSHEET: Final = "S3"
DATA_S1_WORKSHEET_ALIASES: Final[tuple[str, ...]] = ("S3", "Data S1")
DATA_S1_ARTIFACT_SHA256: Final = (
    "79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800"
)
DATA_S1_ARTIFACT_BYTE_SIZE: Final = 83_851_778
DATA_S1_SOURCE_SNAPSHOT_KEY: Final = (
    "study-defined:10.1101/2025.04.19.649669:v4:data-s1"
)
DATA_S1_IDENTITY_POLICY_KEY: Final = "zhao-v4-contig-source-occurrence-v1"
DATA_S1_SOURCE_ASSESSMENT_SCHEME: Final = "zhao-biorxiv-v4-hcvr-status-v1"
DATA_S1_COORDINATE_SYSTEM: Final = "0-based-half-open"
DATA_S1_METHOD_RUN_IDENTITY: Final = "zhao-data-s1-import-v2"

DATA_S1_ASSEMBLY_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "GCA_015947965.1",
        "GCA_016617855.1",
        "GCA_016746295.1",
        "GCA_028554795.2",
        "GCA_029931535.1",
        "GCA_943736005.1",
        "GCA_944589985.1",
        "GCA_945859735.2",
        "GCA_946811455.1",
        "GCA_963210365.1",
    }
)

DATA_S1_SOURCE_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("A", "Assembly"),
    ("B", "Contig"),
    ("C", "VR"),
    ("D", "HCVR"),
    ("E", "Contig Length"),
    ("F", "Start"),
    ("G", "End"),
    ("H", "Length"),
    ("I", "Annoated Viral Proportion"),
    ("J", "Viral Major Taxon"),
    ("K", "Eukaryote Classification"),
    ("L", "Phylum"),
    ("M", "Class"),
    ("N", "Order"),
    ("O", "Family"),
    ("P", "Genus"),
    ("Q", "Organism Name"),
    ("R", "VR Type"),
    ("S", "Unique Rate"),
    ("T", "Conserved OG"),
    ("U", "Busco score"),
)

_VIRAL_MAJOR_TAXON: Final = "Orthopolintovirales"
_HOST_CLASS: Final = "Bivalvia"
_RECORD_KEY_NAMESPACE: Final = "call:zhao2026-v4"
_RECORD_KEY_SCHEMA: Final = "zhao-data-s1-detection-call-v2"
_SOURCE_RECORD_KEY_NAMESPACE: Final = "source-record:zhao2026-v4"
_SOURCE_RECORD_KEY_SCHEMA: Final = "zhao-data-s1-source-record-v1"

_SPREADSHEET_NS: Final = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS: Final = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_ROW_TAG: Final = f"{{{_SPREADSHEET_NS}}}row"
_CELL_TAG: Final = f"{{{_SPREADSHEET_NS}}}c"
_VALUE_TAG: Final = f"{{{_SPREADSHEET_NS}}}v"
_TEXT_TAG: Final = f"{{{_SPREADSHEET_NS}}}t"
_SHARED_ITEM_TAG: Final = f"{{{_SPREADSHEET_NS}}}si"
_SHEET_TAG: Final = f"{{{_SPREADSHEET_NS}}}sheet"
_RELATIONSHIP_TAG: Final = f"{{{_PACKAGE_REL_NS}}}Relationship"
_RELATIONSHIP_ID: Final = f"{{{_OFFICE_REL_NS}}}id"

_CELL_REFERENCE_RE: Final = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
_UNSIGNED_INTEGER_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")
_VR_TOKEN_RE: Final = re.compile(r"^vr[1-9][0-9]*$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMPOUND_INTERVAL_MARKERS: Final = re.compile(
    r"[,;|]|\b(?:join|order|complement)\s*\(|\.\.", re.IGNORECASE
)
_BYTE_BOUND_ATTESTATION: Final = object()

type SourceAssessment = Literal["source_high", "source_low"]
type ResolutionStatus = Literal[
    "not_checked", "exact", "unresolved", "length_unverified", "length_mismatch"
]


class DataS1WorkbookError(ValueError):
    """Raised when the XLSX container or fixed worksheet schema is invalid."""


class NcbiResolutionIndexError(ValueError):
    """Raised when a frozen NCBI JSONL report cannot form a reliable index."""


class FileByteVerificationError(ValueError):
    """Raised when a file is unreadable or differs from its expected frozen bytes."""


@dataclass(frozen=True, slots=True)
class FileByteObservation:
    """Streaming SHA-256 and byte-size observation for one local file."""

    path: Path
    byte_size: int
    sha256: str


def verify_file_bytes(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
    expected_byte_size: int | None = None,
    chunk_size: int = 1024 * 1024,
) -> FileByteObservation:
    """Stream *path* once and fail if optional frozen byte expectations differ."""

    if expected_sha256 is not None:
        _require_sha256("expected_sha256", expected_sha256)
    if (
        expected_byte_size is not None
        and (type(expected_byte_size) is not int or expected_byte_size < 0)
    ):
        raise ValueError("expected_byte_size must be a non-negative integer")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    file_path = Path(path)
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with file_path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
                byte_size += len(chunk)
    except OSError as exc:
        raise FileByteVerificationError(f"cannot read file for verification: {file_path}") from exc

    actual_sha256 = digest.hexdigest()
    mismatches: list[str] = []
    if expected_byte_size is not None and byte_size != expected_byte_size:
        mismatches.append(f"byte_size expected {expected_byte_size}, observed {byte_size}")
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        mismatches.append(f"sha256 expected {expected_sha256}, observed {actual_sha256}")
    if mismatches:
        raise FileByteVerificationError(
            f"frozen file verification failed for {file_path}: " + "; ".join(mismatches)
        )
    return FileByteObservation(path=file_path, byte_size=byte_size, sha256=actual_sha256)


@dataclass(frozen=True, slots=True)
class NcbiResolutionIndex:
    """Read-only exact assembly/sequence/length index from NCBI Datasets JSONL.

    Both reports are consumed one line at a time. Only sequences belonging to
    the fixed ten-assembly pilot scope are retained; callers may additionally
    restrict storage to source-referenced sequence keys.
    """

    assemblies: frozenset[str]
    sequence_lengths: Mapping[tuple[str, str], int]
    assembly_report_records: int
    sequence_report_records: int
    assembly_organisms: Mapping[str, tuple[str, int]] = field(default_factory=dict)
    assembly_report_sha256: str | None = None
    assembly_report_byte_size: int | None = None
    sequence_report_sha256: str | None = None
    sequence_report_byte_size: int | None = None
    _byte_attestation: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "assemblies", frozenset(self.assemblies))
        object.__setattr__(
            self,
            "sequence_lengths",
            MappingProxyType(dict(self.sequence_lengths)),
        )
        object.__setattr__(
            self,
            "assembly_organisms",
            MappingProxyType(dict(self.assembly_organisms)),
        )

    @property
    def byte_bound(self) -> bool:
        """Whether index content was derived from the exact observed report streams."""

        return self._byte_attestation is _BYTE_BOUND_ATTESTATION

    @classmethod
    def from_jsonl_reports(
        cls,
        assembly_report_path: str | os.PathLike[str],
        sequence_report_path: str | os.PathLike[str],
        *,
        required_sequence_keys: Set[tuple[str, str]] | None = None,
        expected_assembly_report_sha256: str | None = None,
        expected_assembly_report_byte_size: int | None = None,
        expected_sequence_report_sha256: str | None = None,
        expected_sequence_report_byte_size: int | None = None,
    ) -> NcbiResolutionIndex:
        """Verify and stream frozen NCBI reports into a pilot-scoped index.

        Actual SHA-256 and byte size are always recorded. Supplying an expected
        value makes a mismatch a hard error before authority resolution begins.
        """

        assemblies: set[str] = set()
        assembly_organisms: dict[str, tuple[str, int]] = {}
        assembly_report_records = 0

        def consume_assembly(
            line_number: int, report: Mapping[str, object]
        ) -> None:
            nonlocal assembly_report_records
            assembly_report_records += 1
            accession = report.get("accession")
            if not isinstance(accession, str):
                raise NcbiResolutionIndexError(
                    f"assembly report line {line_number} has no string accession"
                )
            if accession in DATA_S1_ASSEMBLY_ALLOWLIST:
                if accession in assemblies:
                    raise NcbiResolutionIndexError(
                        f"duplicate assembly report accession at line {line_number}: "
                        f"{accession}"
                    )
                assemblies.add(accession)
                organism = report.get("organism")
                if organism is not None:
                    if not isinstance(organism, dict):
                        raise NcbiResolutionIndexError(
                            f"assembly report line {line_number} has invalid organism"
                        )
                    organism_name = organism.get("organism_name")
                    tax_id = organism.get("tax_id")
                    if (
                        not isinstance(organism_name, str)
                        or not organism_name
                        or organism_name != organism_name.strip()
                        or isinstance(tax_id, bool)
                        or not isinstance(tax_id, int)
                        or tax_id <= 0
                    ):
                        raise NcbiResolutionIndexError(
                            "assembly report line "
                            f"{line_number} has invalid organism name/TaxId"
                        )
                    assembly_organisms[accession] = (organism_name, tax_id)

        assembly_observation = _consume_verified_jsonl(
            assembly_report_path,
            "assembly report",
            consume_assembly,
            expected_sha256=expected_assembly_report_sha256,
            expected_byte_size=expected_assembly_report_byte_size,
        )

        sequence_lengths: dict[tuple[str, str], int] = {}
        sequence_report_records = 0

        def consume_sequence(
            line_number: int, report: Mapping[str, object]
        ) -> None:
            nonlocal sequence_report_records
            sequence_report_records += 1
            assembly = report.get("assembly_accession")
            if not isinstance(assembly, str):
                raise NcbiResolutionIndexError(
                    f"sequence report line {line_number} has no string assembly_accession"
                )
            if assembly not in DATA_S1_ASSEMBLY_ALLOWLIST:
                return

            length = report.get("length")
            if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
                raise NcbiResolutionIndexError(
                    f"sequence report line {line_number} has invalid positive length"
                )
            for accession_field in ("genbank_accession", "refseq_accession"):
                accession = report.get(accession_field)
                if not isinstance(accession, str) or not accession:
                    continue
                key = (assembly, accession)
                if required_sequence_keys is not None and key not in required_sequence_keys:
                    continue
                existing_length = sequence_lengths.get(key)
                if existing_length is not None and existing_length != length:
                    raise NcbiResolutionIndexError(
                        "conflicting NCBI lengths for "
                        f"{assembly}/{accession}: {existing_length} and {length}"
                    )
                sequence_lengths[key] = length

        sequence_observation = _consume_verified_jsonl(
            sequence_report_path,
            "sequence report",
            consume_sequence,
            expected_sha256=expected_sequence_report_sha256,
            expected_byte_size=expected_sequence_report_byte_size,
        )

        index = cls(
            assemblies=frozenset(assemblies),
            sequence_lengths=sequence_lengths,
            assembly_report_records=assembly_report_records,
            sequence_report_records=sequence_report_records,
            assembly_organisms=assembly_organisms,
            assembly_report_sha256=assembly_observation.sha256,
            assembly_report_byte_size=assembly_observation.byte_size,
            sequence_report_sha256=sequence_observation.sha256,
            sequence_report_byte_size=sequence_observation.byte_size,
        )
        object.__setattr__(index, "_byte_attestation", _BYTE_BOUND_ATTESTATION)
        return index

    def sequence_length(
        self, assembly_accession_version: str, sequence_accession_version: str
    ) -> int | None:
        """Return the exact authority length for a sequence within an assembly."""

        return self.sequence_lengths.get(
            (assembly_accession_version, sequence_accession_version)
        )


@dataclass(frozen=True, slots=True)
class SourceRowLocator:
    """Typed provenance locator for one physical Excel row."""

    worksheet: str
    excel_row: int

    @property
    def label(self) -> str:
        """Return the stable human-readable worksheet/row locator."""

        return f"{self.worksheet}!{self.excel_row}"


@dataclass(frozen=True, slots=True)
class DataS1ValidationIssue:
    """One deterministic reason that prevents row normalization."""

    code: str
    field: str
    message: str
    raw_value: str


@dataclass(frozen=True, slots=True)
class ImportedDataS1Record:
    """A selected row with a format-valid identity and exact source interval."""

    record_key: str
    source_record_key: str
    method_run_identity: str
    locus_key: str
    artifact_sha256: str
    source_snapshot_key: str
    identity_policy_key: str
    source_assessment_scheme: str
    source_assessment: SourceAssessment
    assembly_accession_version: str
    sequence_accession_version: str
    native_vr_token: str
    assembly_resolution: ResolutionStatus
    contig_resolution: ResolutionStatus
    authority_contig_length: int | None
    contig_length: int
    start0: int
    end0: int
    length: int
    coordinate_system: str
    viral_major_taxon: str
    host_class: str
    vr_type: str
    source_hcvr: str
    locator: SourceRowLocator
    raw_row: Mapping[str, str]
    status: Literal["normalized_candidate"] = field(
        default="normalized_candidate", init=False
    )


@dataclass(frozen=True, slots=True)
class QuarantinedDataS1Record:
    """A selected source row retained with explicit validation/policy issues."""

    record_key: str
    source_record_key: str
    method_run_identity: str
    locus_key: str | None
    artifact_sha256: str
    source_snapshot_key: str
    identity_policy_key: str
    source_assessment_scheme: str
    source_assessment: SourceAssessment
    assembly_accession_version: str
    sequence_accession_version: str
    native_vr_token: str
    assembly_resolution: ResolutionStatus
    contig_resolution: ResolutionStatus
    authority_contig_length: int | None
    locator: SourceRowLocator
    raw_row: Mapping[str, str]
    issues: tuple[DataS1ValidationIssue, ...]
    status: Literal["quarantine"] = field(default="quarantine", init=False)


type DataS1ImportOutcome = ImportedDataS1Record | QuarantinedDataS1Record


def iter_unverified_data_s1_import(
    workbook_path: str | os.PathLike[str],
    *,
    artifact_sha256: str = DATA_S1_ARTIFACT_SHA256,
    source_snapshot_key: str = DATA_S1_SOURCE_SNAPSHOT_KEY,
    identity_policy_key: str = DATA_S1_IDENTITY_POLICY_KEY,
    method_run_identity: str = DATA_S1_METHOD_RUN_IDENTITY,
    resolution_index: NcbiResolutionIndex | None = None,
) -> Iterator[DataS1ImportOutcome]:
    """Parse Data S1 without proving that *workbook_path* matches its claimed hash.

    Selection is fixed to the approved ten assemblies and exact values
    ``J == Orthopolintovirales`` and ``M == Bivalvia``.  The generator is
    replay-safe: call keys bind the frozen artifact digest and source locator;
    locus keys use the approved coordinate-free identity. Without a supplied
    NCBI index, resolution is explicitly ``not_checked``, never ``exact``. This
    low-level API exists for synthetic fixtures and already-verified adapters;
    production callers should use :func:`iter_verified_data_s1_import` or
    :func:`iter_canonical_data_s1_import`.
    """

    _require_sha256("artifact_sha256", artifact_sha256)
    _require_exact_token("source_snapshot_key", source_snapshot_key)
    _require_exact_token("identity_policy_key", identity_policy_key)
    _require_exact_token("method_run_identity", method_run_identity)

    try:
        with ZipFile(workbook_path) as archive:
            shared_strings = _load_shared_strings(archive)
            physical_worksheet, worksheet_path = _find_worksheet_path(
                archive, DATA_S1_WORKSHEET_ALIASES
            )
            yield from _iter_selected_rows(
                archive,
                physical_worksheet,
                worksheet_path,
                shared_strings,
                artifact_sha256=artifact_sha256,
                source_snapshot_key=source_snapshot_key,
                identity_policy_key=identity_policy_key,
                method_run_identity=method_run_identity,
                resolution_index=resolution_index,
            )
    except BadZipFile as exc:
        raise DataS1WorkbookError(f"not a valid XLSX ZIP container: {workbook_path}") from exc


def iter_data_s1_import(
    workbook_path: str | os.PathLike[str],
    *,
    artifact_sha256: str = DATA_S1_ARTIFACT_SHA256,
    source_snapshot_key: str = DATA_S1_SOURCE_SNAPSHOT_KEY,
    identity_policy_key: str = DATA_S1_IDENTITY_POLICY_KEY,
    method_run_identity: str = DATA_S1_METHOD_RUN_IDENTITY,
    resolution_index: NcbiResolutionIndex | None = None,
) -> Iterator[DataS1ImportOutcome]:
    """Backward-compatible, explicitly unverified low-level parser.

    This name is retained for existing synthetic fixtures. It trusts the
    caller-supplied ``artifact_sha256`` and must not be the production byte
    provenance boundary.
    """

    yield from iter_unverified_data_s1_import(
        workbook_path,
        artifact_sha256=artifact_sha256,
        source_snapshot_key=source_snapshot_key,
        identity_policy_key=identity_policy_key,
        method_run_identity=method_run_identity,
        resolution_index=resolution_index,
    )


def iter_verified_data_s1_import(
    workbook_path: str | os.PathLike[str],
    *,
    expected_artifact_sha256: str,
    expected_artifact_byte_size: int,
    source_snapshot_key: str = DATA_S1_SOURCE_SNAPSHOT_KEY,
    identity_policy_key: str = DATA_S1_IDENTITY_POLICY_KEY,
    method_run_identity: str = DATA_S1_METHOD_RUN_IDENTITY,
    resolution_index: NcbiResolutionIndex | None = None,
) -> Iterator[DataS1ImportOutcome]:
    """Verify workbook bytes, then yield rows bound to the observed SHA-256."""

    observation = verify_file_bytes(
        workbook_path,
        expected_sha256=expected_artifact_sha256,
        expected_byte_size=expected_artifact_byte_size,
    )
    yield from iter_unverified_data_s1_import(
        workbook_path,
        artifact_sha256=observation.sha256,
        source_snapshot_key=source_snapshot_key,
        identity_policy_key=identity_policy_key,
        method_run_identity=method_run_identity,
        resolution_index=resolution_index,
    )


def iter_canonical_data_s1_import(
    workbook_path: str | os.PathLike[str],
    *,
    source_snapshot_key: str = DATA_S1_SOURCE_SNAPSHOT_KEY,
    identity_policy_key: str = DATA_S1_IDENTITY_POLICY_KEY,
    method_run_identity: str = DATA_S1_METHOD_RUN_IDENTITY,
    resolution_index: NcbiResolutionIndex | None = None,
) -> Iterator[DataS1ImportOutcome]:
    """Verify the official bioRxiv Data S1 bytes, then stream selected rows."""

    yield from iter_verified_data_s1_import(
        workbook_path,
        expected_artifact_sha256=DATA_S1_ARTIFACT_SHA256,
        expected_artifact_byte_size=DATA_S1_ARTIFACT_BYTE_SIZE,
        source_snapshot_key=source_snapshot_key,
        identity_policy_key=identity_policy_key,
        method_run_identity=method_run_identity,
        resolution_index=resolution_index,
    )


def _require_exact_token(name: str, value: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty exact token")


def _require_sha256(name: str, value: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase SHA-256 hex digest")


def _find_worksheet_path(
    archive: ZipFile, worksheet_names: Sequence[str]
) -> tuple[str, str]:
    try:
        with archive.open("xl/workbook.xml") as stream:
            workbook = ET.parse(stream).getroot()
        with archive.open("xl/_rels/workbook.xml.rels") as stream:
            relationships = ET.parse(stream).getroot()
    except KeyError as exc:
        raise DataS1WorkbookError(f"missing XLSX workbook metadata: {exc.args[0]}") from exc
    except ET.ParseError as exc:
        raise DataS1WorkbookError("invalid XLSX workbook metadata XML") from exc

    relationships_by_name: dict[str, str] = {}
    for sheet in workbook.iter(_SHEET_TAG):
        name = sheet.attrib.get("name")
        sheet_relationship_id = sheet.attrib.get(_RELATIONSHIP_ID)
        if name is not None and sheet_relationship_id is not None:
            relationships_by_name[name] = sheet_relationship_id

    physical_name: str | None = None
    relationship_id: str | None = None
    for worksheet_name in worksheet_names:
        relationship_id = relationships_by_name.get(worksheet_name)
        if relationship_id is not None:
            physical_name = worksheet_name
            break
    if physical_name is None or relationship_id is None:
        raise DataS1WorkbookError(
            f"worksheet not found; expected one of {tuple(worksheet_names)!r}"
        )

    target: str | None = None
    for relationship in relationships.iter(_RELATIONSHIP_TAG):
        if relationship.attrib.get("Id") == relationship_id:
            target = relationship.attrib.get("Target")
            break
    if not target:
        raise DataS1WorkbookError(
            f"worksheet relationship has no target: {relationship_id!r}"
        )

    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(str(PurePosixPath("xl") / target))
    if normalized.startswith("../") or normalized not in archive.namelist():
        raise DataS1WorkbookError(f"invalid worksheet target: {target!r}")
    return physical_name, normalized


def _load_shared_strings(archive: ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()

    values: list[str] = []
    stack: list[ET.Element] = []
    try:
        with archive.open("xl/sharedStrings.xml") as stream:
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if element.tag == _SHARED_ITEM_TAG:
                    values.append("".join(node.text or "" for node in element.iter(_TEXT_TAG)))
                    if len(stack) >= 2:
                        stack[-2].remove(element)
                    element.clear()
                stack.pop()
    except ET.ParseError as exc:
        raise DataS1WorkbookError("invalid sharedStrings.xml") from exc
    return tuple(values)


def _iter_selected_rows(
    archive: ZipFile,
    physical_worksheet: str,
    worksheet_path: str,
    shared_strings: Sequence[str],
    *,
    artifact_sha256: str,
    source_snapshot_key: str,
    identity_policy_key: str,
    method_run_identity: str,
    resolution_index: NcbiResolutionIndex | None,
) -> Iterator[DataS1ImportOutcome]:
    header_seen = False
    stack: list[ET.Element] = []
    try:
        with archive.open(worksheet_path) as stream:
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if element.tag == _ROW_TAG:
                    excel_row, raw_cells = _decode_row(element, shared_strings)
                    if excel_row == 1:
                        _validate_header(raw_cells)
                        header_seen = True
                    elif not header_seen:
                        raise DataS1WorkbookError("Data S1 header row A1:U1 is missing")
                    elif _is_in_approved_scope(raw_cells):
                        raw_row = {
                            header: raw_cells.get(column, "")
                            for column, header in DATA_S1_SOURCE_COLUMNS
                        }
                        yield _normalize_selected_row(
                            raw_row,
                            SourceRowLocator(physical_worksheet, excel_row),
                            artifact_sha256=artifact_sha256,
                            source_snapshot_key=source_snapshot_key,
                            identity_policy_key=identity_policy_key,
                            method_run_identity=method_run_identity,
                            resolution_index=resolution_index,
                        )
                    if len(stack) >= 2:
                        stack[-2].remove(element)
                    element.clear()
                stack.pop()
    except KeyError as exc:
        raise DataS1WorkbookError(f"missing worksheet XML: {worksheet_path}") from exc
    except ET.ParseError as exc:
        raise DataS1WorkbookError(f"invalid worksheet XML: {worksheet_path}") from exc

    if not header_seen:
        raise DataS1WorkbookError("Data S1 header row A1:U1 is missing")


def _decode_row(
    row: ET.Element, shared_strings: Sequence[str]
) -> tuple[int, dict[str, str]]:
    row_reference = row.attrib.get("r")
    if row_reference is None or not row_reference.isdecimal() or int(row_reference) < 1:
        raise DataS1WorkbookError(f"invalid Excel row reference: {row_reference!r}")
    excel_row = int(row_reference)

    values: dict[str, str] = {}
    for cell in row.findall(_CELL_TAG):
        reference = cell.attrib.get("r", "")
        match = _CELL_REFERENCE_RE.fullmatch(reference)
        if match is None or int(match.group(2)) != excel_row:
            raise DataS1WorkbookError(f"invalid cell reference in row {excel_row}: {reference!r}")
        column = match.group(1)
        values[column] = _decode_cell(cell, shared_strings, reference)
    return excel_row, values


def _decode_cell(cell: ET.Element, shared_strings: Sequence[str], reference: str) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_TEXT_TAG))

    value_node = cell.find(_VALUE_TAG)
    if value_node is None or value_node.text is None:
        return ""
    raw_value = value_node.text
    if cell_type != "s":
        return raw_value

    try:
        shared_index = int(raw_value)
        return shared_strings[shared_index]
    except (ValueError, IndexError) as exc:
        raise DataS1WorkbookError(
            f"invalid shared-string index at {reference}: {raw_value!r}"
        ) from exc


def _validate_header(raw_cells: Mapping[str, str]) -> None:
    mismatches = [
        f"{column}: expected {expected!r}, found {raw_cells.get(column, '')!r}"
        for column, expected in DATA_S1_SOURCE_COLUMNS
        if raw_cells.get(column, "") != expected
    ]
    if mismatches:
        raise DataS1WorkbookError("Data S1 header mismatch: " + "; ".join(mismatches))


def _is_in_approved_scope(raw_cells: Mapping[str, str]) -> bool:
    return (
        raw_cells.get("A", "") in DATA_S1_ASSEMBLY_ALLOWLIST
        and raw_cells.get("J", "") == _VIRAL_MAJOR_TAXON
        and raw_cells.get("M", "") == _HOST_CLASS
    )


def _normalize_selected_row(
    raw_row: Mapping[str, str],
    locator: SourceRowLocator,
    *,
    artifact_sha256: str,
    source_snapshot_key: str,
    identity_policy_key: str,
    method_run_identity: str,
    resolution_index: NcbiResolutionIndex | None,
) -> DataS1ImportOutcome:
    issues: list[DataS1ValidationIssue] = []

    assembly = raw_row["Assembly"]
    contig = raw_row["Contig"]
    vr_token = raw_row["VR"]
    hcvr = raw_row["HCVR"]
    vr_type = raw_row["VR Type"]
    source_record_key = data_s1_source_record_key(
        artifact_sha256,
        source_snapshot_key,
        locator,
    )
    record_key = data_s1_record_key(
        artifact_sha256,
        source_snapshot_key,
        locator,
        assembly_accession_version=assembly,
        sequence_accession_version=contig,
        native_vr_token=vr_token,
        method_run_identity=method_run_identity,
    )
    source_assessment: SourceAssessment = "source_high" if hcvr == "Yes" else "source_low"

    identity_is_valid = True
    if not is_versioned_assembly_accession(assembly):
        identity_is_valid = False
        issues.append(
            _issue(
                "invalid_assembly_accession_version",
                "Assembly",
                "expected an exact GCA_/GCF_ accession.version",
                assembly,
            )
        )
    if not is_versioned_contig_accession(contig):
        identity_is_valid = False
        issues.append(
            _issue(
                "invalid_sequence_accession_version",
                "Contig",
                "expected an exact INSDC sequence accession.version",
                contig,
            )
        )
    if _VR_TOKEN_RE.fullmatch(vr_token) is None:
        identity_is_valid = False
        issues.append(
            _issue(
                "invalid_vr_token",
                "VR",
                "expected the source-native token vr followed by a positive integer",
                vr_token,
            )
        )

    candidate_locus_key: str | None = None
    if identity_is_valid:
        try:
            candidate_locus_key = locus_key(
                source_snapshot_key=source_snapshot_key,
                assembly_accession_version=assembly,
                contig_accession_version=contig,
                native_vr_token=vr_token,
                identity_policy_version=identity_policy_key,
            )
        except StableKeyError as exc:
            issues.append(
                _issue("locus_key_error", "VR", f"cannot build locus key: {exc}", vr_token)
            )

    raw_interval_values = (raw_row["Start"], raw_row["End"], raw_row["Length"])
    interval_is_compound = any(
        _COMPOUND_INTERVAL_MARKERS.search(value) is not None
        for value in raw_interval_values
    )
    if interval_is_compound:
        issues.append(
            _issue(
                "multipart_interval",
                "Start/End",
                "Milestone 1 accepts exactly one scalar interval",
                " | ".join(raw_interval_values),
            )
        )

    contig_length = _parse_integer(raw_row, "Contig Length", issues)
    start0 = _parse_integer(raw_row, "Start", issues) if not interval_is_compound else None
    end0 = _parse_integer(raw_row, "End", issues) if not interval_is_compound else None
    length = _parse_integer(raw_row, "Length", issues) if not interval_is_compound else None

    if contig_length is not None and contig_length <= 0:
        issues.append(
            _issue(
                "invalid_contig_length",
                "Contig Length",
                "contig length must be a positive integer",
                raw_row["Contig Length"],
            )
        )

    (
        assembly_resolution,
        contig_resolution,
        authority_contig_length,
    ) = _resolve_against_ncbi(
        resolution_index,
        assembly,
        contig,
        contig_length,
        issues,
    )
    if start0 is not None and end0 is not None:
        if not 0 <= start0 < end0:
            issues.append(
                _issue(
                    "invalid_interval",
                    "Start/End",
                    "expected 0 <= Start < End for a half-open interval",
                    f"{raw_row['Start']}:{raw_row['End']}",
                )
            )
        if length is not None and length != end0 - start0:
            issues.append(
                _issue(
                    "length_mismatch",
                    "Length",
                    "Length must equal End - Start",
                    raw_row["Length"],
                )
            )
        if contig_length is not None and end0 > contig_length:
            issues.append(
                _issue(
                    "interval_out_of_bounds",
                    "End",
                    "End must not exceed Contig Length",
                    raw_row["End"],
                )
            )

    if vr_type == "Viral contig":
        issues.append(
            _issue(
                "viral_contig_policy_quarantine",
                "VR Type",
                "viral-contig-like source records are auditable but not normalized candidates",
                vr_type,
            )
        )
    elif vr_type != "Integration":
        issues.append(
            _issue(
                "unsupported_vr_type",
                "VR Type",
                "expected Integration or Viral contig",
                vr_type,
            )
        )

    if issues:
        return QuarantinedDataS1Record(
            record_key=record_key,
            source_record_key=source_record_key,
            method_run_identity=method_run_identity,
            locus_key=candidate_locus_key,
            artifact_sha256=artifact_sha256,
            source_snapshot_key=source_snapshot_key,
            identity_policy_key=identity_policy_key,
            source_assessment_scheme=DATA_S1_SOURCE_ASSESSMENT_SCHEME,
            source_assessment=source_assessment,
            assembly_accession_version=assembly,
            sequence_accession_version=contig,
            native_vr_token=vr_token,
            assembly_resolution=assembly_resolution,
            contig_resolution=contig_resolution,
            authority_contig_length=authority_contig_length,
            locator=locator,
            raw_row=dict(raw_row),
            issues=tuple(issues),
        )

    assert candidate_locus_key is not None
    assert contig_length is not None
    assert start0 is not None
    assert end0 is not None
    assert length is not None
    return ImportedDataS1Record(
        record_key=record_key,
        source_record_key=source_record_key,
        method_run_identity=method_run_identity,
        locus_key=candidate_locus_key,
        artifact_sha256=artifact_sha256,
        source_snapshot_key=source_snapshot_key,
        identity_policy_key=identity_policy_key,
        source_assessment_scheme=DATA_S1_SOURCE_ASSESSMENT_SCHEME,
        source_assessment=source_assessment,
        assembly_accession_version=assembly,
        sequence_accession_version=contig,
        native_vr_token=vr_token,
        assembly_resolution=assembly_resolution,
        contig_resolution=contig_resolution,
        authority_contig_length=authority_contig_length,
        contig_length=contig_length,
        start0=start0,
        end0=end0,
        length=length,
        coordinate_system=DATA_S1_COORDINATE_SYSTEM,
        viral_major_taxon=raw_row["Viral Major Taxon"],
        host_class=raw_row["Class"],
        vr_type=vr_type,
        source_hcvr=hcvr,
        locator=locator,
        raw_row=dict(raw_row),
    )


def _parse_integer(
    raw_row: Mapping[str, str],
    field_name: str,
    issues: list[DataS1ValidationIssue],
) -> int | None:
    raw_value = raw_row[field_name]
    if _UNSIGNED_INTEGER_RE.fullmatch(raw_value) is None:
        issues.append(
            _issue(
                "invalid_integer",
                field_name,
                "expected an unsigned base-10 integer without rounding or coercion",
                raw_value,
            )
        )
        return None
    return int(raw_value)


def _resolve_against_ncbi(
    resolution_index: NcbiResolutionIndex | None,
    assembly: str,
    contig: str,
    source_contig_length: int | None,
    issues: list[DataS1ValidationIssue],
) -> tuple[ResolutionStatus, ResolutionStatus, int | None]:
    if resolution_index is None:
        return "not_checked", "not_checked", None

    if assembly not in resolution_index.assemblies:
        issues.append(
            _issue(
                "ncbi_assembly_not_resolved",
                "Assembly",
                "exact assembly accession.version was absent from the frozen NCBI report",
                assembly,
            )
        )
        return "unresolved", "unresolved", None

    authority_length = resolution_index.sequence_length(assembly, contig)
    if authority_length is None:
        issues.append(
            _issue(
                "ncbi_sequence_not_resolved",
                "Contig",
                "exact sequence accession.version was absent within the assembly",
                contig,
            )
        )
        return "exact", "unresolved", None

    if source_contig_length is None or source_contig_length <= 0:
        return "exact", "length_unverified", authority_length

    if source_contig_length != authority_length:
        issues.append(
            _issue(
                "ncbi_contig_length_mismatch",
                "Contig Length",
                f"source length differs from frozen NCBI length {authority_length}",
                str(source_contig_length),
            )
        )
        return "exact", "length_mismatch", authority_length

    return "exact", "exact", authority_length


def _consume_verified_jsonl(
    path: str | os.PathLike[str],
    label: str,
    consumer: Callable[[int, Mapping[str, object]], None],
    *,
    expected_sha256: str | None,
    expected_byte_size: int | None,
) -> FileByteObservation:
    """Parse and hash the same binary stream, eliminating verify/parse TOCTOU."""

    if expected_sha256 is not None:
        _require_sha256("expected_sha256", expected_sha256)
    if (
        expected_byte_size is not None
        and (type(expected_byte_size) is not int or expected_byte_size < 0)
    ):
        raise ValueError("expected_byte_size must be a non-negative integer")

    file_path = Path(path)
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with file_path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                digest.update(raw_line)
                byte_size += len(raw_line)
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise NcbiResolutionIndexError(
                        f"invalid JSON in {label} at line {line_number}: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise NcbiResolutionIndexError(
                        f"{label} line {line_number} must contain a JSON object"
                    )
                consumer(line_number, value)
    except OSError as exc:
        raise NcbiResolutionIndexError(f"cannot read {label} {path}: {exc}") from exc

    actual_sha256 = digest.hexdigest()
    mismatches: list[str] = []
    if expected_byte_size is not None and byte_size != expected_byte_size:
        mismatches.append(
            f"byte_size expected {expected_byte_size}, observed {byte_size}"
        )
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        mismatches.append(
            f"sha256 expected {expected_sha256}, observed {actual_sha256}"
        )
    if mismatches:
        raise FileByteVerificationError(
            f"frozen file verification failed for {file_path}: " + "; ".join(mismatches)
        )
    return FileByteObservation(
        path=file_path,
        byte_size=byte_size,
        sha256=actual_sha256,
    )


def data_s1_record_key(
    artifact_sha256: str,
    source_snapshot_key: str,
    locator: SourceRowLocator,
    *,
    assembly_accession_version: str,
    sequence_accession_version: str,
    native_vr_token: str,
    method_run_identity: str,
) -> str:
    """Return the D08 call key for one source-native occurrence and method run."""

    _require_sha256("artifact_sha256", artifact_sha256)
    _require_exact_token("source_snapshot_key", source_snapshot_key)
    _require_exact_token("locator.worksheet", locator.worksheet)
    _require_exact_token("assembly_accession_version", assembly_accession_version)
    _require_exact_token("sequence_accession_version", sequence_accession_version)
    _require_exact_token("native_vr_token", native_vr_token)
    _require_exact_token("method_run_identity", method_run_identity)
    return stable_key(
        _RECORD_KEY_NAMESPACE,
        {
            "artifact_sha256": artifact_sha256,
            "key_schema": _RECORD_KEY_SCHEMA,
            "method_run_identity": method_run_identity,
            "source_native_record_key": {
                "assembly_accession_version": assembly_accession_version,
                "native_vr_token": native_vr_token,
                "sequence_accession_version": sequence_accession_version,
            },
            "source_snapshot_key": source_snapshot_key,
            "worksheet": locator.worksheet,
        },
    )


def data_s1_source_record_key(
    artifact_sha256: str,
    source_snapshot_key: str,
    locator: SourceRowLocator,
) -> str:
    """Return the immutable physical-row identity, independent of a method run."""

    _require_sha256("artifact_sha256", artifact_sha256)
    _require_exact_token("source_snapshot_key", source_snapshot_key)
    _require_exact_token("locator.worksheet", locator.worksheet)
    if type(locator.excel_row) is not int or locator.excel_row <= 1:
        raise ValueError("locator.excel_row must be an integer greater than one")
    return stable_key(
        _SOURCE_RECORD_KEY_NAMESPACE,
        {
            "artifact_sha256": artifact_sha256,
            "excel_row": locator.excel_row,
            "key_schema": _SOURCE_RECORD_KEY_SCHEMA,
            "source_snapshot_key": source_snapshot_key,
            "worksheet": locator.worksheet,
        },
    )


def _issue(code: str, field_name: str, message: str, raw_value: str) -> DataS1ValidationIssue:
    return DataS1ValidationIssue(
        code=code,
        field=field_name,
        message=message,
        raw_value=raw_value,
    )
