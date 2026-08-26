from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_BYTE_SIZE,
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_SOURCE_COLUMNS,
    FileByteVerificationError,
    ImportedDataS1Record,
    NcbiResolutionIndex,
    QuarantinedDataS1Record,
    iter_canonical_data_s1_import,
    iter_data_s1_import,
    iter_verified_data_s1_import,
    verify_file_bytes,
)

APPROVED_ASSEMBLY = "GCA_015947965.1"
OUT_OF_SCOPE_ASSEMBLY = "GCA_000000001.1"


def source_row(**overrides: str) -> dict[str, str]:
    row = {
        "Assembly": APPROVED_ASSEMBLY,
        "Contig": "ABCD010000001.1",
        "VR": "vr1",
        "HCVR": "Yes",
        "Contig Length": "1000",
        "Start": "100",
        "End": "200",
        "Length": "100",
        "Annoated Viral Proportion": "75",
        "Viral Major Taxon": "Orthopolintovirales",
        "Eukaryote Classification": "Metazoa",
        "Phylum": "Mollusca",
        "Class": "Bivalvia",
        "Order": "Testida",
        "Family": "Testidae",
        "Genus": "Testus",
        "Organism Name": "Testus bivalvis",
        "VR Type": "Integration",
        "Unique Rate": "1",
        "Conserved OG": "Passed",
        "Busco score": "99",
    }
    row.update(overrides)
    return row


def write_xlsx(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    *,
    worksheet_name: str = "Data S1",
) -> Path:
    strings: list[str] = []
    indexes: dict[str, int] = {}

    def shared_index(value: str) -> int:
        if value not in indexes:
            indexes[value] = len(strings)
            strings.append(value)
        return indexes[value]

    xml_rows: list[str] = []
    header_cells = "".join(
        f'<c r="{column}1" t="s"><v>{shared_index(header)}</v></c>'
        for column, header in DATA_S1_SOURCE_COLUMNS
    )
    xml_rows.append(f'<row r="1">{header_cells}</row>')

    for excel_row, row in enumerate(rows, start=2):
        cells = "".join(
            f'<c r="{column}{excel_row}" t="s"><v>{shared_index(row.get(header, ""))}</v></c>'
            for column, header in DATA_S1_SOURCE_COLUMNS
        )
        xml_rows.append(f'<row r="{excel_row}">{cells}</row>')

    shared_items = "".join(f"<si><t>{escape(value)}</t></si>" for value in strings)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{shared_items}</sst>'
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(xml_rows)}</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(worksheet_name)}" sheetId="1" '
        'r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path


def write_ncbi_index(
    tmp_path: Path,
    *,
    assemblies: Sequence[str] = (APPROVED_ASSEMBLY,),
    sequences: Sequence[tuple[str, str, int]] = (
        (APPROVED_ASSEMBLY, "ABCD010000001.1", 1000),
    ),
) -> NcbiResolutionIndex:
    assembly_report = tmp_path / "assembly_data_report.jsonl"
    sequence_report = tmp_path / "sequence_report.jsonl"
    assembly_report.write_text(
        "".join(json.dumps({"accession": accession}) + "\n" for accession in assemblies),
        encoding="utf-8",
    )
    sequence_report.write_text(
        "".join(
            json.dumps(
                {
                    "assembly_accession": assembly,
                    "genbank_accession": sequence,
                    "length": length,
                }
            )
            + "\n"
            for assembly, sequence, length in sequences
        ),
        encoding="utf-8",
    )
    return NcbiResolutionIndex.from_jsonl_reports(assembly_report, sequence_report)


def test_streaming_file_byte_verifier_checks_sha256_and_size(tmp_path: Path) -> None:
    path = tmp_path / "frozen.bin"
    payload = b"frozen-byte-provenance"
    path.write_bytes(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    observation = verify_file_bytes(
        path,
        expected_sha256=expected_sha256,
        expected_byte_size=len(payload),
        chunk_size=3,
    )

    assert observation.byte_size == len(payload)
    assert observation.sha256 == expected_sha256
    with pytest.raises(FileByteVerificationError, match="byte_size expected"):
        verify_file_bytes(path, expected_byte_size=len(payload) + 1)
    with pytest.raises(FileByteVerificationError, match="sha256 expected"):
        verify_file_bytes(path, expected_sha256="0" * 64)


def test_verified_import_binds_outcomes_to_observed_workbook_bytes(tmp_path: Path) -> None:
    workbook = write_xlsx(tmp_path / "verified.xlsx", [source_row()])
    payload = workbook.read_bytes()
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    outcome = list(
        iter_verified_data_s1_import(
            workbook,
            expected_artifact_sha256=expected_sha256,
            expected_artifact_byte_size=len(payload),
        )
    )[0]

    assert outcome.artifact_sha256 == expected_sha256
    with pytest.raises(FileByteVerificationError, match="sha256 expected"):
        list(
            iter_verified_data_s1_import(
                workbook,
                expected_artifact_sha256="0" * 64,
                expected_artifact_byte_size=len(payload),
            )
        )


def test_canonical_import_rejects_synthetic_workbook_bytes(tmp_path: Path) -> None:
    workbook = write_xlsx(tmp_path / "not-canonical.xlsx", [source_row()])

    assert DATA_S1_ARTIFACT_BYTE_SIZE == 83_851_778
    with pytest.raises(FileByteVerificationError, match="frozen file verification failed"):
        list(iter_canonical_data_s1_import(workbook))


def test_ncbi_index_records_actual_report_bytes_and_checks_expectations(
    tmp_path: Path,
) -> None:
    assembly_report = tmp_path / "assembly.jsonl"
    sequence_report = tmp_path / "sequence.jsonl"
    assembly_report.write_text(
        json.dumps({"accession": APPROVED_ASSEMBLY}) + "\n",
        encoding="utf-8",
    )
    sequence_report.write_text(
        json.dumps(
            {
                "assembly_accession": APPROVED_ASSEMBLY,
                "genbank_accession": "ABCD010000001.1",
                "length": 1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assembly_sha256 = hashlib.sha256(assembly_report.read_bytes()).hexdigest()
    sequence_sha256 = hashlib.sha256(sequence_report.read_bytes()).hexdigest()

    index = NcbiResolutionIndex.from_jsonl_reports(
        assembly_report,
        sequence_report,
        expected_assembly_report_sha256=assembly_sha256,
        expected_assembly_report_byte_size=assembly_report.stat().st_size,
        expected_sequence_report_sha256=sequence_sha256,
        expected_sequence_report_byte_size=sequence_report.stat().st_size,
    )

    assert index.assembly_report_sha256 == assembly_sha256
    assert index.assembly_report_byte_size == assembly_report.stat().st_size
    assert index.sequence_report_sha256 == sequence_sha256
    assert index.sequence_report_byte_size == sequence_report.stat().st_size
    assert index.byte_bound is True
    assert replace(index).byte_bound is False
    with pytest.raises(FileByteVerificationError, match="sha256 expected"):
        NcbiResolutionIndex.from_jsonl_reports(
            assembly_report,
            sequence_report,
            expected_sequence_report_sha256="0" * 64,
        )


def test_imports_high_and_low_source_assessments(tmp_path: Path) -> None:
    workbook = write_xlsx(
        tmp_path / "assessment.xlsx",
        [source_row(), source_row(VR="vr2", HCVR="No", Start="300", End="450", Length="150")],
    )

    outcomes = list(iter_data_s1_import(workbook))

    assert [outcome.source_assessment for outcome in outcomes] == [
        "source_high",
        "source_low",
    ]
    assert all(isinstance(outcome, ImportedDataS1Record) for outcome in outcomes)
    assert outcomes[0].raw_row["HCVR"] == "Yes"
    assert outcomes[0].locator.worksheet == "Data S1"
    assert outcomes[0].locator.excel_row == 2
    assert outcomes[0].locator.label == "Data S1!2"
    assert outcomes[0].artifact_sha256 == DATA_S1_ARTIFACT_SHA256
    assert outcomes[0].assembly_resolution == "not_checked"
    assert outcomes[0].contig_resolution == "not_checked"


def test_canonical_s3_and_local_alias_preserve_the_physical_sheet_locator(
    tmp_path: Path,
) -> None:
    canonical_workbook = write_xlsx(
        tmp_path / "canonical.xlsx", [source_row()], worksheet_name="S3"
    )
    alias_workbook = write_xlsx(tmp_path / "alias.xlsx", [source_row()])

    canonical = list(iter_data_s1_import(canonical_workbook))[0]
    alias = list(iter_data_s1_import(alias_workbook))[0]

    assert canonical.locator.label == "S3!2"
    assert alias.locator.label == "Data S1!2"
    assert canonical.record_key != alias.record_key
    assert canonical.locus_key == alias.locus_key


def test_same_contig_with_multiple_vr_tokens_remains_distinct(tmp_path: Path) -> None:
    workbook = write_xlsx(
        tmp_path / "multiple-vr.xlsx",
        [
            source_row(VR="vr3", Start="210", End="260", Length="50"),
            source_row(VR="vr7", Start="600", End="700", Length="100"),
        ],
    )

    first, second = list(iter_data_s1_import(workbook))

    assert isinstance(first, ImportedDataS1Record)
    assert isinstance(second, ImportedDataS1Record)
    assert first.sequence_accession_version == second.sequence_accession_version
    assert first.native_vr_token != second.native_vr_token
    assert first.locus_key != second.locus_key


def test_locus_identity_excludes_coordinates_and_replay_is_deterministic(
    tmp_path: Path,
) -> None:
    first_workbook = write_xlsx(tmp_path / "first.xlsx", [source_row()])
    second_workbook = write_xlsx(
        tmp_path / "second.xlsx",
        [source_row(Start="400", End="550", Length="150")],
    )

    first = list(iter_data_s1_import(first_workbook))[0]
    replay = list(iter_data_s1_import(first_workbook))[0]
    moved = list(iter_data_s1_import(second_workbook))[0]

    assert isinstance(first, ImportedDataS1Record)
    assert isinstance(replay, ImportedDataS1Record)
    assert isinstance(moved, ImportedDataS1Record)
    assert first.record_key == replay.record_key
    assert first.locus_key == replay.locus_key == moved.locus_key
    assert (first.start0, first.end0) != (moved.start0, moved.end0)

    different_artifact = list(
        iter_data_s1_import(first_workbook, artifact_sha256="0" * 64)
    )[0]
    assert different_artifact.record_key != first.record_key
    assert different_artifact.source_record_key != first.source_record_key
    assert different_artifact.locus_key == first.locus_key
    different_method = list(
        iter_data_s1_import(
            first_workbook,
            method_run_identity="zhao-data-s1-import-v3",
        )
    )[0]
    assert different_method.record_key != first.record_key
    assert different_method.source_record_key == first.source_record_key
    with pytest.raises(ValueError, match="full lowercase SHA-256"):
        list(iter_data_s1_import(first_workbook, artifact_sha256="not-a-digest"))


def test_bad_selected_rows_are_structured_quarantine_records(tmp_path: Path) -> None:
    workbook = write_xlsx(
        tmp_path / "bad-rows.xlsx",
        [
            source_row(Contig="ABCD010000001", VR="vr2"),
            source_row(VR="region-3"),
            source_row(VR="vr4", Start="10;20", End="30;40", Length="40"),
            source_row(VR="vr5", Length="99"),
            source_row(VR="vr6", Start="900", End="1100", Length="200"),
        ],
    )

    outcomes = list(iter_data_s1_import(workbook))
    issue_codes = [{issue.code for issue in outcome.issues} for outcome in outcomes]

    assert len(outcomes) == 5
    assert all(isinstance(outcome, QuarantinedDataS1Record) for outcome in outcomes)
    assert "invalid_sequence_accession_version" in issue_codes[0]
    assert "invalid_vr_token" in issue_codes[1]
    assert "multipart_interval" in issue_codes[2]
    assert "length_mismatch" in issue_codes[3]
    assert "interval_out_of_bounds" in issue_codes[4]
    assert all(outcome.record_key.startswith("call:zhao2026-v4:sha256:") for outcome in outcomes)


def test_scope_filtering_is_exact_but_keeps_all_hcvr_values(tmp_path: Path) -> None:
    workbook = write_xlsx(
        tmp_path / "scope.xlsx",
        [
            source_row(HCVR="No"),
            source_row(VR="vr2", **{"Viral Major Taxon": "Asfuvirales"}),
            source_row(VR="vr3", Class="Gastropoda"),
            source_row(VR="vr4", Assembly=OUT_OF_SCOPE_ASSEMBLY),
        ],
    )

    outcomes = list(iter_data_s1_import(workbook))

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], ImportedDataS1Record)
    assert outcomes[0].source_assessment == "source_low"


def test_viral_contig_is_retained_as_policy_quarantine(tmp_path: Path) -> None:
    workbook = write_xlsx(
        tmp_path / "viral-contig.xlsx",
        [source_row(**{"VR Type": "Viral contig", "HCVR": "No"})],
    )

    outcome = list(iter_data_s1_import(workbook))[0]

    assert isinstance(outcome, QuarantinedDataS1Record)
    assert outcome.source_assessment == "source_low"
    assert outcome.locus_key is not None
    assert {issue.code for issue in outcome.issues} == {
        "viral_contig_policy_quarantine"
    }


def test_ncbi_index_marks_only_exact_assembly_contig_and_length_as_resolved(
    tmp_path: Path,
) -> None:
    index = write_ncbi_index(tmp_path)
    workbook = write_xlsx(tmp_path / "resolved.xlsx", [source_row()])

    outcome = list(iter_data_s1_import(workbook, resolution_index=index))[0]

    assert isinstance(outcome, ImportedDataS1Record)
    assert outcome.assembly_resolution == "exact"
    assert outcome.contig_resolution == "exact"
    assert outcome.authority_contig_length == 1000
    assert index.assembly_report_records == 1
    assert index.sequence_report_records == 1


def test_ncbi_missing_and_length_mismatch_are_structured_quarantine(
    tmp_path: Path,
) -> None:
    missing_assembly_index = write_ncbi_index(
        tmp_path,
        assemblies=(),
        sequences=(),
    )
    missing_assembly_workbook = write_xlsx(
        tmp_path / "missing-assembly.xlsx", [source_row()]
    )
    missing_assembly = list(
        iter_data_s1_import(
            missing_assembly_workbook, resolution_index=missing_assembly_index
        )
    )[0]

    assert isinstance(missing_assembly, QuarantinedDataS1Record)
    assert missing_assembly.assembly_resolution == "unresolved"
    assert missing_assembly.contig_resolution == "unresolved"
    assert {issue.code for issue in missing_assembly.issues} == {
        "ncbi_assembly_not_resolved"
    }

    missing_index = write_ncbi_index(
        tmp_path,
        sequences=((APPROVED_ASSEMBLY, "ABCD010000002.1", 1000),),
    )
    missing_workbook = write_xlsx(tmp_path / "missing.xlsx", [source_row()])
    missing = list(
        iter_data_s1_import(missing_workbook, resolution_index=missing_index)
    )[0]

    assert isinstance(missing, QuarantinedDataS1Record)
    assert missing.assembly_resolution == "exact"
    assert missing.contig_resolution == "unresolved"
    assert {issue.code for issue in missing.issues} == {"ncbi_sequence_not_resolved"}

    mismatch_index = write_ncbi_index(
        tmp_path,
        sequences=((APPROVED_ASSEMBLY, "ABCD010000001.1", 999),),
    )
    mismatch_workbook = write_xlsx(tmp_path / "mismatch.xlsx", [source_row()])
    mismatch = list(
        iter_data_s1_import(mismatch_workbook, resolution_index=mismatch_index)
    )[0]

    assert isinstance(mismatch, QuarantinedDataS1Record)
    assert mismatch.assembly_resolution == "exact"
    assert mismatch.contig_resolution == "length_mismatch"
    assert mismatch.authority_contig_length == 999
    assert {issue.code for issue in mismatch.issues} == {
        "ncbi_contig_length_mismatch"
    }


def test_invalid_coordinates_keep_coordinate_free_locus_identity_after_resolution(
    tmp_path: Path,
) -> None:
    index = write_ncbi_index(tmp_path)
    workbook = write_xlsx(tmp_path / "bad-placement.xlsx", [source_row(Length="99")])

    outcome = list(iter_data_s1_import(workbook, resolution_index=index))[0]

    assert isinstance(outcome, QuarantinedDataS1Record)
    assert outcome.assembly_resolution == "exact"
    assert outcome.contig_resolution == "exact"
    assert outcome.locus_key is not None
    assert {issue.code for issue in outcome.issues} == {"length_mismatch"}
