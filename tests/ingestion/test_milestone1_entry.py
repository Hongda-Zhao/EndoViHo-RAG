from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eve_relation_rag.contracts.source_manifest import load_source_manifest
from eve_relation_rag.domain.keys import canonical_json_sha256
from eve_relation_rag.importers.audit import APPROVED_DATA_S1_KEY_DIGESTS
from eve_relation_rag.importers.data_s1 import DATA_S1_ASSEMBLY_ALLOWLIST, NcbiResolutionIndex
from eve_relation_rag.ingestion.milestone1 import (
    APPROVED_AUDIT_MODULE_SHA256,
    APPROVED_EXECUTION_CODE_SHA256,
    APPROVED_IMPORTER_SHA256,
    APPROVED_STAGING_EXPECTATION,
    APPROVED_STAGING_MODULE_SHA256,
    DEFAULT_RELEASE_KEY,
    Milestone1EntryError,
    assembly_specs_from_resolution_index,
    build_milestone1_staging_request,
)
from eve_relation_rag.ingestion.staging import AssemblySpec

MANIFEST_PATH = (
    Path(__file__).parents[2]
    / "data"
    / "manifests"
    / "milestone1_zhao_v4_data_s1.json"
)
ASSEMBLY_TAXA = {
    "GCA_015947965.1": ("Margaritifera margaritifera", 2_505_931),
    "GCA_016617855.1": ("Megalonaias nervosa", 52_375),
    "GCA_016746295.1": ("Potamilus streckersoni", 2_493_646),
    "GCA_028554795.2": ("Sinohyriopsis cumingii", 165_450),
    "GCA_029931535.1": ("Margaritifera margaritifera", 2_505_931),
    "GCA_943736005.1": ("Tridacna crocea", 80_833),
    "GCA_944589985.1": ("Limnoperna fortunei", 356_393),
    "GCA_945859735.2": ("Tridacna gigas", 80_829),
    "GCA_946811455.1": ("Hippopus hippopus", 80_818),
    "GCA_963210365.1": ("Tridacna derasa", 80_831),
}


def test_frozen_execution_components_match_source_bytes() -> None:
    package = Path(__file__).parents[2] / "src" / "eve_relation_rag"
    expected = {
        package / "importers" / "data_s1.py": APPROVED_IMPORTER_SHA256,
        package / "importers" / "audit.py": APPROVED_AUDIT_MODULE_SHA256,
        package / "ingestion" / "staging.py": APPROVED_STAGING_MODULE_SHA256,
    }

    for path, digest in expected.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_verified_assembly_report_projects_exact_taxids(tmp_path: Path) -> None:
    report_path = tmp_path / "assembly_data_report.jsonl"
    sequence_report_path = tmp_path / "sequence_report.jsonl"
    report_path.write_text(
        "".join(
            json.dumps(
                {
                    "accession": accession,
                    "organism": {"organism_name": name, "tax_id": tax_id},
                }
            )
            + "\n"
            for accession, (name, tax_id) in reversed(ASSEMBLY_TAXA.items())
        ),
        encoding="utf-8",
    )
    sequence_report_path.write_text("", encoding="utf-8")
    index = NcbiResolutionIndex.from_jsonl_reports(
        report_path,
        sequence_report_path,
    )

    specs = assembly_specs_from_resolution_index(
        index,
        allowlist=tuple(DATA_S1_ASSEMBLY_ALLOWLIST),
    )

    assert [spec.accession_version for spec in specs] == sorted(DATA_S1_ASSEMBLY_ALLOWLIST)
    assert len({spec.source_tax_id for spec in specs}) == 9
    assert specs[0] == AssemblySpec(
        "GCA_015947965.1", "Margaritifera margaritifera", 2_505_931
    )


def test_frozen_request_defaults_and_expectations_are_complete() -> None:
    assert DEFAULT_RELEASE_KEY == "release:endoviho-rag:v0:20260826:001"
    assert APPROVED_EXECUTION_CODE_SHA256 == canonical_json_sha256(
        {
            "audit_module_sha256": APPROVED_AUDIT_MODULE_SHA256,
            "importer_sha256": APPROVED_IMPORTER_SHA256,
            "staging_module_sha256": APPROVED_STAGING_MODULE_SHA256,
        }
    )
    assert APPROVED_EXECUTION_CODE_SHA256 != APPROVED_IMPORTER_SHA256
    assert APPROVED_STAGING_EXPECTATION.source_records == 39_495
    assert APPROVED_STAGING_EXPECTATION.source_high == 71
    assert APPROVED_STAGING_EXPECTATION.source_low == 39_424
    assert APPROVED_STAGING_EXPECTATION.normalized_candidates == 38_968
    assert APPROVED_STAGING_EXPECTATION.quarantined_rows == 527
    assert APPROVED_STAGING_EXPECTATION.loci == 39_495
    assert APPROVED_STAGING_EXPECTATION.placements == 38_968
    assert APPROVED_STAGING_EXPECTATION.quarantine_issues == 527
    assert APPROVED_STAGING_EXPECTATION.call_key_set_sha256 == (
        APPROVED_DATA_S1_KEY_DIGESTS["sorted_call_keys_sha256"]
    )
    assert APPROVED_STAGING_EXPECTATION.locus_key_set_sha256 == (
        APPROVED_DATA_S1_KEY_DIGESTS["sorted_locus_keys_sha256"]
    )


def test_request_rejects_unapproved_manifest_digest() -> None:
    manifest = load_source_manifest(MANIFEST_PATH)
    resolution = manifest.assembly_resolution
    index = NcbiResolutionIndex(
        assemblies=DATA_S1_ASSEMBLY_ALLOWLIST,
        sequence_lengths={},
        assembly_report_records=resolution.assembly_report.records,
        sequence_report_records=resolution.sequence_report.records,
        assembly_report_sha256=resolution.assembly_report.sha256,
        assembly_report_byte_size=resolution.assembly_report.byte_size,
        sequence_report_sha256=resolution.sequence_report.sha256,
        sequence_report_byte_size=resolution.sequence_report.byte_size,
    )

    with pytest.raises(Milestone1EntryError) as error:
        build_milestone1_staging_request(
            manifest,
            manifest_sha256="0" * 64,
            resolution_index=index,
            assemblies=tuple(
                AssemblySpec(accession, *ASSEMBLY_TAXA[accession])
                for accession in sorted(DATA_S1_ASSEMBLY_ALLOWLIST)
            ),
        )

    assert error.value.code == "manifest_checksum_mismatch"
