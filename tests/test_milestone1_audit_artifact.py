import json
from pathlib import Path
from typing import Any

from eve_relation_rag.ingestion.milestone1 import (
    APPROVED_AUDIT_MODULE_SHA256,
    APPROVED_EXECUTION_CODE_SHA256,
    APPROVED_IMPORTER_SHA256,
    APPROVED_STAGING_MODULE_SHA256,
)

AUDIT_PATH = (
    Path(__file__).parents[1]
    / "data"
    / "audits"
    / "milestone1_data_s1_import_audit.json"
)


def load_audit() -> dict[str, Any]:
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))


def test_frozen_full_source_audit_passed_without_duplicate_keys() -> None:
    audit = load_audit()
    report = audit["report"]

    assert report["passed"] is True
    assert report["counts"]["source_records"] == 39_495
    assert report["counts"]["source_high"] == 71
    assert report["counts"]["source_low"] == 39_424
    assert report["counts"]["assembly_resolution_exact"] == 39_495
    assert report["counts"]["contig_resolution_exact"] == 39_495
    assert report["distinct_counts"]["call_keys"] == 39_495
    assert report["distinct_counts"]["locus_keys"] == 39_495
    assert report["counts"]["call_key_preimage_error"] == 0
    assert report["counts"]["call_key_preimage_mismatch"] == 0
    assert report["counts"]["locus_key_preimage_error"] == 0
    assert report["counts"]["locus_key_preimage_mismatch"] == 0
    assert report["key_digests"]["sorted_call_keys_sha256"] == (
        "0b204b937aa53bcb286f555e85817d360ba5288ad23e3ba865191179730debae"
    )
    assert report["key_digests"]["sorted_locus_keys_sha256"] == (
        "cfba1fa2f70f6ea7f297fbffa67ac6f76c67e11be23687bc688896a2830b4fcc"
    )
    assert all(value == 0 for value in report["duplicate_counts"].values())
    assert report["mismatches"] == []


def test_source_audit_does_not_claim_a_public_release() -> None:
    audit = load_audit()

    assert audit["source"]["remote_checksum_verified"] is True
    assert audit["release_readiness"]["public_release_ready"] is False
    assert audit["release_readiness"]["release_membership_created"] is False
    assert audit["release_readiness"]["flank_assessment_status"] == "not_assessed"


def test_source_audit_pins_the_verified_execution_tools() -> None:
    tools = load_audit()["inputs_and_tools"]

    assert tools["importer_sha256"] == APPROVED_IMPORTER_SHA256
    assert tools["audit_module_sha256"] == APPROVED_AUDIT_MODULE_SHA256
    assert tools["staging_module_sha256"] == APPROVED_STAGING_MODULE_SHA256
    assert tools["execution_code_sha256"] == APPROVED_EXECUTION_CODE_SHA256
    assert tools["importer_method_run_identity"] == "zhao-data-s1-import-v2"
