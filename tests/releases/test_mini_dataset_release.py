from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.releases.mini_dataset import (
    MiniLocusRecord,
    load_mini_dataset_release,
)

ROOT = Path(__file__).parents[2]
RELEASE_ROOT = ROOT / "data/releases/endoviho-mini-v1"

EXPECTED_COORDINATES = {
    ("GCA_016617855.1", "JAECUM010000370.1", 161396, 201168, 47452),
    ("GCA_016617855.1", "JAECUM010003740.1", 22125, 39838, 47464),
    ("GCA_016617855.1", "JAECUM010004538.1", 34185, 41753, 47456),
    ("GCA_016617855.1", "JAECUM010005628.1", 24104, 38300, 47469),
    ("GCA_016617855.1", "JAECUM010009514.1", 74272, 88109, 47471),
    ("GCA_016617855.1", "JAECUM010020169.1", 23276, 36939, 47472),
    ("GCA_016746295.1", "JAEAOA010000226.1", 75546, 99170, 47425),
    ("GCA_016746295.1", "JAEAOA010001674.1", 242655, 258537, 47424),
    ("GCA_016746295.1", "JAEAOA010001896.1", 72336, 89096, 47423),
    ("GCA_028554795.2", "JAPYKE020000077.1", 3642455, 3658155, 39725),
    ("GCA_028554795.2", "JAPYKE020000094.1", 1689385, 1704500, 39724),
}


def test_real_mini_release_has_exact_public_scope_and_authority_versions() -> None:
    release = load_mini_dataset_release(RELEASE_ROOT)

    assert release.manifest.release_key == "release:endoviho-rag:mini:v1:20260903:001"
    assert release.manifest.release_status == "published"
    assert release.manifest.database_activation_status == "not_performed"
    assert release.manifest.source.doi == "10.1101/2025.04.19.649669"
    assert release.manifest.source.version == "v4"
    assert release.manifest.source.worksheet == "S3"
    assert release.manifest.source.license_key == "CC-BY-NC-ND-4.0"
    assert release.manifest.counts.model_dump() == {
        "assemblies": 3,
        "loci": 11,
        "host_lineages": 1,
        "source_viral_lineages": 1,
        "formal_viral_lineages": 1,
        "include": 11,
        "exclude": 0,
        "review": 0,
    }
    ncbi, ictv = release.manifest.authority_snapshots
    assert ncbi.version == "NCBI Taxonomy taxdump 2026-08-29T05:29:15Z"
    assert ncbi.merged_history_included is True
    assert ncbi.deleted_history_included is True
    assert [artifact.role for artifact in ictv.artifacts] == [
        "master_species_list",
        "corrected_virus_metadata_resource",
    ]
    assert [artifact.version for artifact in ictv.artifacts] == [
        "MSL41 v1",
        "MSL41.v1.20260729",
    ]


def test_every_public_member_has_exact_coordinates_full_flanks_and_include_decision() -> None:
    release = load_mini_dataset_release(RELEASE_ROOT)

    observed = set()
    for record in release.loci:
        taxon = record.assembly_source_taxon
        locus = record.eve_locus_or_reported_viral_region
        evidence = record.evidence_and_source
        observed.add(
            (
                taxon.assembly_accession_version,
                locus.contig_accession_version,
                locus.start0,
                locus.end0,
                evidence.source_row,
            )
        )
        assert taxon.assembly_status_at_snapshot == "current"
        assert taxon.host_family_name == "Unionidae"
        assert locus.length == locus.end0 - locus.start0
        assert evidence.left_flank.inspected_bp == 20_000
        assert evidence.right_flank.inspected_bp == 20_000
        assert evidence.left_flank.ambiguous_bp == 0
        assert evidence.right_flank.ambiguous_bp == 0
        assert record.inclusion_decision == "include"
        assert record.public_membership is True

    assert observed == EXPECTED_COORDINATES


def test_source_annotations_do_not_assign_a_relation_class() -> None:
    release = load_mini_dataset_release(RELEASE_ROOT)

    for record in release.loci:
        assert record.relation_class_assigned is False
        assert record.source_annotations.hcvr == "Yes"
        assert record.source_annotations.vr_type == "Integration"
        assert record.viral_lineage_affinity.source_label == "Orthopolintovirales"
        assert record.viral_lineage_affinity.formal_label == "Amphintovirales"
        encoded = record.model_dump_json()
        assert '"Transferred gene"' not in encoded
        assert '"Integrated virus"' not in encoded


def test_release_rejects_record_or_file_tampering(tmp_path: Path) -> None:
    manifest = (RELEASE_ROOT / "dataset_release.json").read_bytes()
    loci = (RELEASE_ROOT / "loci.jsonl").read_bytes()
    lines = loci.splitlines()
    payload = json.loads(lines[0])
    payload["inclusion_decision"] = "review"

    with pytest.raises(ValidationError):
        MiniLocusRecord.model_validate(payload)

    output = tmp_path / "release"
    output.mkdir()
    (output / "dataset_release.json").write_bytes(manifest)
    tampered = b"\n".join([json.dumps(payload, sort_keys=True).encode(), *lines[1:]]) + b"\n"
    (output / "loci.jsonl").write_bytes(tampered)
    assert hashlib.sha256(tampered).hexdigest() != json.loads(manifest)[
        "public_membership_file"
    ]["sha256"]
    with pytest.raises(ValueError, match="differs from manifest"):
        load_mini_dataset_release(output)
