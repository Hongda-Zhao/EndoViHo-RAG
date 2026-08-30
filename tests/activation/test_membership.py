from pathlib import Path

from eve_relation_rag.activation.driver import (
    V0_SOURCE_AUDIT_SHA256,
    V0_SOURCE_MANIFEST_SHA256,
)
from eve_relation_rag.activation.membership import load_m1_gate_evidence


def test_tracked_m1_gate_evidence_replays_exact_terminal_counts() -> None:
    root = Path(__file__).parents[2]

    evidence = load_m1_gate_evidence(
        source_manifest_path=root / "data/manifests/milestone1_zhao_v4_data_s1.json",
        expected_source_manifest_sha256=V0_SOURCE_MANIFEST_SHA256,
        source_audit_path=root / "data/audits/milestone1_data_s1_import_audit.json",
        expected_source_audit_sha256=V0_SOURCE_AUDIT_SHA256,
    )

    assert evidence.passed is True
    assert evidence.source_records == 39_495
    assert evidence.exact_placements == 38_968
    assert evidence.accounted_quarantine == 527
