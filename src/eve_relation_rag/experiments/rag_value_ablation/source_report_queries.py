"""Complete queries over the approved source-report variant's immutable input packet.

These results describe source reports. They are never public release memberships,
exact placements, or independently validated biological associations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from eve_relation_rag.experiments.rag_value_ablation.source_reports import (
    LiteratureRegionReport,
    SourceRowReport,
    select_source_reports,
)
from eve_relation_rag.literature.contracts import Sha256, StableToken, StrictFrozenSchema
from eve_relation_rag.literature.hashing import canonical_json_sha256

SOURCE_FIELD_NAMES = dict(zip("ABCDEFGHIJKLMNOPQRSTU", (
    "Assembly", "Contig", "VR", "HCVR", "Contig Length", "Start", "End", "Length",
    "Annoated Viral Proportion", "Viral Major Taxon", "Eukaryote Classification", "Phylum",
    "Class", "Order", "Family", "Genus", "Organism Name", "VR Type", "Unique Rate",
    "Conserved OG", "Busco score",
), strict=True))
CORE53_SOURCE_QUERY_IDS = frozenset(
    f"{prefix}-{family}-{index:02d}" for prefix in ("HOST", "VIRUS", "REL", "RECORD")
    for family, count in (("S", 4), ("H", 2)) for index in range(1, count + 1)
) | {"UNSUP-09"}


class SourceReportQuery(StrictFrozenSchema):
    query_kind: Literal["rows", "reported_regions"] = "rows"
    source_taxon_id: int | None = Field(default=None, gt=0)
    include_descendants: bool = False
    viral_label: str | None = None
    assembly: str | None = None
    reported_region_keys: tuple[StableToken, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.query_kind == "reported_regions":
            if not self.reported_region_keys or any((self.source_taxon_id, self.include_descendants,
                                                     self.viral_label, self.assembly)):
                raise ValueError("reported-region lookup requires only exact source region keys")
            if len(set(self.reported_region_keys)) != len(self.reported_region_keys):
                raise ValueError("reported-region keys must be unique")
        else:
            if self.reported_region_keys:
                raise ValueError("row queries cannot silently include literature regions")
            # Reuse the source filter's accession and descendant rules, even for empty input.
            select_source_reports((), source_taxon_id=self.source_taxon_id,
                                  include_descendants=self.include_descendants,
                                  viral_label=self.viral_label, assembly=self.assembly)
        return self


class SourceReportResult(StrictFrozenSchema):
    result_schema_version: Literal["source-report-query-result-v1"] = (
        "source-report-query-result-v1"
    )
    source_packet_file_sha256: Sha256
    query: SourceReportQuery
    rows: tuple[SourceRowReport, ...] = ()
    regions: tuple[LiteratureRegionReport, ...] = ()
    source_report_count: int = Field(ge=0)
    completeness_scope: Literal["fixed_source_report_packet_only"] = (
        "fixed_source_report_packet_only"
    )
    biological_validation_status: Literal["not_assessed"] = "not_assessed"
    result_sha256: Sha256

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.query.query_kind == "rows":
            expected = select_source_reports(
                self.rows, source_taxon_id=self.query.source_taxon_id,
                include_descendants=self.query.include_descendants,
                viral_label=self.query.viral_label, assembly=self.query.assembly,
            )
            if self.regions or self.rows != expected:
                raise ValueError("query result contains out-of-scope or unordered reports")
        elif (self.rows or tuple(r.record_key for r in self.regions)
              != self.query.reported_region_keys):
            raise ValueError("reported-region result differs from the explicit lookup")
        keys = tuple(r.record_key for r in self.rows) + tuple(r.record_key for r in self.regions)
        if len(set(keys)) != len(keys) or self.source_report_count != len(keys):
            raise ValueError("source report count must match unique returned source identities")
        if self.result_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        ):
            raise ValueError("source query result checksum differs from its contents")
        return self

    def visible(self) -> dict[str, object]:
        """Keep original fields, locators and uncertainty; omit runtime and review metadata."""
        return {
            "source_packet_sha256": self.source_packet_file_sha256,
            "query": self.query.model_dump(mode="json"),
            "metric_key": "source_report_count",
            "source_report_count": self.source_report_count,
            "completeness_scope": self.completeness_scope,
            "biological_validation_status": self.biological_validation_status,
            "original_field_names": SOURCE_FIELD_NAMES,
            "source_rows": [{"record_key": r.record_key,
                             "source_occurrence_locus_key": r.source_occurrence_locus_key,
                             "source_artifact_sha256": r.source_artifact_sha256,
                             "worksheet": r.worksheet, "excel_row": r.excel_row,
                             "original_fields": {f.column: f.value for f in r.fields},
                             "source_taxon_id": r.source_taxon.tax_id,
                             "reported_location_convention": r.reported_location_convention}
                            for r in self.rows],
            "reported_regions": [r.model_dump(mode="json", exclude={"record_sha256"})
                                 for r in self.regions],
        }


class SourceReportRepository:
    """Verify the complete packet once; perform exact selections with no paging/truncation."""

    def __init__(self, directory: Path, *, expected_manifest_file_sha256: str) -> None:
        manifest_path = directory / "packet_manifest.json"
        raw = manifest_path.read_bytes()
        if manifest_path.is_symlink() or hashlib.sha256(raw).hexdigest() != (
            expected_manifest_file_sha256
        ):
            raise ValueError("source packet differs from its fixed manifest")
        manifest = json.loads(raw)
        if (manifest["schema_version"] != "rag-value-source-report-packet-v1"
                or manifest["public_validated_release"] is not False
                or manifest["manifest_sha256"] != canonical_json_sha256(
                    {k: v for k, v in manifest.items() if k != "manifest_sha256"})):
            raise ValueError("source packet is not a verified source-report snapshot")
        for name, digest in manifest["files"].items():
            path = directory / name
            if Path(name).name != name or path.is_symlink():
                raise ValueError("source packet file is outside its explicit directory")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("source packet file changed")
        self.packet_sha256 = expected_manifest_file_sha256
        self.rows = tuple(SourceRowReport.model_validate_json(line) for line in
                          (directory / "source_reports.jsonl").read_bytes().splitlines())
        self.regions = tuple(LiteratureRegionReport.model_validate_json(line) for line in
                             (directory / "literature_regions.jsonl").read_bytes().splitlines())
        if (len(self.rows) != manifest["source_row_count"]
                or len(self.regions) != manifest["literature_region_count"]
                or len({r.record_key for r in self.rows}) != len(self.rows)
                or len({r.source_occurrence_locus_key for r in self.rows}) != len(self.rows)
                or len({r.record_key for r in self.regions}) != len(self.regions)):
            raise ValueError("source packet has incomplete or repeated identities")
        bindings = json.loads((directory / "entity_region_bindings.json").read_bytes())
        self.region_bindings: dict[str, str] = {
            key: value for key, value in bindings.items() if key != "note"
        }
        if not set(self.region_bindings.values()) <= {r.record_key for r in self.regions}:
            raise ValueError("selected region binding is outside the source packet")

    def query(self, query: SourceReportQuery) -> SourceReportResult:
        rows = select_source_reports(
            self.rows, source_taxon_id=query.source_taxon_id,
            include_descendants=query.include_descendants,
            viral_label=query.viral_label, assembly=query.assembly,
        ) if query.query_kind == "rows" else ()
        by_key = {r.record_key: r for r in self.regions}
        try:
            regions = tuple(by_key[k] for k in query.reported_region_keys)
        except KeyError:
            raise ValueError("reported-region identity is absent from the fixed packet") from None
        value = SourceReportResult.model_construct(
            source_packet_file_sha256=self.packet_sha256, query=query,
            rows=rows, regions=regions, source_report_count=len(rows) + len(regions),
        ).model_dump(mode="json", exclude={"result_sha256"})
        return SourceReportResult.model_validate_json(json.dumps(
            {**value, "result_sha256": canonical_json_sha256(value)}))

    def verify_complete_result(self, result: SourceReportResult) -> None:
        if result != self.query(result.query):
            raise ValueError("result is not the complete selection from the fixed source packet")

    def core53_queries(self, question_id: str) -> tuple[SourceReportQuery, ...]:
        """Same 25 structured/hybrid question scopes, now returning source reports."""
        if question_id not in CORE53_SOURCE_QUERY_IDS:
            raise ValueError("question is outside the exact 25 source-query scopes")
        if question_id.startswith(("HOST-S-", "HOST-H-")):
            return (SourceReportQuery(source_taxon_id=41073, include_descendants=True),)
        if question_id.startswith(("VIRUS-S-", "VIRUS-H-")):
            return (SourceReportQuery(viral_label="Megaviricetes"),)
        if question_id.startswith(("REL-S-", "REL-H-")):
            labels = ("Megaviricetes", "Retroviridae") if question_id == "REL-S-04" else (
                "Megaviricetes",)
            return tuple(SourceReportQuery(source_taxon_id=41073, include_descendants=True,
                                           viral_label=label) for label in labels)
        if question_id in {"RECORD-S-01", "RECORD-S-03", "RECORD-H-02"}:
            return (SourceReportQuery(assembly="GCA_963924615.1", viral_label=(
                "Megaviricetes" if question_id == "RECORD-S-03" else None)),)
        if question_id in {"RECORD-S-02", "RECORD-S-04", "RECORD-H-01"}:
            slots = ("EVE_LOCUS_A", "EVE_LOCUS_B", "EVE_LOCUS_C") if question_id == (
                "RECORD-S-04") else ("EVE_LOCUS_A",)
            return tuple(SourceReportQuery(query_kind="reported_regions",
                                            reported_region_keys=(self.region_bindings[slot],))
                         for slot in slots)
        if question_id == "UNSUP-09":
            return (SourceReportQuery(),)
        raise ValueError("question has no source-report query in the core53 mapping")
