from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.contracts.source_manifest import (
    ArtifactSpec,
    load_source_manifest,
    verify_local_artifact,
)

MANIFEST_PATH = (
    Path(__file__).parents[1] / "data" / "manifests" / "milestone1_zhao_v4_data_s1.json"
)


def test_typed_manifest_loads_the_approved_contract() -> None:
    manifest = load_source_manifest(MANIFEST_PATH)

    assert manifest.manifest_schema == "endoviho-source-manifest-v1"
    assert len(manifest.selection.assembly_allowlist) == 10
    assert manifest.expected_counts.source_records == 39_495
    assert manifest.expected_counts.source_high == 71
    assert manifest.expected_counts.source_low == 39_424
    assert manifest.artifact.license_key == "CC-BY-NC-ND-4.0"
    assert manifest.call_identity_policy.method_run_identity == "zhao-data-s1-import-v2"
    assert manifest.source_record_identity_policy.key_schema == (
        "zhao-data-s1-source-record-v1"
    )
    assert manifest.assembly_resolution.assembly_report.records == 10
    assert manifest.assembly_resolution.sequence_report.records == 220_512
    assert manifest.assembly_resolution.datasets_cli_binary_sha256.startswith("c2c38c")
    assert manifest.assembly_resolution.resolution_result.selected_contigs_resolved_exact == 12_233


def test_manifest_rejects_internally_inconsistent_confidence_counts(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["expected_counts"]["source_low"] = 1
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match=r"source_high \+ source_low"):
        load_source_manifest(invalid_path)


def test_local_artifact_verification_checks_size_and_sha256(tmp_path: Path) -> None:
    payload = b"frozen-source-artifact"
    artifact_path = tmp_path / "Data S1.xlsx"
    artifact_path.write_bytes(payload)
    sha256 = hashlib.sha256(payload).hexdigest()
    artifact = ArtifactSpec(
        source_label="fixture",
        native_filename="fixture.xlsx",
        accepted_local_filename="Data S1.xlsx",
        media_url=None,
        byte_size=len(payload),
        sha256=sha256,
        license_key="fixture-license",
        license_basis="fixture-only source license",
        worksheet="Data S1",
        used_range="A1:U2",
        populated_columns="A:U",
        remote_checksum_verified=False,
        retrieved_at=None,
        http_metadata=None,
    )

    verified = verify_local_artifact(artifact, artifact_path)
    mismatched = verify_local_artifact(
        artifact.model_copy(update={"sha256": "0" * 64}), artifact_path
    )

    assert verified.valid is True
    assert verified.errors == ()
    assert verified.actual_sha256 == sha256
    assert mismatched.valid is False
    assert mismatched.errors == ("artifact_sha256_mismatch",)


@pytest.mark.parametrize(
    "field_path",
    [
        ("artifact", "license_key"),
        ("artifact", "license_basis"),
        ("assembly_resolution", "source_snapshot_key"),
        ("assembly_resolution", "datasets_cli_binary_sha256"),
        ("assembly_resolution", "commands"),
        ("assembly_resolution", "license_or_usage_basis"),
        ("assembly_resolution", "assembly_report", "sha256"),
        ("assembly_resolution", "assembly_report", "byte_size"),
        ("assembly_resolution", "assembly_report", "records"),
    ],
)
def test_manifest_rejects_missing_frozen_provenance_fields(
    tmp_path: Path,
    field_path: tuple[str, ...],
) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    parent = payload
    for token in field_path[:-1]:
        parent = parent[token]
    del parent[field_path[-1]]
    invalid_path = tmp_path / "missing-provenance.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="Field required"):
        load_source_manifest(invalid_path)
