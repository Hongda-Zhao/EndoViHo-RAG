"""Lossless experiment inputs for source reports, distinct from public validated loci.

The source-admission decision permits retaining low-confidence reports. It does not
establish exact placements, assembly verification, flank support, or human Gold.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self, cast

from pydantic import Field, model_validator

from eve_relation_rag.domain.keys import (
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
    locus_key,
)
from eve_relation_rag.importers.data_s1 import DATA_S1_IDENTITY_POLICY_KEY
from eve_relation_rag.literature.contracts import (
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

SOURCE_COLUMNS = tuple("ABCDEFGHIJKLMNOPQRSTU")


class RawSourceField(StrictFrozenSchema):
    column: str = Field(pattern="^[A-U]$")
    value: str


class ReportTaxon(StrictFrozenSchema):
    """A source name resolved in a fixed taxonomy, not an assembly metadata check."""

    source_name: NonEmptyText
    tax_id: int = Field(gt=0)
    taxonomy_source_sha256: Sha256
    ancestor_tax_ids: tuple[int, ...] = Field(min_length=1)
    assembly_assignment_basis: Literal["source_reported"] = "source_reported"

    @model_validator(mode="after")
    def validate_ancestry(self) -> Self:
        if self.ancestor_tax_ids[0] != self.tax_id:
            raise ValueError("taxon chain must start with the selected taxon")
        if len(set(self.ancestor_tax_ids)) != len(self.ancestor_tax_ids):
            raise ValueError("taxon chain must not contain a cycle")
        if self.ancestor_tax_ids[-1] != 1:
            raise ValueError("taxon chain must reach the NCBI root")
        return self


class SourceRowReport(StrictFrozenSchema):
    """One retained row, including every confidence and failed source annotation."""

    record_kind: Literal["source_row_report"] = "source_row_report"
    record_key: StableToken
    source_occurrence_locus_key: StableToken
    source_snapshot_key: StableToken
    source_artifact_sha256: Sha256
    worksheet: NonEmptyText
    excel_row: int = Field(ge=2)
    fields: tuple[RawSourceField, ...] = Field(min_length=21, max_length=21)
    source_taxon: ReportTaxon
    reported_location_convention: Literal["source_native_unverified"] = (
        "source_native_unverified"
    )
    biological_validation_status: Literal["not_assessed"] = "not_assessed"
    record_sha256: Sha256

    def cell(self, column: str) -> str:
        return self.fields[SOURCE_COLUMNS.index(column)].value

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if tuple(f.column for f in self.fields) != SOURCE_COLUMNS:
            raise ValueError("source report requires every original column in order")
        if not is_versioned_assembly_accession(self.cell("A")):
            raise ValueError("source assembly label lacks accession.version")
        if not is_versioned_contig_accession(self.cell("B")):
            raise ValueError("source contig label lacks accession.version")
        if self.source_taxon.source_name != self.cell("Q"):
            raise ValueError("taxon binding differs from the original source name")
        if self.source_occurrence_locus_key != locus_key(
            source_snapshot_key=self.source_snapshot_key,
            assembly_accession_version=self.cell("A"), contig_accession_version=self.cell("B"),
            native_vr_token=self.cell("C"), identity_policy_version=DATA_S1_IDENTITY_POLICY_KEY,
        ):
            raise ValueError("source occurrence locus key differs from its identity preimage")
        identity = {
            "source_snapshot_key": self.source_snapshot_key,
            "source_artifact_sha256": self.source_artifact_sha256,
            "worksheet": self.worksheet, "excel_row": self.excel_row,
        }
        if self.record_key != "source-report:sha256:" + canonical_json_sha256(identity):
            raise ValueError("source report identity does not match its original locator")
        if self.record_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        ):
            raise ValueError("source report contents differ from its checksum")
        return self


class LiteratureRegionReport(StrictFrozenSchema):
    """A named reported region; unknown identity or coordinates remain absent."""

    record_kind: Literal["literature_region_report"] = "literature_region_report"
    record_key: StableToken
    source_doi: NonEmptyText
    source_artifact_sha256: Sha256
    source_locator: NonEmptyText
    taxon_name: NonEmptyText
    region_name: NonEmptyText
    lineage_as_reported: NonEmptyText
    location_as_reported: NonEmptyText
    approximate_length_bp: int | None = Field(default=None, gt=0)
    assembly_accession_version: str | None = None
    sequence_accession_version: str | None = None
    exact_coordinates: Literal[None] = None
    reported_claim: NonEmptyText
    reported_uncertainty: NonEmptyText
    exact_coordinate_scoring_applicable: Literal[False] = False
    biological_validation_status: Literal["not_assessed"] = "not_assessed"
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.assembly_accession_version is not None and not is_versioned_assembly_accession(
            self.assembly_accession_version
        ):
            raise ValueError("reported assembly must retain an exact accession.version")
        if self.sequence_accession_version is not None and not is_versioned_contig_accession(
            self.sequence_accession_version
        ):
            raise ValueError("reported sequence must retain an exact accession.version")
        identity = {"doi": self.source_doi, "locator": self.source_locator,
                    "taxon": self.taxon_name, "region": self.region_name}
        if self.record_key != "reported-region:sha256:" + canonical_json_sha256(identity):
            raise ValueError("reported region identity differs from its source")
        if self.record_sha256 != canonical_json_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        ):
            raise ValueError("reported region contents differ from its checksum")
        return self


def seal_source_row(**values: object) -> SourceRowReport:
    """Seal explicit source fields; never infer coordinates or suppress failed rows."""
    identity = {k: values[k] for k in (
        "source_snapshot_key", "source_artifact_sha256", "worksheet", "excel_row",
    )}
    values["record_key"] = "source-report:sha256:" + canonical_json_sha256(identity)
    source_fields = cast(Sequence[RawSourceField], values["fields"])
    fields = {f.column: f.value for f in source_fields}
    values["source_occurrence_locus_key"] = locus_key(
        source_snapshot_key=str(values["source_snapshot_key"]),
        assembly_accession_version=fields["A"], contig_accession_version=fields["B"],
        native_vr_token=fields["C"], identity_policy_version=DATA_S1_IDENTITY_POLICY_KEY,
    )
    provisional = SourceRowReport.model_construct(**values)  # type: ignore[arg-type]
    payload = provisional.model_dump(mode="json", exclude={"record_sha256"})
    return SourceRowReport.model_validate_json(
        _sealed_json(payload, "record_sha256")
    )


def seal_literature_region(**values: object) -> LiteratureRegionReport:
    identity = {"doi": values["source_doi"], "locator": values["source_locator"],
                "taxon": values["taxon_name"], "region": values["region_name"]}
    values["record_key"] = "reported-region:sha256:" + canonical_json_sha256(identity)
    provisional = LiteratureRegionReport.model_construct(**values)  # type: ignore[arg-type]
    payload = provisional.model_dump(mode="json", exclude={"record_sha256"})
    return LiteratureRegionReport.model_validate_json(_sealed_json(payload, "record_sha256"))


def _sealed_json(payload: dict[str, object], key: str) -> bytes:
    from eve_relation_rag.literature.hashing import canonical_json_bytes
    return canonical_json_bytes({**payload, key: canonical_json_sha256(payload)})


def select_source_reports(
    records: Sequence[SourceRowReport], *,
    source_taxon_id: int | None = None, include_descendants: bool = False,
    viral_label: str | None = None, assembly: str | None = None,
) -> tuple[SourceRowReport, ...]:
    """Exact AND selection for preparing a reference table, not a runtime retriever.

    No confidence or VR-type filter exists: the user chose to retain all source reports.
    Viral labels here have study-defined scope; this function performs no taxon renaming.
    """
    if source_taxon_id is None and include_descendants:
        raise ValueError("descendant scope requires a taxon")
    if assembly is not None and not is_versioned_assembly_accession(assembly):
        raise ValueError("assembly filter requires accession.version")
    selected = []
    for record in records:
        taxon = record.source_taxon
        if source_taxon_id is not None and (
            source_taxon_id not in taxon.ancestor_tax_ids if include_descendants
            else source_taxon_id != taxon.tax_id
        ):
            continue
        if viral_label is not None and record.cell("J") != viral_label:
            continue
        if assembly is not None and record.cell("A") != assembly:
            continue
        selected.append(record)
    return tuple(sorted(selected, key=lambda r: r.record_key))
