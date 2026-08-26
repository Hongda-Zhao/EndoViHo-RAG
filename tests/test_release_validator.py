from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from eve_relation_rag.domain.keys import LocusIdentity
from eve_relation_rag.importers.audit import (
    APPROVED_DATA_S1_EXPECTED_COUNTS,
    APPROVED_DATA_S1_KEY_DIGESTS,
)
from eve_relation_rag.importers.data_s1 import DATA_S1_ARTIFACT_SHA256
from eve_relation_rag.releases.validator import (
    FlankEvidence,
    IctvReleaseEvidence,
    InclusionEvidence,
    NcbiTaxonomyEvidence,
    PlacementEvidence,
    ReleaseMembershipCandidate,
    ReleaseValidationRequest,
    SourceAuditEvidence,
    SourceManifestEvidence,
    validate_release,
)

SOURCE_SNAPSHOT_KEY = "study-defined:10.1101/2025.04.19.649669:v4:data-s1"
IDENTITY_POLICY_VERSION = "zhao-v4-contig-source-occurrence-v1"
MANIFEST_SHA256 = "afa5982542c592aaec6ec1033e0ac9ebbd3786e881baed0d81a1a602a30adf0d"
ARTIFACT_SHA256 = DATA_S1_ARTIFACT_SHA256
ARTIFACT_URI = (
    "https://www.biorxiv.org/content/biorxiv/early/2026/05/21/"
    "2025.04.19.649669/DC6/embed/media-6.xlsx?download=true"
)


def _source() -> SourceManifestEvidence:
    return SourceManifestEvidence(
        source_snapshot_key=SOURCE_SNAPSHOT_KEY,
        manifest_sha256=MANIFEST_SHA256,
        verified_manifest_sha256=MANIFEST_SHA256,
        artifact_key=(
            "source-artifact:biorxiv-data-s1:sha256:"
            "4b9090d9f3e651179680361af19097e1b5d2ab267da4f221caa6838a6b240150"
        ),
        artifact_sha256=ARTIFACT_SHA256,
        verified_artifact_sha256=ARTIFACT_SHA256,
        license_key="CC-BY-NC-ND-4.0",
        verified_license_key="CC-BY-NC-ND-4.0",
        provenance_uri=ARTIFACT_URI,
        remote_artifact_verified=True,
        remote_artifact_uri=ARTIFACT_URI,
        remote_retrieved_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def _source_audit() -> SourceAuditEvidence:
    return SourceAuditEvidence(
        audit_schema="endoviho-milestone1-source-audit-v1",
        audit_artifact_sha256="3" * 64,
        verified_audit_artifact_sha256="3" * 64,
        passed=True,
        expected_source_record_count=APPROVED_DATA_S1_EXPECTED_COUNTS["source_records"],
        observed_source_record_count=APPROVED_DATA_S1_EXPECTED_COUNTS["source_records"],
        expected_accounted_quarantine_count=APPROVED_DATA_S1_EXPECTED_COUNTS[
            "vr_type_viral_contig"
        ],
        expected_call_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
            "sorted_call_keys_sha256"
        ],
        observed_call_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
            "sorted_call_keys_sha256"
        ],
        expected_locus_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
            "sorted_locus_keys_sha256"
        ],
        observed_locus_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
            "sorted_locus_keys_sha256"
        ],
    )


def _ncbi_taxonomy() -> NcbiTaxonomyEvidence:
    return NcbiTaxonomyEvidence(
        snapshot_key="lineage-snapshot:ncbi-taxonomy:2026-08-26",
        authority="NCBI Taxonomy",
        version="taxdump-2026-08-26",
        artifact_key="source-artifact:ncbi-taxdump:2026-08-26",
        artifact_sha256="8" * 64,
        verified_artifact_sha256="8" * 64,
        provenance_uri="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
        usage_basis_key="NCBI-MOLECULAR-DATA-USAGE-POLICY",
        retrieved_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        release_bound=True,
        merged_history_included=True,
        deleted_history_included=True,
    )


def _ictv() -> IctvReleaseEvidence:
    return IctvReleaseEvidence(
        msl_snapshot_key="lineage-snapshot:ictv:msl41-v1",
        msl_version="MSL41 v1",
        msl_artifact_key="source-artifact:ictv:msl41-v1",
        msl_artifact_sha256="9" * 64,
        verified_msl_artifact_sha256="9" * 64,
        vmr_artifact_key="source-artifact:ictv:vmr-msl41-corrected",
        vmr_artifact_sha256="a" * 64,
        verified_vmr_artifact_sha256="a" * 64,
        provenance_uri="https://ictv.global/msl/current",
        license_key="ICTV-DATA-USE",
        retrieved_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        msl_release_bound=True,
        vmr_release_bound=True,
        vmr_corrected=True,
    )


def _identity(*, native_vr_token: str = "vr3") -> LocusIdentity:
    return LocusIdentity(
        source_snapshot_key=SOURCE_SNAPSHOT_KEY,
        assembly_accession_version="GCA_945859735.2",
        contig_accession_version="CAMAOU020000182.1",
        native_vr_token=native_vr_token,
        identity_policy_version=IDENTITY_POLICY_VERSION,
    )


def _placement() -> PlacementEvidence:
    return PlacementEvidence(
        contig_accession_version="CAMAOU020000182.1",
        start0=210479,
        end0=248796,
        precision="exact",
        coordinate_system="0-based-half-open",
        provenance_key="source-record:zhao-v4:data-s1:row-19239",
    )


def _flanks() -> tuple[FlankEvidence, FlankEvidence]:
    return (
        FlankEvidence(
            side="left",
            verdict="supported",
            policy_key="flank-policy:zhao-v4-pilot-v1",
            evidence_key="evidence:left:row-19239",
            inspection_window_bp=20_000,
            available_bp=210_479,
            inspected_bp=20_000,
            method_or_curator_key="method:flank-review:v1",
            evidence_sha256="6" * 64,
        ),
        FlankEvidence(
            side="right",
            verdict="supported",
            policy_key="flank-policy:zhao-v4-pilot-v1",
            evidence_key="evidence:right:row-19239",
            inspection_window_bp=20_000,
            available_bp=751_204,
            inspected_bp=20_000,
            method_or_curator_key="method:flank-review:v1",
            evidence_sha256="7" * 64,
        ),
    )


def _candidate(
    *,
    native_vr_token: str = "vr3",
    source_assessment: str | None = "source_high",
) -> ReleaseMembershipCandidate:
    identity = _identity(native_vr_token=native_vr_token)
    return ReleaseMembershipCandidate(
        locus_key=identity.key(),
        identity=identity,
        assembly_accession_version=identity.assembly_accession_version,
        assembly_resolution="exact",
        contig_accession_version=identity.contig_accession_version,
        contig_resolution="exact",
        contig_length=1_000_000,
        source_record_key=f"source-record:zhao-v4:data-s1:{native_vr_token}",
        method_key="method:zhao-v4-hcvr",
        import_run_key="import-run:zhao-v4-data-s1-v1",
        source_assessment=source_assessment,
        placements=(_placement(),),
        flank_assessments=_flanks(),
        inclusion=InclusionEvidence(
            decision="include",
            policy_key="inclusion-policy:zhao-v4-pilot-v1",
            authorized_by="curator:endoviho",
        ),
    )


def _request(
    candidate: ReleaseMembershipCandidate | None = None,
    *,
    source: SourceManifestEvidence | None = None,
) -> ReleaseValidationRequest:
    return ReleaseValidationRequest(
        release_key="release:endoviho-rag:v0:20260826:001",
        source=source or _source(),
        source_audit=_source_audit(),
        ncbi_taxonomy=_ncbi_taxonomy(),
        ictv=_ictv(),
        candidates=(candidate or _candidate(),),
        accounted_quarantine_count=527,
    )


def _error_codes(request: ReleaseValidationRequest) -> set[str]:
    return {issue.code for issue in validate_release(request).errors}


def test_release_key_must_follow_approved_immutable_grammar() -> None:
    request = replace(_request(), release_key="release:endoviho-rag:latest")

    assert "release_key_invalid" in _error_codes(request)


@pytest.mark.parametrize("source_assessment", ["source_high", "source_low"])
def test_complete_candidate_passes_with_either_source_assessment(
    source_assessment: str,
) -> None:
    report = validate_release(_request(_candidate(source_assessment=source_assessment)))

    assert report.valid is True
    assert report.errors == ()
    assert {warning.code for warning in report.warnings} == {
        "accounted_quarantine_rows_retained",
        "source_assessment_non_authoritative",
    }
    assert report.counts.candidate_count == 1
    assert report.counts.eligible_membership_count == 1
    assert report.counts.blocked_membership_count == 0
    assert report.counts.explicit_include_count == 1
    assert report.counts.source_high_count == (source_assessment == "source_high")
    assert report.counts.source_low_count == (source_assessment == "source_low")
    assert report.counts.audited_source_record_count == 39_495
    json.dumps(report.to_dict())


def test_empty_release_and_missing_whole_ledger_audit_fail_closed() -> None:
    empty = replace(_request(), candidates=())
    missing_audit = replace(_request(), source_audit=None)

    assert "release_candidates_empty" in _error_codes(empty)
    assert "source_audit_missing" in _error_codes(missing_audit)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"passed": False}, "source_audit_not_passed"),
        (
            {"observed_source_record_count": 0},
            "source_audit_record_count_mismatch",
        ),
        (
            {"observed_call_keys_sha256": "8" * 64},
            "source_audit_checksum_mismatch",
        ),
    ],
)
def test_whole_ledger_audit_must_match_frozen_evidence(
    mutation: dict[str, object], expected_code: str
) -> None:
    audit = replace(_source_audit(), **mutation)

    assert expected_code in _error_codes(replace(_request(), source_audit=audit))


@pytest.mark.parametrize(
    ("audit", "accounted_quarantine_count", "expected_code"),
    [
        (
            replace(
                _source_audit(),
                expected_source_record_count=1,
                observed_source_record_count=1,
            ),
            527,
            "source_audit_record_count_not_canonical",
        ),
        (
            replace(
                _source_audit(),
                expected_accounted_quarantine_count=526,
            ),
            526,
            "source_audit_quarantine_count_not_canonical",
        ),
        (
            replace(
                _source_audit(),
                expected_call_keys_sha256="b" * 64,
                observed_call_keys_sha256="b" * 64,
            ),
            527,
            "source_audit_call_digest_not_canonical",
        ),
        (
            replace(
                _source_audit(),
                expected_locus_keys_sha256="c" * 64,
                observed_locus_keys_sha256="c" * 64,
            ),
            527,
            "source_audit_locus_digest_not_canonical",
        ),
    ],
)
def test_self_consistent_but_noncanonical_source_audit_fails_closed(
    audit: SourceAuditEvidence,
    accounted_quarantine_count: int,
    expected_code: str,
) -> None:
    request = replace(
        _request(),
        source_audit=audit,
        accounted_quarantine_count=accounted_quarantine_count,
    )

    assert expected_code in _error_codes(request)


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            replace(_source(), source_snapshot_key="study-defined:replacement"),
            "source_snapshot_not_canonical",
        ),
        (
            replace(
                _source(),
                manifest_sha256="d" * 64,
                verified_manifest_sha256="d" * 64,
            ),
            "source_manifest_not_canonical",
        ),
        (
            replace(
                _source(),
                artifact_sha256="e" * 64,
                verified_artifact_sha256="e" * 64,
            ),
            "source_artifact_not_canonical",
        ),
        (
            replace(
                _source(),
                provenance_uri="https://example.invalid/repacked.xlsx",
                remote_artifact_uri="https://example.invalid/repacked.xlsx",
            ),
            "source_uri_not_canonical",
        ),
    ],
)
def test_self_consistent_but_noncanonical_source_manifest_fails_closed(
    source: SourceManifestEvidence,
    expected_code: str,
) -> None:
    assert expected_code in _error_codes(_request(source=source))


@pytest.mark.parametrize(
    ("ncbi_taxonomy", "expected_code"),
    [
        (None, "ncbi_taxonomy_evidence_missing"),
        (
            replace(_ncbi_taxonomy(), merged_history_included=False),
            "ncbi_taxonomy_history_incomplete",
        ),
        (
            replace(_ncbi_taxonomy(), deleted_history_included=False),
            "ncbi_taxonomy_history_incomplete",
        ),
        (
            replace(_ncbi_taxonomy(), release_bound=False),
            "ncbi_taxonomy_not_release_bound",
        ),
    ],
)
def test_complete_release_bound_ncbi_taxonomy_history_is_required(
    ncbi_taxonomy: NcbiTaxonomyEvidence | None,
    expected_code: str,
) -> None:
    request = replace(_request(), ncbi_taxonomy=ncbi_taxonomy)
    assert expected_code in _error_codes(request)


@pytest.mark.parametrize(
    ("ictv", "expected_code"),
    [
        (None, "ictv_release_evidence_missing"),
        (
            replace(_ictv(), msl_version="MSL40"),
            "ictv_msl_version_invalid",
        ),
        (
            replace(_ictv(), vmr_release_bound=False),
            "ictv_artifact_not_release_bound",
        ),
        (
            replace(_ictv(), vmr_corrected=False),
            "ictv_vmr_not_corrected",
        ),
    ],
)
def test_ictv_msl41_and_corrected_vmr_must_be_release_bound(
    ictv: IctvReleaseEvidence | None,
    expected_code: str,
) -> None:
    request = replace(_request(), ictv=ictv)
    assert expected_code in _error_codes(request)


@pytest.mark.parametrize("source_assessment", ["source_high", "source_low"])
def test_source_assessment_never_substitutes_for_explicit_include(
    source_assessment: str,
) -> None:
    candidate = replace(
        _candidate(source_assessment=source_assessment),
        inclusion=None,
    )

    report = validate_release(_request(candidate))

    assert report.valid is False
    assert "inclusion_decision_missing" in {issue.code for issue in report.errors}
    assert report.counts.explicit_include_count == 0
    assert report.counts.eligible_membership_count == 0
    assert report.counts.blocked_membership_count == 1


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (replace(_source(), manifest_sha256=None), "source_checksum_invalid"),
        (
            replace(_source(), verified_artifact_sha256="3" * 64),
            "source_checksum_mismatch",
        ),
        (replace(_source(), license_key=None), "source_metadata_missing"),
        (
            replace(_source(), verified_license_key="license:restricted"),
            "source_license_mismatch",
        ),
        (replace(_source(), provenance_uri=None), "source_metadata_missing"),
        (replace(_source(), remote_artifact_verified=False), "remote_artifact_not_verified"),
        (replace(_source(), remote_artifact_uri=None), "source_metadata_missing"),
        (
            replace(_source(), remote_retrieved_at=datetime(2026, 8, 26, 12, 0)),
            "remote_retrieval_timestamp_invalid",
        ),
    ],
)
def test_source_manifest_checksum_license_and_provenance_are_mandatory(
    source: SourceManifestEvidence, expected_code: str
) -> None:
    report = validate_release(_request(source=source))

    assert report.valid is False
    assert expected_code in {issue.code for issue in report.errors}
    assert report.counts.eligible_membership_count == 0
    assert report.counts.blocked_membership_count == 1


@pytest.mark.parametrize(
    ("candidate", "expected_code"),
    [
        (
            replace(_candidate(), assembly_resolution="ambiguous"),
            "assembly_resolution_not_exact",
        ),
        (
            replace(_candidate(), assembly_accession_version="GCA_945859735"),
            "assembly_accession_not_versioned",
        ),
        (replace(_candidate(), contig_resolution="unresolved"), "contig_resolution_not_exact"),
        (
            replace(_candidate(), contig_accession_version="CAMAOU020000182"),
            "contig_accession_not_versioned",
        ),
        (replace(_candidate(), contig_length=None), "contig_length_invalid"),
    ],
)
def test_exact_versioned_assembly_and_contig_are_required(
    candidate: ReleaseMembershipCandidate, expected_code: str
) -> None:
    assert expected_code in _error_codes(_request(candidate))


@pytest.mark.parametrize(
    ("placements", "expected_code"),
    [
        ((), "placement_count_not_one"),
        ((_placement(), _placement()), "placement_count_not_one"),
        ((replace(_placement(), precision="approximate"),), "placement_not_exact"),
        (
            (replace(_placement(), coordinate_system="1-based-closed"),),
            "placement_coordinate_system_invalid",
        ),
        ((replace(_placement(), start0=248796),), "placement_interval_invalid"),
        ((replace(_placement(), end0=1_000_001),), "placement_interval_out_of_bounds"),
        ((replace(_placement(), provenance_key=None),), "placement_provenance_missing"),
    ],
)
def test_exactly_one_valid_exact_interval_is_required(
    placements: tuple[PlacementEvidence, ...], expected_code: str
) -> None:
    candidate = replace(_candidate(), placements=placements)
    assert expected_code in _error_codes(_request(candidate))


@pytest.mark.parametrize(
    ("flanks", "expected_code"),
    [
        ((_flanks()[0],), "flank_assessment_count_not_one"),
        (
            (_flanks()[0], replace(_flanks()[1], verdict="insufficient")),
            "flank_not_supported",
        ),
        (
            (_flanks()[0], replace(_flanks()[1], policy_key="flank-policy:other-v1")),
            "flank_policy_mismatch",
        ),
        (
            (replace(_flanks()[0], evidence_key=None), _flanks()[1]),
            "flank_evidence_missing",
        ),
        (
            (replace(_flanks()[0], inspection_window_bp=10_000), _flanks()[1]),
            "flank_window_invalid",
        ),
        (
            (replace(_flanks()[0], inspected_bp=0), _flanks()[1]),
            "supported_flank_not_inspected",
        ),
        (
            (replace(_flanks()[0], method_or_curator_key=None), _flanks()[1]),
            "flank_method_missing",
        ),
        (
            (replace(_flanks()[0], evidence_sha256=None), _flanks()[1]),
            "flank_evidence_checksum_invalid",
        ),
    ],
)
def test_left_and_right_flanks_must_independently_pass(
    flanks: tuple[FlankEvidence, ...], expected_code: str
) -> None:
    candidate = replace(_candidate(), flank_assessments=flanks)
    assert expected_code in _error_codes(_request(candidate))


@pytest.mark.parametrize(
    ("candidate", "expected_code"),
    [
        (replace(_candidate(), source_record_key=None), "candidate_provenance_missing"),
        (replace(_candidate(), method_key=None), "candidate_provenance_missing"),
        (replace(_candidate(), import_run_key=None), "candidate_provenance_missing"),
        (replace(_candidate(), source_assessment=None), "source_assessment_invalid"),
        (
            replace(
                _candidate(),
                inclusion=InclusionEvidence(
                    decision="review",
                    policy_key="inclusion-policy:zhao-v4-pilot-v1",
                    authorized_by="curator:endoviho",
                ),
            ),
            "inclusion_decision_not_include",
        ),
        (
            replace(_candidate(), unresolved_issues=("assembly_alias",)),
            "candidate_unresolved_issues_present",
        ),
        (
            replace(_candidate(), quarantine_issues=("viral_contig",)),
            "candidate_quarantine_issues_present",
        ),
        (
            replace(_candidate(), conflicts=("flank_disagreement",)),
            "candidate_conflict_issues_present",
        ),
    ],
)
def test_provenance_decision_and_clean_state_are_required(
    candidate: ReleaseMembershipCandidate, expected_code: str
) -> None:
    assert expected_code in _error_codes(_request(candidate))


def test_locus_key_must_match_coordinate_free_identity() -> None:
    candidate = replace(_candidate(), locus_key="locus:eve:v1:sha256:" + "0" * 64)

    assert "locus_key_mismatch" in _error_codes(_request(candidate))


def test_duplicate_locus_memberships_fail_and_block_both_rows() -> None:
    candidate = _candidate()
    request = replace(_request(candidate), candidates=(candidate, candidate))

    report = validate_release(request)

    assert report.valid is False
    assert [issue.code for issue in report.errors].count("duplicate_locus_key") == 2
    assert report.counts.eligible_membership_count == 0
    assert report.counts.blocked_membership_count == 2


def test_accounted_quarantine_rows_are_counted_without_blocking_release() -> None:
    report = validate_release(_request())

    assert report.valid is True
    assert report.counts.accounted_quarantine_count == 527
    assert "accounted_quarantine_rows_retained" in {warning.code for warning in report.warnings}


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("unresolved_issues", ("unresolved-record",), "release_unresolved_issues_present"),
        (
            "unresolved_quarantine_issues",
            ("quarantine-row-missing-terminal-outcome",),
            "release_unresolved_quarantine_issues_present",
        ),
        ("conflicts", ("count-conflict",), "release_conflict_issues_present"),
    ],
)
def test_release_level_unresolved_quarantine_and_conflicts_fail_closed(
    field: str, value: tuple[str, ...], expected_code: str
) -> None:
    request = replace(_request(), **{field: value})
    report = validate_release(request)

    assert report.valid is False
    assert expected_code in {issue.code for issue in report.errors}
    assert report.counts.eligible_membership_count == 0
