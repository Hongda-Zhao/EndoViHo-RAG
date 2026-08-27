"""Deterministic serializers and presentation derived only from result models."""

from __future__ import annotations

from collections.abc import Sequence

from eve_relation_rag.domain.keys import canonical_json
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    AssemblySummary,
    ErrorResponse,
    LocusDetailData,
    LocusPageData,
    LocusSummary,
    PlanSuccess,
    QuerySuccess,
    SourceTaxonPageData,
    StructuredResult,
)

type StructuredResponseModel = PlanSuccess | QuerySuccess | ErrorResponse


def serialize_structured_result(result: StructuredResult) -> str:
    """Return stable compact UTF-8 JSON without adding volatile metadata."""

    return canonical_json(result.model_dump(mode="json"))


def serialize_structured_response(response: StructuredResponseModel) -> str:
    """Return stable compact UTF-8 JSON for one validated response variant."""

    return canonical_json(response.model_dump(mode="json"))


def render_structured_result_text(result: StructuredResult) -> str:
    """Return a complete deterministic line-oriented rendering.

    Values are serialized from the already validated ``StructuredResult``;
    this function performs no lookup, inference, or biological interpretation.
    """

    return "\n".join(
        (
            f"release={canonical_json(result.release.model_dump(mode='json'))}",
            f"plan_sha256={result.plan_sha256}",
            f"data={canonical_json(result.data.model_dump(mode='json'))}",
            "warnings="
            + canonical_json([item.model_dump(mode="json") for item in result.warnings]),
            "limitations="
            + canonical_json([item.model_dump(mode="json") for item in result.limitations]),
        )
    )


def _markdown_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value)
    return rendered.replace("\\", "\\\\").replace("|", "\\|")


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    header = "| " + " | ".join(_markdown_cell(item) for item in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_markdown_cell(item) for item in row) + " |" for row in rows]
    return "\n".join((header, divider, *body))


def _locus_row(item: LocusSummary) -> tuple[object, ...]:
    viral_terms = "; ".join(
        f"{lineage.role}:{lineage.snapshot_key}:{lineage.term_key}"
        for lineage in item.viral_lineages
    )
    return (
        item.locus_key,
        item.assembly_accession_version,
        item.placement.sequence_accession_version,
        item.placement.start0,
        item.placement.end0,
        item.placement.strand,
        item.source_organism_name,
        item.source_taxon.canonical_name,
        viral_terms,
    )


def _locus_table(items: Sequence[LocusSummary]) -> str:
    return _table(
        (
            "locus_key",
            "assembly_accession_version",
            "sequence_accession_version",
            "start0",
            "end0",
            "strand",
            "source_organism_name",
            "source_taxon",
            "viral_lineages",
        ),
        tuple(_locus_row(item) for item in items),
    )


def _assembly_table(items: Sequence[AssemblySummary]) -> str:
    return _table(
        (
            "assembly_accession_version",
            "assembly_key",
            "source_organism_name",
            "source_taxon",
            "included_locus_count",
        ),
        tuple(
            (
                item.assembly_accession_version,
                item.assembly_key,
                item.source_organism_name,
                item.source_taxon.canonical_name,
                item.included_locus_count,
            )
            for item in items
        ),
    )


def _page_metadata(data: LocusPageData | AssemblyPageData | SourceTaxonPageData) -> str:
    page = data.page
    return _table(
        ("limit", "returned_count", "total_count", "sort_key", "sort_direction", "next_cursor"),
        (
            (
                page.limit,
                page.returned_count,
                page.total_count,
                page.sort_key,
                page.sort_direction,
                page.next_cursor,
            ),
        ),
    )


def _detail_sections(data: LocusDetailData) -> list[str]:
    sections = ["### Locus", _locus_table((data.locus,))]
    sections.extend(
        (
            "### Detection calls",
            _table(
                (
                    "call_key",
                    "source_method_key",
                    "process_run_key",
                    "source_record_key",
                    "artifact_key",
                    "artifact_sha256",
                    "worksheet",
                    "row_number",
                ),
                tuple(
                    (
                        call.call_key,
                        call.source_method_key,
                        call.process_run_key,
                        call.source_record_key,
                        call.artifact_key,
                        call.artifact_sha256,
                        call.worksheet,
                        call.row_number,
                    )
                    for call in data.calls
                ),
            ),
            "### Public assertions",
            _table(
                (
                    "assertion_key",
                    "assertion_type",
                    "predicate_key",
                    "asserted_value",
                    "source_label",
                    "source_confidence",
                    "lineage_term_key",
                    "evidence_key",
                    "artifact_key",
                    "artifact_sha256",
                    "source_uri",
                ),
                tuple(
                    (
                        assertion.assertion_key,
                        assertion.assertion_type,
                        assertion.predicate_key,
                        assertion.asserted_value,
                        assertion.source_label,
                        assertion.source_confidence,
                        assertion.lineage.term_key if assertion.lineage is not None else None,
                        assertion.supporting_evidence.evidence_key,
                        assertion.supporting_evidence.artifact_key,
                        assertion.supporting_evidence.artifact_sha256,
                        assertion.supporting_evidence.source_uri,
                    )
                    for assertion in data.public_assertions
                ),
            ),
        )
    )
    return sections


def render_structured_result_table(result: StructuredResult) -> str:
    """Return a deterministic Markdown table view of one structured result."""

    sections = [
        "## Structured result",
        _table(
            ("release_key", "manifest_sha256", "plan_sha256", "result_kind"),
            (
                (
                    result.release.release_key,
                    result.release.manifest_sha256,
                    result.plan_sha256,
                    result.data.kind,
                ),
            ),
        ),
    ]
    data = result.data
    if isinstance(data, AssemblyDetailData):
        sections.extend(("### Assembly", _assembly_table((data.assembly,))))
    elif isinstance(data, LocusDetailData):
        sections.extend(_detail_sections(data))
    elif isinstance(data, LocusPageData):
        sections.extend(("### Page", _page_metadata(data), "### Loci", _locus_table(data.items)))
    elif isinstance(data, AssemblyPageData):
        sections.extend(
            ("### Page", _page_metadata(data), "### Assemblies", _assembly_table(data.items))
        )
    elif isinstance(data, SourceTaxonPageData):
        sections.extend(
            (
                "### Page",
                _page_metadata(data),
                "### Source taxa",
                _table(
                    (
                        "snapshot_key",
                        "term_key",
                        "canonical_name",
                        "rank",
                        "represented_assembly_count",
                        "included_locus_count",
                    ),
                    tuple(
                        (
                            item.lineage.snapshot_key,
                            item.lineage.term_key,
                            item.lineage.canonical_name,
                            item.lineage.rank,
                            item.represented_assembly_count,
                            item.included_locus_count,
                        )
                        for item in data.items
                    ),
                ),
            )
        )
    elif isinstance(data, AggregateData):
        sections.extend(
            (
                "### Aggregate",
                _table(
                    ("metric_key", "value", "unit", "deduplication_key"),
                    ((data.metric_key, data.value, data.unit, data.deduplication_key),),
                ),
            )
        )

    sections.extend(
        (
            "### Warnings",
            _table(
                ("code", "message"), tuple((item.code, item.message) for item in result.warnings)
            ),
            "### Limitations",
            _table(
                ("code", "message"),
                tuple((item.code, item.message) for item in result.limitations),
            ),
        )
    )
    return "\n\n".join(sections)


__all__ = [
    "StructuredResponseModel",
    "render_structured_result_table",
    "render_structured_result_text",
    "serialize_structured_response",
    "serialize_structured_result",
]
