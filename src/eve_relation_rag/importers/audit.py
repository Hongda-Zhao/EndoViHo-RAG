"""Reproducible, fail-closed audit summaries for Data S1 import outcomes."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from eve_relation_rag.domain.keys import StableKeyError, locus_key
from eve_relation_rag.importers.data_s1 import (
    DataS1ImportOutcome,
    data_s1_record_key,
)

AUDIT_SCHEMA_VERSION: Final = "data-s1-import-audit-v1"
SORTED_KEY_DIGEST_SCHEME: Final = "sha256-canonical-json-sorted-key-multiset-v1"

APPROVED_DATA_S1_EXPECTED_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "source_records": 39_495,
        "source_high": 71,
        "source_low": 39_424,
        "assemblies": 10,
        "source_organism_names": 9,
        "contigs": 12_233,
        "unique_source_occurrence_keys": 39_495,
        "vr_type_integration": 38_968,
        "vr_type_viral_contig": 527,
    }
)
APPROVED_DATA_S1_KEY_DIGESTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "sorted_call_keys_sha256": (
            "0b204b937aa53bcb286f555e85817d360ba5288ad23e3ba865191179730debae"
        ),
        "sorted_locus_keys_sha256": (
            "cfba1fa2f70f6ea7f297fbffa67ac6f76c67e11be23687bc688896a2830b4fcc"
        ),
    }
)

_CALL_KEY_RE: Final = re.compile(r"^call:zhao2026-v4:sha256:[0-9a-f]{64}$")
_LOCUS_KEY_RE: Final = re.compile(r"^locus:eve:v1:sha256:[0-9a-f]{64}$")


class DataS1AuditConfigurationError(ValueError):
    """Raised when manifest expected counts are incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class AuditMismatch:
    """One expected-versus-observed audit failure."""

    field: str
    expected: int | str
    actual: int | str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready representation."""

        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class DataS1AuditSummary:
    """Order-independent summary of a single pass over import outcomes."""

    counts: Mapping[str, int]
    distinct_counts: Mapping[str, int]
    duplicate_counts: Mapping[str, int]
    issue_counts: Mapping[str, int]
    key_digests: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return only JSON-native mappings, strings, integers, and booleans."""

        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "counts": dict(self.counts),
            "distinct_counts": dict(self.distinct_counts),
            "duplicate_counts": dict(self.duplicate_counts),
            "issue_counts": dict(self.issue_counts),
            "key_digests": dict(self.key_digests),
        }


@dataclass(frozen=True, slots=True)
class DataS1AuditReport:
    """A summary paired with the frozen manifest expectations and verdict."""

    passed: bool
    expected_counts: Mapping[str, int]
    summary: DataS1AuditSummary
    mismatches: tuple[AuditMismatch, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready report suitable for a frozen audit artifact."""

        report = self.summary.to_dict()
        report.update(
            {
                "passed": self.passed,
                "expected_counts": dict(self.expected_counts),
                "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
            }
        )
        return report


class DataS1AuditMismatch(RuntimeError):
    """Fail-closed result carrying the complete JSON-ready audit report."""

    def __init__(self, report: DataS1AuditReport) -> None:
        self.report = report
        fields = ", ".join(mismatch.field for mismatch in report.mismatches[:5])
        suffix = "" if len(report.mismatches) <= 5 else ", ..."
        super().__init__(f"Data S1 audit failed: {fields}{suffix}")


def sorted_key_sha256(keys: Iterable[str]) -> str:
    """Hash the canonical JSON encoding of a sorted key multiset.

    Sorting makes the digest independent of import order.  Encoding the full
    list, rather than a set, preserves duplicate multiplicity for auditing.
    """

    encoded = json.dumps(
        sorted(keys),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def summarize_data_s1_outcomes(
    outcomes: Iterable[DataS1ImportOutcome],
) -> DataS1AuditSummary:
    """Consume outcomes once and return a deterministic JSON-ready summary."""

    counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    assemblies: set[str] = set()
    contigs: set[tuple[str, str]] = set()
    organism_names: set[str] = set()
    source_locators: list[str] = []
    call_keys: list[str] = []
    locus_keys: list[str] = []

    for outcome in outcomes:
        counts["source_records"] += 1
        counts[outcome.source_assessment] += 1
        counts[outcome.status] += 1

        vr_type = outcome.raw_row.get("VR Type", "")
        if vr_type == "Integration":
            counts["vr_type_integration"] += 1
        elif vr_type == "Viral contig":
            counts["vr_type_viral_contig"] += 1
        else:
            counts["vr_type_other"] += 1

        if outcome.assembly_resolution == "exact":
            counts["assembly_resolution_exact"] += 1
        else:
            counts["assembly_resolution_not_exact"] += 1
        if outcome.contig_resolution == "exact":
            counts["contig_resolution_exact"] += 1
        else:
            counts["contig_resolution_not_exact"] += 1

        assemblies.add(outcome.assembly_accession_version)
        contigs.add(
            (
                outcome.assembly_accession_version,
                outcome.sequence_accession_version,
            )
        )
        organism_name = outcome.raw_row.get("Organism Name", "")
        if organism_name:
            organism_names.add(organism_name)
        else:
            counts["missing_organism_name"] += 1

        source_locators.append(outcome.locator.label)
        call_keys.append(outcome.record_key)
        if _CALL_KEY_RE.fullmatch(outcome.record_key) is None:
            counts["invalid_call_key_format"] += 1
        try:
            expected_call_key = data_s1_record_key(
                outcome.artifact_sha256,
                outcome.source_snapshot_key,
                outcome.locator,
                assembly_accession_version=outcome.assembly_accession_version,
                sequence_accession_version=outcome.sequence_accession_version,
                native_vr_token=outcome.native_vr_token,
                method_run_identity=outcome.method_run_identity,
            )
        except (StableKeyError, ValueError):
            counts["call_key_preimage_error"] += 1
        else:
            if outcome.record_key != expected_call_key:
                counts["call_key_preimage_mismatch"] += 1

        if outcome.locus_key is None:
            counts["missing_locus_key"] += 1
        else:
            locus_keys.append(outcome.locus_key)
            if _LOCUS_KEY_RE.fullmatch(outcome.locus_key) is None:
                counts["invalid_locus_key_format"] += 1
        try:
            expected_locus_key = locus_key(
                source_snapshot_key=outcome.source_snapshot_key,
                assembly_accession_version=outcome.assembly_accession_version,
                contig_accession_version=outcome.sequence_accession_version,
                native_vr_token=outcome.native_vr_token,
                identity_policy_version=outcome.identity_policy_key,
            )
        except StableKeyError:
            counts["locus_key_preimage_error"] += 1
        else:
            if outcome.locus_key != expected_locus_key:
                counts["locus_key_preimage_mismatch"] += 1

        for issue in getattr(outcome, "issues", ()):
            issue_counts[issue.code] += 1

    call_duplicates = _duplicate_metrics(call_keys)
    locus_duplicates = _duplicate_metrics(locus_keys)
    locator_duplicates = _duplicate_metrics(source_locators)

    normalized_counts = {
        "source_records": counts["source_records"],
        "source_high": counts["source_high"],
        "source_low": counts["source_low"],
        "normalized_candidate": counts["normalized_candidate"],
        "quarantine": counts["quarantine"],
        "vr_type_integration": counts["vr_type_integration"],
        "vr_type_viral_contig": counts["vr_type_viral_contig"],
        "vr_type_other": counts["vr_type_other"],
        "assembly_resolution_exact": counts["assembly_resolution_exact"],
        "assembly_resolution_not_exact": counts["assembly_resolution_not_exact"],
        "contig_resolution_exact": counts["contig_resolution_exact"],
        "contig_resolution_not_exact": counts["contig_resolution_not_exact"],
        "missing_locus_key": counts["missing_locus_key"],
        "missing_organism_name": counts["missing_organism_name"],
        "invalid_call_key_format": counts["invalid_call_key_format"],
        "invalid_locus_key_format": counts["invalid_locus_key_format"],
        "call_key_preimage_error": counts["call_key_preimage_error"],
        "call_key_preimage_mismatch": counts["call_key_preimage_mismatch"],
        "locus_key_preimage_error": counts["locus_key_preimage_error"],
        "locus_key_preimage_mismatch": counts["locus_key_preimage_mismatch"],
    }
    distinct_counts = {
        "assemblies": len(assemblies),
        "contigs": len(contigs),
        "source_organism_names": len(organism_names),
        "source_locators": len(set(source_locators)),
        "call_keys": len(set(call_keys)),
        "locus_keys": len(set(locus_keys)),
    }
    duplicate_counts = {
        "call_key_values": call_duplicates[0],
        "call_key_extra_occurrences": call_duplicates[1],
        "locus_key_values": locus_duplicates[0],
        "locus_key_extra_occurrences": locus_duplicates[1],
        "source_locator_values": locator_duplicates[0],
        "source_locator_extra_occurrences": locator_duplicates[1],
    }
    key_digests = {
        "scheme": SORTED_KEY_DIGEST_SCHEME,
        "sorted_call_keys_sha256": sorted_key_sha256(call_keys),
        "sorted_locus_keys_sha256": sorted_key_sha256(locus_keys),
    }
    return DataS1AuditSummary(
        counts=normalized_counts,
        distinct_counts=distinct_counts,
        duplicate_counts=duplicate_counts,
        issue_counts=dict(sorted(issue_counts.items())),
        key_digests=key_digests,
    )


def audit_data_s1_outcomes(
    outcomes: Iterable[DataS1ImportOutcome],
    manifest_expected_counts: Mapping[str, object],
) -> DataS1AuditReport:
    """Validate one import against the frozen Data S1 contract or raise.

    The supplied manifest counts must themselves match the approved baseline.
    The observed outcomes must then match every count and invariant. Any
    mismatch raises :class:`DataS1AuditMismatch`; callers cannot accidentally
    treat a report with ``passed = False`` as a successful audit.
    """

    expected_counts = _validate_expected_counts(manifest_expected_counts)
    summary = summarize_data_s1_outcomes(outcomes)
    mismatches: list[AuditMismatch] = []

    for name, approved_value in APPROVED_DATA_S1_EXPECTED_COUNTS.items():
        _compare(
            mismatches,
            f"manifest_expected_counts.{name}",
            approved_value,
            expected_counts[name],
        )

    counts = summary.counts
    distinct = summary.distinct_counts
    duplicates = summary.duplicate_counts
    approved = APPROVED_DATA_S1_EXPECTED_COUNTS

    for name in (
        "source_records",
        "source_high",
        "source_low",
        "vr_type_integration",
        "vr_type_viral_contig",
    ):
        _compare(mismatches, f"counts.{name}", approved[name], counts[name])
    for name in ("assemblies", "contigs", "source_organism_names"):
        _compare(mismatches, f"distinct_counts.{name}", approved[name], distinct[name])

    _compare(
        mismatches,
        "distinct_counts.locus_keys",
        approved["unique_source_occurrence_keys"],
        distinct["locus_keys"],
    )
    _compare(
        mismatches,
        "distinct_counts.call_keys",
        approved["source_records"],
        distinct["call_keys"],
    )
    _compare(
        mismatches,
        "distinct_counts.source_locators",
        approved["source_records"],
        distinct["source_locators"],
    )
    _compare(
        mismatches,
        "counts.normalized_candidate",
        approved["vr_type_integration"],
        counts["normalized_candidate"],
    )
    _compare(
        mismatches,
        "counts.quarantine",
        approved["vr_type_viral_contig"],
        counts["quarantine"],
    )
    _compare(
        mismatches,
        "counts.assembly_resolution_exact",
        approved["source_records"],
        counts["assembly_resolution_exact"],
    )
    _compare(
        mismatches,
        "counts.contig_resolution_exact",
        approved["source_records"],
        counts["contig_resolution_exact"],
    )

    for name in (
        "vr_type_other",
        "assembly_resolution_not_exact",
        "contig_resolution_not_exact",
        "missing_locus_key",
        "missing_organism_name",
        "invalid_call_key_format",
        "invalid_locus_key_format",
        "call_key_preimage_error",
        "call_key_preimage_mismatch",
        "locus_key_preimage_error",
        "locus_key_preimage_mismatch",
    ):
        _compare(mismatches, f"counts.{name}", 0, counts[name])
    for name, actual in duplicates.items():
        _compare(mismatches, f"duplicate_counts.{name}", 0, actual)

    expected_issue_counts = {
        "viral_contig_policy_quarantine": approved["vr_type_viral_contig"]
    }
    for code in sorted(set(summary.issue_counts) | set(expected_issue_counts)):
        _compare(
            mismatches,
            f"issue_counts.{code}",
            expected_issue_counts.get(code, 0),
            summary.issue_counts.get(code, 0),
        )

    for name, expected_digest in APPROVED_DATA_S1_KEY_DIGESTS.items():
        _compare(
            mismatches,
            f"key_digests.{name}",
            expected_digest,
            summary.key_digests[name],
        )

    report = DataS1AuditReport(
        passed=not mismatches,
        expected_counts=expected_counts,
        summary=summary,
        mismatches=tuple(mismatches),
    )
    if mismatches:
        raise DataS1AuditMismatch(report)
    return report


def _validate_expected_counts(values: Mapping[str, object]) -> dict[str, int]:
    required = set(APPROVED_DATA_S1_EXPECTED_COUNTS)
    supplied = set(values)
    missing = sorted(required - supplied)
    extra = sorted(supplied - required)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"unexpected={extra}")
        raise DataS1AuditConfigurationError(
            "manifest expected_counts fields do not match the audit schema: "
            + "; ".join(details)
        )

    normalized: dict[str, int] = {}
    for name in APPROVED_DATA_S1_EXPECTED_COUNTS:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DataS1AuditConfigurationError(
                f"manifest expected_counts.{name} must be a non-negative integer"
            )
        normalized[name] = value
    return normalized


def _duplicate_metrics(values: Iterable[str]) -> tuple[int, int]:
    frequencies = Counter(values)
    duplicate_values = sum(count > 1 for count in frequencies.values())
    extra_occurrences = sum(count - 1 for count in frequencies.values() if count > 1)
    return duplicate_values, extra_occurrences


def _compare(
    mismatches: list[AuditMismatch],
    field: str,
    expected: int | str,
    actual: int | str,
) -> None:
    if actual != expected:
        mismatches.append(AuditMismatch(field=field, expected=expected, actual=actual))
