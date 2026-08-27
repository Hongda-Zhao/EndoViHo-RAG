from __future__ import annotations

import json
from datetime import UTC, datetime

from eve_relation_rag.retrieval.structured.rendering import (
    render_structured_result_table,
    render_structured_result_text,
    serialize_structured_response,
    serialize_structured_result,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblySummary,
    ErrorResponse,
    Limitation,
    LineageRef,
    PublishedReleaseRef,
    StructuredError,
    StructuredResult,
)

RELEASE = "release:endoviho-rag:v0:20260827:001"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _release() -> PublishedReleaseRef:
    return PublishedReleaseRef(
        dataset_key="dataset:endoviho-rag",
        release_key=RELEASE,
        schema_version="milestone-1-v1",
        manifest_sha256=SHA_A,
        published_at=datetime(2026, 8, 27, tzinfo=UTC),
    )


def _aggregate_result() -> StructuredResult:
    return StructuredResult(
        plan_sha256=SHA_B,
        release=_release(),
        data=AggregateData(
            metric_key="distinct_assembly_count",
            value=2,
            unit="assemblies",
            deduplication_key="assembly_accession_version",
        ),
    )


def test_canonical_result_and_response_serializers_are_stable() -> None:
    result = _aggregate_result()
    first = serialize_structured_result(result)
    second = serialize_structured_result(result.model_copy())

    assert first == second
    assert first.startswith('{"data":')
    assert "generated_at" not in first
    assert json.loads(first) == result.model_dump(mode="json")

    response = ErrorResponse(
        error=StructuredError(
            code="cursor_invalid",
            message="The cursor is malformed or unauthenticated.",
        )
    )
    serialized_response = serialize_structured_response(response)
    assert json.loads(serialized_response) == response.model_dump(mode="json")


def test_text_and_markdown_renderers_are_deterministic_and_result_only() -> None:
    result = _aggregate_result()

    text = render_structured_result_text(result)
    assert text == render_structured_result_text(result)
    assert f"plan_sha256={SHA_B}" in text
    assert '"metric_key":"distinct_assembly_count"' in text

    table = render_structured_result_table(result)
    assert table == render_structured_result_table(result)
    assert "| metric_key | value | unit | deduplication_key |" in table
    assert "| distinct_assembly_count | 2 | assemblies | assembly_accession_version |" in table


def test_markdown_renderer_escapes_public_text_without_interpreting_it() -> None:
    lineage = LineageRef(
        term_key="lineage-term:ncbi:taxid-1",
        canonical_name="Bivalvia | test",
        rank="class",
        snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )
    result = StructuredResult(
        plan_sha256=SHA_B,
        release=_release(),
        data=AssemblyDetailData(
            assembly=AssemblySummary(
                assembly_key="assembly:ncbi:GCA_1.1",
                assembly_accession_version="GCA_1.1",
                source_organism_name="Organism | literal",
                source_taxon=lineage,
                included_locus_count=1,
            )
        ),
        limitations=(
            Limitation(
                code="assembly_source_taxon_is_not_ancient_host",
                message="Assembly source taxon is not an ancient host.",
            ),
        ),
    )

    table = render_structured_result_table(result)
    assert "Organism \\| literal" in table
    assert "Bivalvia \\| test" in table
