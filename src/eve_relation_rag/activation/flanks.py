"""Exact flank request planning and offline validation of downloaded wrappers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    FLANK_ASSESSMENT_POLICY_KEY,
    FLANK_WINDOW_BP,
    AdjudicationCohortManifest,
    CohortRecord,
    FetchToolIdentity,
    FlankEvidenceManifest,
    FlankEvidenceRecord,
    FlankEvidenceRequest,
    FlankEvidenceRequestPlan,
    FlankSideEvidence,
    FullSequenceBundleIndexRow,
    FullSequenceBundleManifest,
    FullSequenceBundleRecord,
    NcbiSequenceFetchWrapper,
    canonical_model_sha256,
    canonical_revalidate,
    seal_manifest_payload,
)
from eve_relation_rag.importers.data_s1 import FileByteObservation, verify_file_bytes


class FlankEvidenceError(ValueError):
    """Raised when request or downloaded-wrapper integrity cannot be established."""


@dataclass(frozen=True, slots=True)
class LoadedSequenceWrapper:
    """Strict wrapper plus its independently measured physical file identity."""

    wrapper: NcbiSequenceFetchWrapper
    file_observation: FileByteObservation


@dataclass(frozen=True, slots=True)
class LoadedFullSequenceBundle:
    """Verified aggregate wrapper, provenance sidecar, and exact accession index."""

    manifest: FullSequenceBundleManifest
    records: MappingProxyType[str, FullSequenceBundleRecord]
    file_observation: FileByteObservation


@dataclass(frozen=True, slots=True)
class PrimaryFlankArtifacts:
    """Complete initial 71-locus request plan and evidence manifest."""

    request_plan: FlankEvidenceRequestPlan
    evidence_manifest: FlankEvidenceManifest


_FULL_SEQUENCE_BUNDLE_ADAPTER = TypeAdapter(tuple[FullSequenceBundleRecord, ...])
type BoundaryBase = Literal[
    "A", "C", "G", "T", "R", "Y", "S", "W", "K", "M", "B", "D", "H", "V", "N"
]


def build_flank_request_plan(
    cohort: AdjudicationCohortManifest,
    records: Sequence[CohortRecord] | None = None,
) -> FlankEvidenceRequestPlan:
    """Build canonical NCBI 1-based inclusive ranges for selected cohort rows.

    When ``records`` is omitted the initial plan contains all 71 primary records.
    Expansion records may be supplied only if they already occur in the frozen
    per-assembly queues; this function never invents or reorders a candidate.
    """

    try:
        cohort = canonical_revalidate(cohort)
        if records is not None:
            records = tuple(canonical_revalidate(record) for record in records)
    except ValidationError as exc:
        raise FlankEvidenceError("request-plan input failed canonical validation") from exc

    selected = cohort.primary_records if records is None else tuple(records)
    if not selected:
        raise FlankEvidenceError("at least one frozen cohort record is required")

    primary_keys = {row.locus_key for row in cohort.primary_records}
    expansion_keys = {row.locus_key for queue in cohort.expansion_queues for row in queue.records}
    known_by_locus = {
        row.locus_key: row
        for row in (
            *cohort.primary_records,
            *(row for queue in cohort.expansion_queues for row in queue.records),
        )
    }
    requests: list[FlankEvidenceRequest] = []
    for record in selected:
        frozen = known_by_locus.get(record.locus_key)
        if frozen is None or frozen != record:
            raise FlankEvidenceError("request record is not an exact frozen cohort row")
        tier: Literal["primary", "expansion"] = (
            "primary" if record.locus_key in primary_keys else "expansion"
        )
        if tier == "expansion" and record.locus_key not in expansion_keys:
            raise FlankEvidenceError("request record has no frozen expansion tier")
        requests.append(_request_for_record(cohort.manifest_sha256, record, tier=tier))

    canonical_requests = tuple(
        sorted(
            requests,
            key=lambda row: (
                row.assembly_accession_version,
                row.source_row,
                row.locus_key,
            ),
        )
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "flank-evidence-request-plan-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "inspection_window_bp": FLANK_WINDOW_BP,
        "requests": canonical_requests,
    }
    return FlankEvidenceRequestPlan.model_validate(seal_manifest_payload(payload))


def load_sequence_wrapper(
    path: str | Path,
    *,
    expected_file_sha256: str | None = None,
    expected_file_byte_size: int | None = None,
) -> LoadedSequenceWrapper:
    """Load an already-downloaded JSON wrapper after physical byte verification."""

    observation = verify_file_bytes(
        path,
        expected_sha256=expected_file_sha256,
        expected_byte_size=expected_file_byte_size,
    )
    try:
        payload = Path(path).read_text(encoding="utf-8")
        wrapper = NcbiSequenceFetchWrapper.model_validate_json(payload)
    except (OSError, UnicodeError, ValidationError) as exc:
        raise FlankEvidenceError("sequence wrapper is unreadable or invalid") from exc
    return LoadedSequenceWrapper(wrapper=wrapper, file_observation=observation)


def load_full_sequence_bundle(
    path: str | Path,
    *,
    expected_file_sha256: str,
    expected_file_byte_size: int,
    source_uri: str,
    retrieved_at: str,
    tool_version: str,
    http_status: int = 200,
) -> LoadedFullSequenceBundle:
    """Validate the downloaded 69-contig aggregate JSON and build its sidecar manifest."""

    observation = verify_file_bytes(
        path,
        expected_sha256=expected_file_sha256,
        expected_byte_size=expected_file_byte_size,
    )
    try:
        records = _FULL_SEQUENCE_BUNDLE_ADAPTER.validate_json(
            Path(path).read_text(encoding="utf-8"), strict=True
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise FlankEvidenceError("full-sequence bundle is unreadable or invalid") from exc
    accessions = tuple(row.accession for row in records)
    if accessions != tuple(sorted(accessions)) or len(accessions) != len(set(accessions)):
        raise FlankEvidenceError("full-sequence bundle accessions must be unique and sorted")

    tool = FetchToolIdentity(
        tool_name="ncbi-sequence-fetch",
        tool_version=tool_version,
        parser_policy_key="parser:ncbi-full-sequence-bundle-v1",
    )
    index = tuple(
        FullSequenceBundleIndexRow(
            accession_version=row.accession,
            sequence_length=row.length,
            normalized_sequence_sha256=_sequence_sha256(row.sequence),
        )
        for row in records
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "ncbi-full-sequence-bundle-manifest-v1",
        "artifact_sha256": observation.sha256,
        "artifact_byte_size": observation.byte_size,
        "source_uri": source_uri,
        "retrieved_at": retrieved_at,
        "http_status": http_status,
        "acquisition_requests_per_second": 3,
        "api_key_used": False,
        "tool": tool,
        "record_count": len(records),
        "total_sequence_bp": sum(row.length for row in records),
        "records": index,
    }
    manifest = FullSequenceBundleManifest.model_validate(seal_manifest_payload(payload))
    return LoadedFullSequenceBundle(
        manifest=manifest,
        records=MappingProxyType({row.accession: row for row in records}),
        file_observation=observation,
    )


def materialize_range_wrapper(
    request: FlankEvidenceRequest,
    bundle: LoadedFullSequenceBundle,
) -> LoadedSequenceWrapper:
    """Derive the frozen locus range from an exact full-contig bundle record."""

    try:
        request = canonical_revalidate(request)
        bundle_manifest = canonical_revalidate(bundle.manifest)
    except ValidationError as exc:
        raise FlankEvidenceError("range materialization input failed validation") from exc

    record = bundle.records.get(request.sequence_accession_version)
    if record is None:
        raise FlankEvidenceError("full-sequence bundle lacks the requested accession.version")
    try:
        record = canonical_revalidate(record)
    except ValidationError as exc:
        raise FlankEvidenceError("bundle record failed canonical validation") from exc
    start_index = request.ncbi_range_start1 - 1
    stop_index = request.ncbi_range_end1
    normalized = record.sequence[start_index:stop_index]
    payload: dict[str, object] = {
        "wrapper_schema_version": "ncbi-sequence-fetch-wrapper-v1",
        "request_sha256": request.request_sha256,
        "status": "success",
        "requested_accession_version": request.sequence_accession_version,
        "resolved_accession_version": record.accession,
        "ncbi_range_start1": request.ncbi_range_start1,
        "ncbi_range_end1": request.ncbi_range_end1,
        "full_sequence_length": record.length,
        "retrieved_at": bundle_manifest.retrieved_at,
        "source_uri": bundle_manifest.source_uri,
        "http_status": bundle_manifest.http_status,
        "response_byte_size": bundle.file_observation.byte_size,
        "response_sha256": bundle.file_observation.sha256,
        "normalized_sequence": normalized,
        "normalized_sequence_sha256": _sequence_sha256(normalized),
        "error_code": None,
        "tool": bundle_manifest.tool,
    }
    sealed = dict(payload)
    sealed["wrapper_sha256"] = canonical_model_sha256(payload)
    wrapper = NcbiSequenceFetchWrapper.model_validate(sealed)
    return LoadedSequenceWrapper(wrapper=wrapper, file_observation=bundle.file_observation)


def materialize_primary_flank_artifacts(
    cohort: AdjudicationCohortManifest,
    bundle: LoadedFullSequenceBundle,
    *,
    assessed_by: str,
    assessed_at: str,
) -> PrimaryFlankArtifacts:
    """Materialize all 71 primary assessments from the exact aggregate bundle."""

    request_plan = build_flank_request_plan(cohort)
    records = tuple(
        assess_sequence_wrapper(
            request,
            materialize_range_wrapper(request, bundle),
            assessed_by=assessed_by,
            assessed_at=assessed_at,
        )
        for request in request_plan.requests
    )
    evidence = build_flank_evidence_manifest(cohort, request_plan, records)
    return PrimaryFlankArtifacts(
        request_plan=request_plan,
        evidence_manifest=evidence,
    )


def assess_sequence_wrapper(
    request: FlankEvidenceRequest,
    loaded: LoadedSequenceWrapper,
    *,
    assessed_by: str,
    assessed_at: str,
) -> FlankEvidenceRecord:
    """Turn one checksum-verified wrapper into independent left/right assessments."""

    try:
        request = canonical_revalidate(request)
        wrapper = canonical_revalidate(loaded.wrapper)
    except ValidationError as exc:
        raise FlankEvidenceError("flank assessment input failed canonical validation") from exc
    _validate_wrapper_matches_request(request, wrapper)

    if wrapper.status != "success":
        left = _empty_side(
            side="left",
            available_bp=request.expected_left_bp,
            reason_code="sequence_evidence_unavailable",
        )
        right = _empty_side(
            side="right",
            available_bp=request.expected_right_bp,
            reason_code="sequence_evidence_unavailable",
        )
    else:
        assert wrapper.normalized_sequence is not None
        if wrapper.resolved_accession_version != request.sequence_accession_version:
            left = _contradicted_side(
                "left", request.expected_left_bp, "accession_version_conflict"
            )
            right = _contradicted_side(
                "right", request.expected_right_bp, "accession_version_conflict"
            )
        elif wrapper.full_sequence_length != request.sequence_length:
            left = _contradicted_side("left", request.expected_left_bp, "sequence_length_conflict")
            right = _contradicted_side(
                "right", request.expected_right_bp, "sequence_length_conflict"
            )
        else:
            locus_start_offset = request.locus_start0 - request.request_start0
            locus_end_offset = request.locus_end0 - request.request_start0
            sequence = wrapper.normalized_sequence
            left = _assess_side(
                side="left",
                sequence=sequence[:locus_start_offset],
                available_bp=request.expected_left_bp,
            )
            right = _assess_side(
                side="right",
                sequence=sequence[locus_end_offset:],
                available_bp=request.expected_right_bp,
            )

    payload: dict[str, object] = {
        "request_sha256": request.request_sha256,
        "wrapper_sha256": wrapper.wrapper_sha256,
        "wrapper_file_sha256": loaded.file_observation.sha256,
        "source_record_key": request.source_record_key,
        "source_row": request.source_row,
        "locus_key": request.locus_key,
        "interval_key": request.interval_key,
        "placement_key": request.placement_key,
        "interval_basis": request.interval_basis,
        "assessment_policy_key": FLANK_ASSESSMENT_POLICY_KEY,
        "assessed_by": assessed_by,
        "assessed_at": assessed_at,
        "source_uri": wrapper.source_uri,
        "response_sha256": wrapper.response_sha256,
        "normalized_sequence_sha256": wrapper.normalized_sequence_sha256,
        "left": left,
        "right": right,
    }
    sealed = dict(payload)
    sealed["record_sha256"] = _record_sha256(payload)
    return FlankEvidenceRecord.model_validate(sealed)


def build_flank_evidence_manifest(
    cohort: AdjudicationCohortManifest,
    request_plan: FlankEvidenceRequestPlan,
    records: Iterable[FlankEvidenceRecord],
) -> FlankEvidenceManifest:
    """Bind a unique evidence record to every request in a plan."""

    try:
        cohort = canonical_revalidate(cohort)
        request_plan = canonical_revalidate(request_plan)
        records = tuple(canonical_revalidate(record) for record in records)
    except ValidationError as exc:
        raise FlankEvidenceError("flank manifest input failed canonical validation") from exc

    if request_plan.cohort_manifest_sha256 != cohort.manifest_sha256:
        raise FlankEvidenceError("request plan does not belong to the cohort")
    canonical_records = tuple(sorted(records, key=lambda row: (row.source_row, row.locus_key)))
    by_request = {row.request_sha256: row for row in canonical_records}
    if len(by_request) != len(canonical_records):
        raise FlankEvidenceError("duplicate request evidence")
    planned = {row.request_sha256 for row in request_plan.requests}
    if set(by_request) != planned:
        raise FlankEvidenceError("evidence manifest must cover exactly the request plan")
    payload: dict[str, object] = {
        "manifest_schema_version": "flank-evidence-manifest-v1",
        "release_key": ACTIVATION_RELEASE_KEY,
        "cohort_manifest_sha256": cohort.manifest_sha256,
        "request_plan_manifest_sha256": request_plan.manifest_sha256,
        "records": canonical_records,
    }
    return FlankEvidenceManifest.model_validate(seal_manifest_payload(payload))


def _request_for_record(
    cohort_manifest_sha256: str,
    record: CohortRecord,
    *,
    tier: Literal["primary", "expansion"],
) -> FlankEvidenceRequest:
    start0 = max(0, record.start0 - FLANK_WINDOW_BP)
    end0 = min(record.sequence_length, record.end0 + FLANK_WINDOW_BP)
    payload: dict[str, object] = {
        "cohort_manifest_sha256": cohort_manifest_sha256,
        "selection_tier": tier,
        "source_record_key": record.source_record_key,
        "source_row": record.source_row,
        "locus_key": record.locus_key,
        "interval_key": record.interval_key,
        "placement_key": record.placement_key,
        "interval_basis": record.interval_basis,
        "assembly_accession_version": record.assembly_accession_version,
        "sequence_accession_version": record.sequence_accession_version,
        "sequence_length": record.sequence_length,
        "locus_start0": record.start0,
        "locus_end0": record.end0,
        "request_start0": start0,
        "request_end0": end0,
        "ncbi_range_start1": start0 + 1,
        "ncbi_range_end1": end0,
        "expected_left_bp": record.start0 - start0,
        "expected_right_bp": end0 - record.end0,
        "inspection_window_bp": FLANK_WINDOW_BP,
        "database": "nuccore",
        "rettype": "fasta",
        "retmode": "text",
        "strand": "plus",
    }
    sealed = dict(payload)
    sealed["request_sha256"] = _record_sha256(payload)
    return FlankEvidenceRequest.model_validate(sealed)


def _validate_wrapper_matches_request(
    request: FlankEvidenceRequest, wrapper: NcbiSequenceFetchWrapper
) -> None:
    if wrapper.request_sha256 != request.request_sha256:
        raise FlankEvidenceError("wrapper belongs to a different request")
    if wrapper.requested_accession_version != request.sequence_accession_version:
        raise FlankEvidenceError("wrapper requested a different accession.version")
    if (
        wrapper.ncbi_range_start1 != request.ncbi_range_start1
        or wrapper.ncbi_range_end1 != request.ncbi_range_end1
    ):
        raise FlankEvidenceError("wrapper range differs from the frozen request")


def _assess_side(
    *,
    side: Literal["left", "right"],
    sequence: str,
    available_bp: int,
) -> FlankSideEvidence:
    if len(sequence) != available_bp:
        return _contradicted_side(side, available_bp, "response_coordinate_conflict")
    if not sequence:
        return _empty_side(side=side, available_bp=0, reason_code="no_adjacent_base")
    boundary = sequence[-1] if side == "left" else sequence[0]
    ambiguous = sum(base not in "ACGT" for base in sequence)
    longest_run = _longest_ambiguity_run(sequence)
    supported = boundary in "ACGT"
    return FlankSideEvidence(
        side=side,
        verdict="supported" if supported else "insufficient",
        reason_code="inspectable_adjacent_sequence" if supported else "ambiguous_boundary_base",
        available_bp=available_bp,
        inspected_bp=len(sequence),
        ambiguous_bp=ambiguous,
        ambiguity_fraction=_fraction(ambiguous, len(sequence)),
        longest_ambiguity_run=longest_run,
        boundary_base=cast(BoundaryBase, boundary),
    )


def _empty_side(
    *, side: Literal["left", "right"], available_bp: int, reason_code: str
) -> FlankSideEvidence:
    return FlankSideEvidence(
        side=side,
        verdict="insufficient",
        reason_code=reason_code,
        available_bp=available_bp,
        inspected_bp=0,
        ambiguous_bp=0,
        ambiguity_fraction="0.000000",
        longest_ambiguity_run=0,
        boundary_base=None,
    )


def _contradicted_side(
    side: Literal["left", "right"], available_bp: int, reason_code: str
) -> FlankSideEvidence:
    return FlankSideEvidence(
        side=side,
        verdict="contradicted",
        reason_code=reason_code,
        available_bp=available_bp,
        inspected_bp=0,
        ambiguous_bp=0,
        ambiguity_fraction="0.000000",
        longest_ambiguity_run=0,
        boundary_base=None,
    )


def _fraction(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.000000"
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    return f"{value:.6f}"


def _longest_ambiguity_run(sequence: str) -> int:
    longest = 0
    current = 0
    for base in sequence:
        if base in "ACGT":
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _record_sha256(payload: dict[str, object]) -> str:
    return canonical_model_sha256(payload)


def _sequence_sha256(sequence: str) -> str:
    import hashlib

    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


__all__ = [
    "FlankEvidenceError",
    "LoadedSequenceWrapper",
    "LoadedFullSequenceBundle",
    "PrimaryFlankArtifacts",
    "assess_sequence_wrapper",
    "build_flank_evidence_manifest",
    "build_flank_request_plan",
    "load_sequence_wrapper",
    "load_full_sequence_bundle",
    "materialize_range_wrapper",
    "materialize_primary_flank_artifacts",
]
