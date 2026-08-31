from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from eve_relation_rag.activation.contracts import (
    APPROVED_ASSEMBLIES,
    IctvArtifactManifest,
    NcbiTaxonomyArtifactManifest,
    StudyFormalMappingManifest,
    seal_manifest_payload,
)
from eve_relation_rag.activation.taxonomy import (
    TaxonomyArtifactError,
    build_assembly_taxon_assignment_manifest,
    build_ictv_artifact_manifest,
    build_ncbi_taxonomy_artifact_manifest,
    build_polintovirus_rename_mapping_manifest,
    load_ictv_taxonomy_snapshot,
    load_ncbi_taxonomy_snapshot,
    observe_taxdump_members,
)
from eve_relation_rag.domain.keys import stable_key

RETRIEVED_AT = "2026-08-29T00:00:00Z"
SHA_A = "a" * 64


def test_ncbi_loader_binds_complete_history_and_resolves_merged_taxid(
    tmp_path: Path,
) -> None:
    archive = _taxdump(tmp_path)
    manifest = _ncbi_manifest(archive)

    loaded = load_ncbi_taxonomy_snapshot(manifest, archive, required_tax_ids=(99,))

    assert loaded.resolved_tax_ids == {99: 11}
    assert {row.authority_local_id for row in loaded.manifest.terms} == {"1", "10", "11"}
    assert loaded.manifest.ncbi_history is not None
    assert loaded.manifest.ncbi_history.merged_tax_id_count == 1
    assert loaded.manifest.ncbi_history.deleted_tax_id_count == 1
    species = next(row for row in loaded.manifest.terms if row.authority_local_id == "11")
    assert species.canonical_name == "Current species"
    assert any(alias.alias == "Old species" for alias in species.aliases)


def test_ncbi_loader_rejects_deleted_required_taxid(tmp_path: Path) -> None:
    archive = _taxdump(tmp_path)

    with pytest.raises(TaxonomyArtifactError, match="deleted TaxId"):
        load_ncbi_taxonomy_snapshot(_ncbi_manifest(archive), archive, required_tax_ids=(100,))


def test_ictv_loader_imports_full_hierarchy_and_requires_current_order(
    tmp_path: Path,
) -> None:
    msl, vmr = _ictv_workbooks(tmp_path, order="Amphintovirales")
    manifest = _ictv_manifest(msl, vmr)

    snapshot = load_ictv_taxonomy_snapshot(
        manifest,
        msl_path=msl,
        corrected_vmr_path=vmr,
    )

    assert snapshot.coverage == "complete-msl41-hierarchy"
    assert any(
        row.canonical_name == "Amphintovirales" and row.rank == "order" for row in snapshot.terms
    )
    assert all(row.canonical_name != "Orthopolintovirales" for row in snapshot.terms)


def test_ictv_loader_rejects_old_study_order_as_formal_name(tmp_path: Path) -> None:
    msl, vmr = _ictv_workbooks(tmp_path, order="Orthopolintovirales")

    with pytest.raises(TaxonomyArtifactError, match="Amphintovirales"):
        load_ictv_taxonomy_snapshot(
            _ictv_manifest(msl, vmr),
            msl_path=msl,
            corrected_vmr_path=vmr,
        )


def test_mapping_requires_explicit_renamed_to_relation() -> None:
    payload: dict[str, object] = {
        "manifest_schema_version": "study-formal-mapping-manifest-v1",
        "release_key": "release:endoviho-rag:v0:20260826:001",
        "study_snapshot_key": "study:zhao-v4",
        "formal_snapshot_key": "formal:ictv-msl41",
        "formal_snapshot_manifest_sha256": SHA_A,
        "mappings": (
            {
                "mapping_key": stable_key(
                    "study-formal-mapping",
                    {
                        "formal_snapshot_key": "formal:ictv-msl41",
                        "formal_term_key": "ictv:Amphintovirales",
                        "relation": "renamed_to",
                        "study_snapshot_key": "study:zhao-v4",
                        "study_term_key": "study:Orthopolintovirales",
                    },
                ),
                "study_snapshot_key": "study:zhao-v4",
                "study_term_key": "study:Orthopolintovirales",
                "formal_snapshot_key": "formal:ictv-msl41",
                "formal_term_key": "ictv:Amphintovirales",
                "relation": "renamed_to",
                "curation_method_key": "curation:ictv-proposal-2024.010D",
                "evidence_artifact_sha256": "b" * 64,
                "evidence_locator": "proposal 2024.010D rename table",
            },
        ),
    }
    manifest = StudyFormalMappingManifest.model_validate(seal_manifest_payload(payload))

    assert manifest.mappings[0].relation == "renamed_to"
    bad = manifest.model_dump(mode="python")
    bad["mappings"][0]["relation"] = "string_similarity"
    with pytest.raises(ValidationError):
        StudyFormalMappingManifest.model_validate(bad)


def test_artifact_builders_measure_policy_and_do_not_invent_ictv_checksum(
    tmp_path: Path,
) -> None:
    archive = _taxdump(tmp_path)
    archive_raw = archive.read_bytes()
    policy = tmp_path / "usage-policy.html"
    policy.write_text("frozen public policy capture", encoding="utf-8")
    policy_sha256 = hashlib.sha256(policy.read_bytes()).hexdigest()
    ncbi = build_ncbi_taxonomy_artifact_manifest(
        archive,
        expected_sha256=hashlib.sha256(archive_raw).hexdigest(),
        expected_byte_size=len(archive_raw),
        upstream_md5=hashlib.md5(archive_raw, usedforsecurity=False).hexdigest(),
        version="taxdump-test",
        source_uri="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
        checksum_source_uri="https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.md5",
        retrieved_at=RETRIEVED_AT,
        usage_policy_source_uri="https://www.ncbi.nlm.nih.gov/home/about/policies/",
        usage_policy_retrieved_at=RETRIEVED_AT,
        usage_policy_capture_path=policy,
        expected_usage_policy_sha256=policy_sha256,
    )
    assert ncbi.archive.upstream_checksum_verified is True
    assert ncbi.usage_policy.local_capture_sha256 == policy_sha256

    msl, vmr = _ictv_workbooks(tmp_path, order="Amphintovirales")
    ictv = build_ictv_artifact_manifest(
        msl_path=msl,
        corrected_vmr_path=vmr,
        expected_msl_sha256=hashlib.sha256(msl.read_bytes()).hexdigest(),
        expected_msl_byte_size=msl.stat().st_size,
        expected_vmr_sha256=hashlib.sha256(vmr.read_bytes()).hexdigest(),
        expected_vmr_byte_size=vmr.stat().st_size,
        msl_source_uri="https://ictv.global/taxonomy",
        vmr_source_uri="https://ictv.global/taxonomy",
        retrieved_at=RETRIEVED_AT,
        usage_policy_source_uri="https://ictv.global/taxonomy",
        usage_policy_retrieved_at=RETRIEVED_AT,
        usage_policy_capture_path=policy,
        expected_usage_policy_sha256=policy_sha256,
    )
    assert ictv.msl.upstream_checksum is None
    assert ictv.msl.upstream_checksum_verified is False
    assert ictv.msl.checksum_source_uri is None


def test_assignment_and_curated_rename_builders_are_executable(tmp_path: Path) -> None:
    archive = _taxdump(tmp_path)
    loaded_ncbi = load_ncbi_taxonomy_snapshot(
        _ncbi_manifest(archive), archive, required_tax_ids=(99,)
    )
    assembly_report = tmp_path / "assembly_data_report.jsonl"
    assembly_report.write_text(
        "".join(
            json.dumps({"accession": accession, "organism": {"tax_id": 99}}) + "\n"
            for accession in APPROVED_ASSEMBLIES
        ),
        encoding="utf-8",
    )
    report_raw = assembly_report.read_bytes()
    assignments = build_assembly_taxon_assignment_manifest(
        loaded_ncbi,
        assembly_report_path=assembly_report,
        expected_assembly_report_sha256=hashlib.sha256(report_raw).hexdigest(),
        expected_assembly_report_byte_size=len(report_raw),
        assembly_report_artifact_key="source-artifact:ncbi-assembly-report:test",
    )
    assert len(assignments.assignments) == 10
    assert {row.reported_ncbi_tax_id for row in assignments.assignments} == {99}
    assert {row.resolved_ncbi_tax_id for row in assignments.assignments} == {11}

    msl, vmr = _ictv_workbooks(tmp_path, order="Amphintovirales")
    formal = load_ictv_taxonomy_snapshot(
        _ictv_manifest(msl, vmr), msl_path=msl, corrected_vmr_path=vmr
    )
    mapping = build_polintovirus_rename_mapping_manifest(
        formal,
        study_snapshot_key="lineage-snapshot:study:zhao-v4",
        study_term_keys={
            "Orthopolintovirales": "lineage-term:study:orthopolintovirales",
            "Adintoviridae": "lineage-term:study:adintoviridae",
        },
        evidence_artifact_sha256="b" * 64,
        evidence_locator="proposal 2024.010D rename table",
    )
    assert len(mapping.mappings) == 2
    assert {row.relation for row in mapping.mappings} == {"renamed_to"}
    assert {row.formal_term_key for row in mapping.mappings}.issubset(
        {row.term_key for row in formal.terms}
    )
    order_only = build_polintovirus_rename_mapping_manifest(
        formal,
        study_snapshot_key="lineage-snapshot:study:zhao-v4",
        study_term_keys={
            "Orthopolintovirales": "lineage-term:study:orthopolintovirales",
        },
        evidence_artifact_sha256="b" * 64,
        evidence_locator="proposal 2024.010D rename table",
    )
    assert len(order_only.mappings) == 1
    assert order_only.mappings[0].relation == "renamed_to"


def _taxdump(tmp_path: Path) -> Path:
    path = tmp_path / "taxdump.tar.gz"
    members = {
        "nodes.dmp": (b"1\t|\t1\t|\tno rank\t|\n10\t|\t1\t|\tgenus\t|\n11\t|\t10\t|\tspecies\t|\n"),
        "names.dmp": (
            b"1\t|\troot\t|\t\t|\tscientific name\t|\n"
            b"10\t|\tExample genus\t|\t\t|\tscientific name\t|\n"
            b"11\t|\tCurrent species\t|\t\t|\tscientific name\t|\n"
            b"11\t|\tOld species\t|\t\t|\tsynonym\t|\n"
        ),
        "merged.dmp": b"99\t|\t11\t|\n",
        "delnodes.dmp": b"100\t|\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for filename, raw in members.items():
            info = tarfile.TarInfo(filename)
            info.size = len(raw)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return path


def _ncbi_manifest(path: Path) -> NcbiTaxonomyArtifactManifest:
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    payload: dict[str, object] = {
        "manifest_schema_version": "ncbi-taxonomy-artifact-manifest-v1",
        "snapshot_key": stable_key(
            "lineage-snapshot:ncbi-taxonomy",
            {"archive_sha256": sha256, "filename": path.name},
        ),
        "authority_namespace": "ncbi-taxonomy",
        "version": "test-20260829",
        "archive": {
            "artifact_key": "source-artifact:ncbi-taxonomy:test",
            "filename": "taxdump.tar.gz",
            "media_type": "application/gzip",
            "byte_size": len(raw),
            "sha256": sha256,
            "upstream_checksum_algorithm": "md5",
            "upstream_checksum": md5,
            "upstream_checksum_verified": True,
            "source_uri": "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
            "checksum_source_uri": ("https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.md5"),
            "retrieved_at": RETRIEVED_AT,
            "license_key": "NCBI-PUBLIC-DOMAIN-US-GOVERNMENT-WORK",
        },
        "members": observe_taxdump_members(path),
        "usage_policy": {
            "usage_basis_key": "NCBI-MOLECULAR-DATA-USAGE-POLICY",
            "source_uri": "https://www.ncbi.nlm.nih.gov/home/about/policies/",
            "retrieved_at": RETRIEVED_AT,
            "local_capture_sha256": SHA_A,
        },
    }
    return NcbiTaxonomyArtifactManifest.model_validate(seal_manifest_payload(payload))


def _ictv_workbooks(tmp_path: Path, *, order: str) -> tuple[Path, Path]:
    msl = tmp_path / "ICTV_Master_Species_List_2025_MSL41.v1.xlsx"
    vmr = tmp_path / "VMR_MSL41.v1.20260729.xlsx"
    rank_values = {
        "Realm": "Varidnaviria",
        "Kingdom": "Bamfordvirae",
        "Phylum": "Preplasmiviricota",
        "Class": "Polintoviricetes",
        "Order": order,
        "Family": "Eupolintoviridae",
        "Genus": "Alphadintovirus",
        "Species": "Alphadintovirus example",
        "ICTV_ID": "ICTV202000001",
    }
    headers = [
        "Realm",
        "Subrealm",
        "Kingdom",
        "Subkingdom",
        "Phylum",
        "Subphylum",
        "Class",
        "Subclass",
        "Order",
        "Suborder",
        "Family",
        "Subfamily",
        "Genus",
        "Subgenus",
        "Species",
        "ICTV_ID",
    ]
    _write_xlsx(msl, "MSL", (headers, [rank_values.get(header, "") for header in headers]))
    _write_xlsx(vmr, "VMR MSL41", (("ICTV_ID",), ("ICTV202000001",)))
    return msl, vmr


def _ictv_manifest(msl: Path, vmr: Path) -> IctvArtifactManifest:
    def artifact(path: Path, key: str) -> dict[str, object]:
        raw = path.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        return {
            "artifact_key": key,
            "filename": path.name,
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "byte_size": len(raw),
            "sha256": sha256,
            "upstream_checksum_algorithm": "sha256",
            "upstream_checksum": sha256,
            "upstream_checksum_verified": True,
            "source_uri": "https://ictv.global/taxonomy",
            "checksum_source_uri": "https://ictv.global/taxonomy",
            "retrieved_at": RETRIEVED_AT,
            "license_key": "CC-BY-4.0",
        }

    msl_sha256 = hashlib.sha256(msl.read_bytes()).hexdigest()
    vmr_sha256 = hashlib.sha256(vmr.read_bytes()).hexdigest()
    payload: dict[str, object] = {
        "manifest_schema_version": "ictv-msl41-artifact-manifest-v1",
        "snapshot_key": stable_key(
            "lineage-snapshot:ictv-msl41",
            {
                "msl_sha256": msl_sha256,
                "vmr_revision": "MSL41.v1.20260729",
                "vmr_sha256": vmr_sha256,
            },
        ),
        "authority_namespace": "ictv",
        "msl_version": "MSL41 v1",
        "msl": artifact(msl, "source-artifact:ictv:msl41-test"),
        "corrected_vmr": artifact(vmr, "source-artifact:ictv:vmr-test"),
        "vmr_revision": "MSL41.v1.20260729",
        "usage_policy": {
            "usage_basis_key": "ICTV-CC-BY-4.0",
            "source_uri": "https://ictv.global/taxonomy",
            "retrieved_at": RETRIEVED_AT,
            "local_capture_sha256": SHA_A,
        },
    }
    return IctvArtifactManifest.model_validate(seal_manifest_payload(payload))


def _write_xlsx(path: Path, worksheet: str, rows: tuple[tuple[str, ...] | list[str], ...]) -> None:
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{_column_name(index)}{row_number}" t="inlineStr"><is><t>'
            f"{escape(value)}</t></is></c>"
            for index, value in enumerate(row, start=1)
            if value
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(worksheet)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
