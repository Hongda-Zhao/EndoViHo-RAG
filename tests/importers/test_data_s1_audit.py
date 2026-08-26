from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

import pytest

import eve_relation_rag.importers.audit as audit_module
from eve_relation_rag.domain.keys import locus_key
from eve_relation_rag.importers.audit import (
    APPROVED_DATA_S1_EXPECTED_COUNTS,
    APPROVED_DATA_S1_KEY_DIGESTS,
    DataS1AuditConfigurationError,
    DataS1AuditMismatch,
    audit_data_s1_outcomes,
    sorted_key_sha256,
    summarize_data_s1_outcomes,
)
from eve_relation_rag.importers.data_s1 import data_s1_record_key

ASSEMBLIES = tuple(f"GCA_{index:09d}.1" for index in range(1, 11))
ARTIFACT_SHA256 = "a" * 64
SOURCE_SNAPSHOT_KEY = "study-defined:test:data-s1"
IDENTITY_POLICY_KEY = "zhao-v4-contig-source-occurrence-v1"
METHOD_RUN_IDENTITY = "fixture-data-s1-import-v1"


@dataclass(frozen=True, slots=True)
class FakeLocator:
    worksheet: str
    excel_row: int

    @property
    def label(self) -> str:
        return f"{self.worksheet}!{self.excel_row}"


@dataclass(frozen=True, slots=True)
class FakeIssue:
    code: str


@dataclass(frozen=True, slots=True)
class FakeOutcome:
    artifact_sha256: str
    source_snapshot_key: str
    identity_policy_key: str
    method_run_identity: str
    source_assessment: str
    status: str
    raw_row: Mapping[str, str]
    assembly_resolution: str
    contig_resolution: str
    assembly_accession_version: str
    sequence_accession_version: str
    native_vr_token: str
    locator: FakeLocator
    record_key: str
    locus_key: str | None
    issues: tuple[FakeIssue, ...] = ()


def approved_outcomes(
    *,
    reverse: bool = False,
    duplicate_last_call_key: bool = False,
    first_contig_resolution: str = "exact",
) -> Iterator[FakeOutcome]:
    indexes = range(39_494, -1, -1) if reverse else range(39_495)
    for index in indexes:
        contig_slot = index % 12_233
        viral_contig = index >= 38_968
        assembly = ASSEMBLIES[contig_slot % len(ASSEMBLIES)]
        sequence = f"TEST{contig_slot:08d}.1"
        native_vr_token = f"vr{index + 1}"
        locator = FakeLocator("S3", index + 2)
        record_key = data_s1_record_key(
            ARTIFACT_SHA256,
            SOURCE_SNAPSHOT_KEY,
            locator,
            assembly_accession_version=assembly,
            sequence_accession_version=sequence,
            native_vr_token=native_vr_token,
            method_run_identity=METHOD_RUN_IDENTITY,
        )
        if duplicate_last_call_key and index == 39_494:
            record_key = data_s1_record_key(
                ARTIFACT_SHA256,
                SOURCE_SNAPSHOT_KEY,
                FakeLocator("S3", 2),
                assembly_accession_version=ASSEMBLIES[0],
                sequence_accession_version="TEST00000000.1",
                native_vr_token="vr1",
                method_run_identity=METHOD_RUN_IDENTITY,
            )
        yield FakeOutcome(
            artifact_sha256=ARTIFACT_SHA256,
            source_snapshot_key=SOURCE_SNAPSHOT_KEY,
            identity_policy_key=IDENTITY_POLICY_KEY,
            method_run_identity=METHOD_RUN_IDENTITY,
            source_assessment="source_high" if index < 71 else "source_low",
            status="quarantine" if viral_contig else "normalized_candidate",
            raw_row={
                "VR Type": "Viral contig" if viral_contig else "Integration",
                "Organism Name": f"Organism {contig_slot % 9}",
            },
            assembly_resolution="exact",
            contig_resolution=(
                first_contig_resolution if index == 0 else "exact"
            ),
            assembly_accession_version=assembly,
            sequence_accession_version=sequence,
            native_vr_token=native_vr_token,
            locator=locator,
            record_key=record_key,
            locus_key=locus_key(
                source_snapshot_key=SOURCE_SNAPSHOT_KEY,
                assembly_accession_version=assembly,
                contig_accession_version=sequence,
                native_vr_token=native_vr_token,
                identity_policy_version=IDENTITY_POLICY_KEY,
            ),
            issues=(FakeIssue("viral_contig_policy_quarantine"),)
            if viral_contig
            else (),
        )


def test_approved_counts_pass_and_key_digests_are_order_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_summary = summarize_data_s1_outcomes(approved_outcomes())
    monkeypatch.setattr(
        audit_module,
        "APPROVED_DATA_S1_KEY_DIGESTS",
        {
            name: fixture_summary.key_digests[name]
            for name in APPROVED_DATA_S1_KEY_DIGESTS
        },
    )
    forward = audit_data_s1_outcomes(
        approved_outcomes(), APPROVED_DATA_S1_EXPECTED_COUNTS
    )
    reverse = audit_data_s1_outcomes(
        approved_outcomes(reverse=True), APPROVED_DATA_S1_EXPECTED_COUNTS
    )

    assert forward.passed is True
    assert forward.mismatches == ()
    assert forward.summary.counts["source_records"] == 39_495
    assert forward.summary.distinct_counts["contigs"] == 12_233
    assert forward.summary.issue_counts == {"viral_contig_policy_quarantine": 527}
    assert forward.summary.key_digests == reverse.summary.key_digests
    json.dumps(forward.to_dict(), sort_keys=True)


def test_real_approved_key_digests_are_frozen_and_fail_on_fixture_keys() -> None:
    assert APPROVED_DATA_S1_KEY_DIGESTS == {
        "sorted_call_keys_sha256": (
            "0b204b937aa53bcb286f555e85817d360ba5288ad23e3ba865191179730debae"
        ),
        "sorted_locus_keys_sha256": (
            "cfba1fa2f70f6ea7f297fbffa67ac6f76c67e11be23687bc688896a2830b4fcc"
        ),
    }

    with pytest.raises(DataS1AuditMismatch) as raised:
        audit_data_s1_outcomes(approved_outcomes(), APPROVED_DATA_S1_EXPECTED_COUNTS)

    digest_mismatches = {
        mismatch.field: mismatch for mismatch in raised.value.report.mismatches
    }
    call_mismatch = digest_mismatches[
        "key_digests.sorted_call_keys_sha256"
    ]
    assert isinstance(call_mismatch.expected, str)
    assert isinstance(call_mismatch.actual, str)


def test_format_valid_forged_keys_fail_canonical_preimage_checks() -> None:
    def forged() -> Iterator[FakeOutcome]:
        for index, outcome in enumerate(approved_outcomes()):
            if index == 0:
                yield replace(
                    outcome,
                    record_key="call:zhao2026-v4:sha256:" + "0" * 64,
                    locus_key="locus:eve:v1:sha256:" + "0" * 64,
                )
            else:
                yield outcome

    with pytest.raises(DataS1AuditMismatch) as raised:
        audit_data_s1_outcomes(forged(), APPROVED_DATA_S1_EXPECTED_COUNTS)

    mismatch_fields = {mismatch.field for mismatch in raised.value.report.mismatches}
    assert "counts.call_key_preimage_mismatch" in mismatch_fields
    assert "counts.locus_key_preimage_mismatch" in mismatch_fields


def test_duplicate_and_non_exact_resolution_fail_closed_with_json_report() -> None:
    with pytest.raises(DataS1AuditMismatch) as raised:
        audit_data_s1_outcomes(
            approved_outcomes(
                duplicate_last_call_key=True,
                first_contig_resolution="not_checked",
            ),
            APPROVED_DATA_S1_EXPECTED_COUNTS,
        )

    report = raised.value.report
    mismatch_fields = {mismatch.field for mismatch in report.mismatches}
    assert report.passed is False
    assert "distinct_counts.call_keys" in mismatch_fields
    assert "duplicate_counts.call_key_values" in mismatch_fields
    assert "counts.contig_resolution_not_exact" in mismatch_fields
    json.dumps(report.to_dict(), sort_keys=True)


def test_manifest_expected_counts_are_strict_and_approved() -> None:
    missing = dict(APPROVED_DATA_S1_EXPECTED_COUNTS)
    del missing["source_high"]
    with pytest.raises(DataS1AuditConfigurationError, match="missing"):
        audit_data_s1_outcomes((), missing)

    altered = dict(APPROVED_DATA_S1_EXPECTED_COUNTS)
    altered["source_records"] = 1
    with pytest.raises(DataS1AuditMismatch) as raised:
        audit_data_s1_outcomes(approved_outcomes(), altered)
    assert any(
        mismatch.field == "manifest_expected_counts.source_records"
        for mismatch in raised.value.report.mismatches
    )


def test_sorted_key_digest_preserves_multiplicity_but_not_order() -> None:
    assert sorted_key_sha256(["b", "a", "b"]) == sorted_key_sha256(["b", "b", "a"])
    assert sorted_key_sha256(["a", "b"]) != sorted_key_sha256(["a", "b", "b"])
