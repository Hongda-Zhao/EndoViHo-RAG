"""Read-only M1 replay and evidence-backed public assertion projection."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    PublicAssertionMembershipManifest,
    PublicAssertionMembershipRecord,
    PublicLocusMembershipManifest,
    canonical_revalidate,
)
from eve_relation_rag.activation.policy import build_public_assertion_membership_manifest
from eve_relation_rag.contracts.source_manifest import load_source_manifest
from eve_relation_rag.db.models import (
    AssertionEvidence,
    DatasetRelease,
    EVELocus,
    EvidenceItem,
    ScientificAssertion,
)
from eve_relation_rag.importers.data_s1 import verify_file_bytes


class MembershipExportError(RuntimeError):
    """Raised when frozen M1 evidence or candidate assertions are incomplete."""


type AssertionType = Literal["hcvr", "viral_major_taxon", "vr_type"]


@dataclass(frozen=True, slots=True)
class M1GateEvidence:
    """Verified physical identities and exact terminal M1 counts."""

    source_manifest_sha256: str
    source_audit_sha256: str
    source_records: int
    exact_placements: int
    accounted_quarantine: int
    passed: bool


def load_m1_gate_evidence(
    *,
    source_manifest_path: str | Path,
    expected_source_manifest_sha256: str,
    source_audit_path: str | Path,
    expected_source_audit_sha256: str,
) -> M1GateEvidence:
    """Verify the exact tracked M1 inputs and replay their terminal gate facts."""

    manifest_file = verify_file_bytes(
        source_manifest_path,
        expected_sha256=expected_source_manifest_sha256,
    )
    audit_file = verify_file_bytes(
        source_audit_path,
        expected_sha256=expected_source_audit_sha256,
    )
    try:
        manifest = load_source_manifest(manifest_file.path)
        audit = json.loads(audit_file.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise MembershipExportError("frozen M1 gate evidence is unreadable or invalid") from exc
    if not isinstance(audit, dict):
        raise MembershipExportError("M1 audit root must be an object")
    expected_counts = {
        "source_records": 39_495,
        "source_high": 71,
        "source_low": 39_424,
        "vr_type_integration": 38_968,
        "vr_type_viral_contig": 527,
    }
    if manifest.expected_counts.model_dump()["source_records"] != 39_495:
        raise MembershipExportError("M1 source manifest terminal counts have drifted")
    for key, value in expected_counts.items():
        if getattr(manifest.expected_counts, key) != value:
            raise MembershipExportError(f"M1 source manifest count has drifted: {key}")
    try:
        report = _mapping(audit, "report")
        inputs_and_tools = _mapping(audit, "inputs_and_tools")
        counts = _mapping(report, "counts")
        duplicate_counts = _mapping(report, "duplicate_counts")
    except (KeyError, TypeError) as exc:
        raise MembershipExportError("M1 audit is missing required gate fields") from exc
    required_audit_counts = {
        **expected_counts,
        "normalized_candidate": 38_968,
        "quarantine": 527,
        "assembly_resolution_exact": 39_495,
        "contig_resolution_exact": 39_495,
        "missing_locus_key": 0,
        "invalid_call_key_format": 0,
        "invalid_locus_key_format": 0,
        "call_key_preimage_error": 0,
        "call_key_preimage_mismatch": 0,
        "locus_key_preimage_error": 0,
        "locus_key_preimage_mismatch": 0,
    }
    if (
        audit.get("audit_artifact_schema") != "endoviho-milestone1-source-audit-v1"
        or inputs_and_tools.get("manifest_sha256") != expected_source_manifest_sha256
        or report.get("passed") is not True
        or report.get("mismatches") != []
        or any(value != 0 for value in duplicate_counts.values())
        or any(counts.get(key) != value for key, value in required_audit_counts.items())
    ):
        raise MembershipExportError("M1 source/import gates did not replay exactly")
    return M1GateEvidence(
        source_manifest_sha256=manifest_file.sha256,
        source_audit_sha256=audit_file.sha256,
        source_records=39_495,
        exact_placements=38_968,
        accounted_quarantine=527,
        passed=True,
    )


def export_public_assertion_memberships(
    session: Session,
    public_loci: PublicLocusMembershipManifest,
    *,
    release_key: str = ACTIVATION_RELEASE_KEY,
) -> PublicAssertionMembershipManifest:
    """Read exact source assertions/support edges for the candidate public loci.

    No assertions are synthesized here.  In particular, the curated historical-name
    mapping remains a separate manifest and is not silently converted into a new
    formal-taxonomy assertion.
    """

    if release_key != ACTIVATION_RELEASE_KEY:
        raise MembershipExportError("the approved activation release key is required")
    if session.new or session.dirty or session.deleted:
        raise MembershipExportError("read-only membership export requires a clean ORM session")
    try:
        public_loci = canonical_revalidate(public_loci)
    except ValidationError as exc:
        raise MembershipExportError("public locus manifest failed canonical validation") from exc
    locus_keys = tuple(row.locus_key for row in public_loci.memberships)
    statement = (
        select(
            DatasetRelease.status.label("release_status"),
            EVELocus.locus_key,
            ScientificAssertion.assertion_key,
            ScientificAssertion.assertion_type,
            ScientificAssertion.predicate_key,
            ScientificAssertion.process_run_status,
            AssertionEvidence.relation,
            EvidenceItem.evidence_sha256,
        )
        .select_from(DatasetRelease)
        .join(EVELocus, EVELocus.release_id == DatasetRelease.id)
        .join(
            ScientificAssertion,
            (ScientificAssertion.release_id == DatasetRelease.id)
            & (ScientificAssertion.locus_id == EVELocus.id),
        )
        .outerjoin(
            AssertionEvidence,
            (AssertionEvidence.release_id == ScientificAssertion.release_id)
            & (AssertionEvidence.assertion_id == ScientificAssertion.id),
        )
        .outerjoin(
            EvidenceItem,
            (EvidenceItem.release_id == AssertionEvidence.release_id)
            & (EvidenceItem.id == AssertionEvidence.evidence_id),
        )
        .where(
            DatasetRelease.release_key == release_key,
            EVELocus.locus_key.in_(locus_keys),
        )
        .order_by(
            ScientificAssertion.assertion_key,
            AssertionEvidence.relation,
            EvidenceItem.evidence_sha256,
        )
    )
    with session.no_autoflush:
        rows = tuple(session.execute(statement).mappings())
    if not rows or {row["release_status"] for row in rows} != {"candidate"}:
        raise MembershipExportError("assertions must come from the candidate release")

    metadata: dict[str, tuple[str, AssertionType, str]] = {}
    supports: dict[str, set[str]] = defaultdict(set)
    locus_types: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        assertion_key = row["assertion_key"]
        locus_key = row["locus_key"]
        if row["assertion_type"] not in {"hcvr", "viral_major_taxon", "vr_type"}:
            raise MembershipExportError("database assertion type is outside the frozen contract")
        assertion_type = cast(AssertionType, row["assertion_type"])
        current = (locus_key, assertion_type, row["predicate_key"])
        if metadata.setdefault(assertion_key, current) != current:
            raise MembershipExportError("assertion key resolves to inconsistent database rows")
        if row["process_run_status"] != "succeeded":
            raise MembershipExportError("public assertion process run did not succeed")
        locus_types[locus_key].add(assertion_type)
        relation = row["relation"]
        evidence_sha256 = row["evidence_sha256"]
        if relation is None or evidence_sha256 is None:
            raise MembershipExportError("public assertion lacks a checksummed evidence edge")
        if relation == "contradicts":
            raise MembershipExportError("public assertion has contradicting evidence")
        if relation == "supports":
            supports[assertion_key].add(evidence_sha256)

    required_types = {"hcvr", "viral_major_taxon", "vr_type"}
    if set(locus_types) != set(locus_keys) or any(
        not required_types.issubset(types) for types in locus_types.values()
    ):
        raise MembershipExportError("each public locus requires the three source assertion types")
    if set(supports) != set(metadata) or any(not values for values in supports.values()):
        raise MembershipExportError("every public assertion requires supporting evidence")
    try:
        records = tuple(
            PublicAssertionMembershipRecord(
                assertion_key=assertion_key,
                locus_key=values[0],
                assertion_type=values[1],
                predicate_key=values[2],
                evidence_sha256s=tuple(sorted(supports[assertion_key])),
            )
            for assertion_key, values in sorted(metadata.items())
        )
    except ValidationError as exc:
        raise MembershipExportError("database assertion projection failed validation") from exc
    return build_public_assertion_membership_manifest(public_loci, records)


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value[key]
    if not isinstance(child, dict):
        raise TypeError(f"{key} must be an object")
    return child


__all__ = [
    "M1GateEvidence",
    "MembershipExportError",
    "export_public_assertion_memberships",
    "load_m1_gate_evidence",
]
