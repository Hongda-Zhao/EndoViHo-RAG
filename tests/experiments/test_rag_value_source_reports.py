"""Source retention never establishes public placement or scientific approval."""

import json

import pytest

from eve_relation_rag.experiments.rag_value_ablation.source_reports import (
    LiteratureRegionReport,
    RawSourceField,
    ReportTaxon,
    SourceRowReport,
    seal_literature_region,
    seal_source_row,
    select_source_reports,
)


def row(*, index=2, taxon=49291, ancestors=(49291, 41073, 1), viral="Megaviricetes"):
    cells = dict.fromkeys("ABCDEFGHIJKLMNOPQRSTU", "")
    cells.update(A="GCA_000000001.1", B="NC_000001.1", C=f"VR{index}", D="No",
                 F="100", G="200", H="100", J=viral, Q="Source organism",
                 R="Integration", S="0.125", T="Failed", U="not reported")
    return seal_source_row(
        source_snapshot_key="study-defined:tests:source-report", source_artifact_sha256="a" * 64,
        worksheet="S3", excel_row=index,
        fields=tuple(RawSourceField(column=c, value=v) for c, v in cells.items()),
        source_taxon=ReportTaxon(source_name=cells["Q"], tax_id=taxon,
                                taxonomy_source_sha256="b" * 64, ancestor_tax_ids=ancestors),
    )


def test_low_confidence_failed_and_unreported_values_survive_round_trip_and_selection():
    report = row()
    restored = SourceRowReport.model_validate_json(report.model_dump_json())
    assert [restored.cell(c) for c in "DSTU"] == ["No", "0.125", "Failed", "not reported"]
    assert restored.cell("I") == ""
    assert len(select_source_reports((restored,), viral_label="Megaviricetes")) == 1
    assert restored.biological_validation_status == "not_assessed"
    assert restored.reported_location_convention == "source_native_unverified"
    assert "approval" not in restored.model_dump()


def test_taxonomy_descendants_and_viral_filters_are_conjunctive():
    reports = (row(), row(index=3, viral="Retroviridae"),
               row(index=4, taxon=1143099, ancestors=(1143099, 34667, 1)))
    assert select_source_reports(reports, source_taxon_id=41073) == ()
    assert select_source_reports(reports, source_taxon_id=41073, include_descendants=True,
                                 viral_label="Megaviricetes") == (reports[0],)
    assert len(select_source_reports(reports, assembly="GCA_000000001.1")) == 3
    with pytest.raises(ValueError, match="requires a taxon"):
        select_source_reports(reports, include_descendants=True)


def test_source_annotation_tampering_or_fake_public_validation_is_rejected():
    payload = row().model_dump(mode="json")
    payload["fields"][3]["value"] = "Yes"
    with pytest.raises(ValueError, match="checksum"):
        SourceRowReport.model_validate_json(json.dumps(payload))
    payload = row().model_dump(mode="json")
    payload["biological_validation_status"] = "validated"
    with pytest.raises(ValueError):
        SourceRowReport.model_validate_json(json.dumps(payload))


def test_reported_approximation_cannot_be_promoted_to_exact_coordinates():
    region = seal_literature_region(
        source_doi="10.1234/test", source_artifact_sha256="c" * 64,
        source_locator="Figure 1", taxon_name="Source organism", region_name="reported region",
        lineage_as_reported="putative viral lineage", location_as_reported="about 475 kb",
        approximate_length_bp=475000, reported_claim="The source reports an insertion.",
        reported_uncertainty="Approximate boundary; no accession.version resolved.",
    )
    assert region.exact_coordinates is None
    assert region.exact_coordinate_scoring_applicable is False
    payload = region.model_dump(mode="json")
    payload["exact_coordinates"] = {"start0": 95000, "end0": 570000}
    with pytest.raises(ValueError):
        LiteratureRegionReport.model_validate_json(json.dumps(payload))
