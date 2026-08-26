import json
import re
from pathlib import Path
from typing import Any

MANIFEST_PATH = (
    Path(__file__).parents[1] / "data" / "manifests" / "milestone1_zhao_v4_data_s1.json"
)


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_freezes_approved_all_vr_scope() -> None:
    manifest = load_manifest()
    selection = manifest["selection"]
    counts = manifest["expected_counts"]

    assert len(selection["assembly_allowlist"]) == 10
    assert len(set(selection["assembly_allowlist"])) == 10
    assert selection["viral_major_taxon"] == "Orthopolintovirales"
    assert selection["host_class"] == "Bivalvia"
    assert selection["include_all_vr_values"] is True
    assert counts["source_records"] == 39_495
    assert counts["source_high"] == 71
    assert counts["source_low"] == 39_424
    assert counts["source_high"] + counts["source_low"] == counts["source_records"]
    assert counts["unique_source_occurrence_keys"] == counts["source_records"]


def test_manifest_records_confidence_without_membership() -> None:
    manifest = load_manifest()
    confidence = manifest["source_confidence_policy"]

    assert confidence["source_high_when"] == "HCVR == Yes"
    assert confidence["source_low_when"] == "otherwise"
    assert confidence["creates_release_membership"] is False


def test_manifest_records_verified_canonical_remote_artifact() -> None:
    manifest = load_manifest()
    artifact = manifest["artifact"]

    assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
    assert artifact["remote_checksum_verified"] is True
    assert artifact["sha256"] == (
        "79b5d99c095b359d93c834014863fffbbd5968a1dbadafe6a77133a1d690f800"
    )
    assert artifact["worksheet"] == "S3"
    assert manifest["release_policy"]["requires_remote_artifact_verification"] is True


def test_coordinates_are_validated_but_excluded_from_locus_identity() -> None:
    manifest = load_manifest()

    assert manifest["coordinate_policy"]["canonical_system"] == "0-based-half-open"
    assert manifest["coordinate_policy"]["coordinate_in_locus_identity"] is False
    assert "native_vr_token" in manifest["identity_policy"]["preimage_fields"]
    assert manifest["call_identity_policy"]["method_run_identity"] == (
        "zhao-data-s1-import-v2"
    )
    assert "native_vr_token" in manifest["call_identity_policy"]["preimage_fields"]
    assert "excel_row" in manifest["source_record_identity_policy"]["preimage_fields"]
