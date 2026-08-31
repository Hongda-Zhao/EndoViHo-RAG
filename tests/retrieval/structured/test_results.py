"""Schema-only tests for the approved Milestone 2 structured responses."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListLociPlan,
    PageSpec,
    PlanningAudit,
    SemanticSpan,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    AssemblySummary,
    CallDetail,
    EntitySuggestion,
    ErrorResponse,
    EvidenceDetail,
    ExactPlacement,
    Limitation,
    LineageRef,
    LocusDetailData,
    LocusPageData,
    LocusSummary,
    PageInfo,
    PlanSuccess,
    PublicAssertionDetail,
    PublishedReleaseRef,
    QuerySuccess,
    ResolvedEntity,
    SourceTaxonPageData,
    SourceTaxonSummary,
    StructuredData,
    StructuredError,
    StructuredResponse,
    StructuredResult,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
ASSEMBLY = "GCA_029931535.1"
CONTIG = "ABCD01000001.1"
LOCUS = f"locus:eve:v1:sha256:{SHA_A}"
RELEASE = "release:endoviho-rag:v0:20260827:002"

LIMITATION_MESSAGES = {
    "assembly_local_locus_is_not_independent_integration_event": (
        "An assembly-local locus is not an independent integration event."
    ),
    "assembly_source_taxon_is_not_ancient_host": (
        "An assembly-source taxon is not evidence of an ancient host."
    ),
    "coordinates_are_zero_based_half_open": "Coordinates are zero-based and half-open.",
    "detection_calls_are_not_loci": "Detection calls and loci are distinct objects.",
    "source_confidence_is_not_release_validation": (
        "Source confidence is not independent release validation."
    ),
    "zero_matches_do_not_establish_biological_absence": (
        "Zero matching release records do not establish biological absence."
    ),
}


def _source_lineage(*, term_key: str = "lineage-term:ncbi:taxid-1") -> LineageRef:
    return LineageRef(
        term_key=term_key,
        canonical_name="Bivalvia",
        rank="class",
        snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )


def _viral_lineage(
    *,
    term_key: str = "lineage-term:study:orthopolintovirales",
) -> LineageRef:
    return LineageRef(
        term_key=term_key,
        canonical_name="Orthopolintovirales",
        rank=None,
        snapshot_key="lineage-snapshot:study:zhao-v4",
        authority_namespace="study-defined:10.1101/2025.04.19.649669:v4",
        snapshot_version="v4",
        scheme_kind="study_defined",
        role="study_viral_lineage",
    )


def test_extended_lineage_ref_retains_nonformal_provenance() -> None:
    lineage = LineageRef(
        term_key="extended:asfa-like",
        canonical_name="Asfa-like",
        rank="informal affinity group",
        snapshot_key="lineage-snapshot:extended:asfa-like-v1",
        authority_namespace="curated-extended-viral-lineage",
        snapshot_version="test-v1",
        scheme_kind="study_defined",
        role="extended_viral_lineage",
    )

    assert lineage.role == "extended_viral_lineage"
    assert lineage.scheme_kind == "study_defined"

    with pytest.raises(ValidationError, match="role and scheme_kind"):
        LineageRef.model_validate({**lineage.model_dump(), "scheme_kind": "formal_taxonomy"})


def _placement() -> ExactPlacement:
    return ExactPlacement(
        sequence_key=f"sequence:insdc:{CONTIG}",
        sequence_accession_version=CONTIG,
        start0=100,
        end0=200,
        strand="unknown",
    )


def _locus(*, viral_lineages: tuple[LineageRef, ...] | None = None) -> LocusSummary:
    return LocusSummary(
        locus_key=LOCUS,
        assembly_key=f"assembly:ncbi:{ASSEMBLY}",
        assembly_accession_version=ASSEMBLY,
        source_organism_name="Margaritifera margaritifera",
        source_taxon=_source_lineage(),
        placement=_placement(),
        viral_lineages=(_viral_lineage(),) if viral_lineages is None else viral_lineages,
    )


def _assembly() -> AssemblySummary:
    return AssemblySummary(
        assembly_key=f"assembly:ncbi:{ASSEMBLY}",
        assembly_accession_version=ASSEMBLY,
        source_organism_name="Margaritifera margaritifera",
        source_taxon=_source_lineage(),
        included_locus_count=1,
    )


def _evidence() -> EvidenceDetail:
    return EvidenceDetail(
        evidence_key="evidence:zhao-v4:row-1",
        evidence_type="supplementary_table_row",
        evidence_sha256=SHA_A,
        source_locator={"worksheet": "S3", "row": 39158},
        summary="The frozen source row.",
        artifact_key="source-artifact:biorxiv-data-s1:test",
        artifact_sha256=SHA_B,
        source_uri="https://example.invalid/data-s1.xlsx",
        verified_license_key="CC-BY-NC-ND-4.0",
    )


def _call() -> CallDetail:
    return CallDetail(
        call_key="detection-call:zhao-v4:test",
        source_method_key="zhao-data-s1-import",
        process_run_key="process-run:zhao-data-s1-import-v2:test",
        source_record_key="source-record:zhao-data-s1:test",
        artifact_key="source-artifact:biorxiv-data-s1:test",
        artifact_sha256=SHA_B,
        worksheet="S3",
        row_number=39158,
    )


def _assertion() -> PublicAssertionDetail:
    return PublicAssertionDetail(
        assertion_key="assertion:zhao-v4:hcvr:test",
        assertion_type="hcvr",
        predicate_key="predicate:source-hcvr-status",
        asserted_value="Yes",
        source_label="Yes",
        source_confidence="source_high",
        lineage=None,
        method_definition_key="method-definition:zhao-data-s1-import-v2",
        method_version="zhao-data-s1-import-v2",
        process_run_key="process-run:zhao-data-s1-import-v2:test",
        supporting_evidence=_evidence(),
    )


def _viral_assertion() -> PublicAssertionDetail:
    return PublicAssertionDetail(
        assertion_key="assertion:zhao-v4:viral-major-taxon:test",
        assertion_type="viral_major_taxon",
        predicate_key="predicate:viral-major-taxon",
        asserted_value="Orthopolintovirales",
        lineage=_viral_lineage(),
        method_definition_key="method-definition:zhao-data-s1-import-v2",
        method_version="zhao-data-s1-import-v2",
        process_run_key="process-run:zhao-data-s1-import-v2:test",
        supporting_evidence=_evidence(),
    )


def _release(*, release_key: str = RELEASE) -> PublishedReleaseRef:
    return PublishedReleaseRef(
        dataset_key="dataset:endoviho-rag",
        release_key=release_key,
        schema_version="milestone-1-v1",
        manifest_sha256=SHA_A,
        published_at=datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC),
    )


def _limitations(*codes: str) -> tuple[Limitation, ...]:
    return tuple(
        Limitation(code=code, message=LIMITATION_MESSAGES[code])  # type: ignore[arg-type]
        for code in sorted(codes)
    )


def _result(data: StructuredData, *limitation_codes: str) -> StructuredResult:
    return StructuredResult(
        plan_sha256=SHA_B,
        release=_release(),
        data=data,
        limitations=_limitations(*limitation_codes),
    )


def _plan() -> AssemblyDetailPlan:
    return AssemblyDetailPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="assembly_detail",
        original_question=f"Show assembly {ASSEMBLY}.",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                AssemblyFilter(
                    filter_type="assembly",
                    assembly_key=f"assembly:ncbi:{ASSEMBLY}",
                ),
            ),
        ),
    )


def _audit() -> PlanningAudit:
    condition = ExtractedCondition(
        condition_id="c-intent",
        source_text="Show assembly",
        source_start=0,
        source_end=13,
        condition_kind="intent",
        mapped_target="intent",
    )
    return PlanningAudit(
        extracted_conditions=(condition,),
        mapped_condition_ids=(condition.condition_id,),
    )


def test_six_data_variants_are_strictly_discriminated() -> None:
    locus = _locus()
    assembly = _assembly()
    source_taxon = SourceTaxonSummary(
        lineage=_source_lineage(),
        represented_assembly_count=1,
        included_locus_count=1,
    )
    variants: tuple[StructuredData, ...] = (
        AssemblyDetailData(assembly=assembly),
        LocusDetailData(
            locus=locus,
            calls=(_call(),),
            public_assertions=(_assertion(), _viral_assertion()),
        ),
        LocusPageData(
            items=(locus,),
            page=PageInfo(
                limit=50,
                returned_count=1,
                total_count=1,
                sort_key="locus_key",
            ),
        ),
        AssemblyPageData(
            items=(assembly,),
            page=PageInfo(
                limit=50,
                returned_count=1,
                total_count=1,
                sort_key="assembly_accession",
            ),
        ),
        SourceTaxonPageData(
            items=(source_taxon,),
            page=PageInfo(
                limit=50,
                returned_count=1,
                total_count=1,
                sort_key="source_taxon_key",
            ),
        ),
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        ),
    )

    adapter = TypeAdapter(StructuredData)
    assert [adapter.validate_python(item).kind for item in variants] == [
        "assembly_detail",
        "locus_detail",
        "locus_page",
        "assembly_page",
        "source_taxon_page",
        "aggregate",
    ]

    invalid = variants[0].model_dump()
    invalid["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        adapter.validate_python(invalid)


def test_public_projection_rejects_identity_coordinate_and_role_drift() -> None:
    with pytest.raises(ValidationError, match="assembly_key does not match"):
        LocusSummary(
            locus_key=LOCUS,
            assembly_key="assembly:ncbi:GCA_000000001.1",
            assembly_accession_version=ASSEMBLY,
            source_organism_name="Margaritifera margaritifera",
            source_taxon=_source_lineage(),
            placement=_placement(),
        )

    with pytest.raises(ValidationError, match="start0 < end0"):
        ExactPlacement(
            sequence_key=f"sequence:insdc:{CONTIG}",
            sequence_accession_version=CONTIG,
            start0=200,
            end0=200,
            strand="unknown",
        )

    with pytest.raises(ValidationError, match="assembly_source_taxonomy"):
        AssemblySummary(
            assembly_key=f"assembly:ncbi:{ASSEMBLY}",
            assembly_accession_version=ASSEMBLY,
            source_organism_name="Margaritifera margaritifera",
            source_taxon=_viral_lineage(),
            included_locus_count=1,
        )


def test_nested_public_records_require_canonical_order_and_typed_assertions() -> None:
    later = _viral_lineage(term_key="lineage-term:study:z-last")
    earlier = _viral_lineage(term_key="lineage-term:study:a-first")
    with pytest.raises(ValidationError, match="canonical order"):
        _locus(viral_lineages=(later, earlier))

    assertion = _assertion()
    invalid_assertion = assertion.model_dump()
    invalid_assertion["source_confidence"] = "source_low"
    with pytest.raises(ValidationError, match="inconsistent"):
        PublicAssertionDetail.model_validate(invalid_assertion)

    later_call = _call().model_copy(update={"call_key": "detection-call:zhao-v4:z"})
    earlier_call = _call().model_copy(update={"call_key": "detection-call:zhao-v4:a"})
    with pytest.raises(ValidationError, match="calls must be in canonical order"):
        LocusDetailData(
            locus=_locus(viral_lineages=()),
            calls=(later_call, earlier_call),
        )

    invalid_evidence = _evidence().model_dump()
    invalid_evidence["source_locator"] = {"score": 0.5}
    with pytest.raises(ValidationError):
        EvidenceDetail.model_validate(invalid_evidence)

    immutable_evidence = EvidenceDetail.model_validate(
        {
            **_evidence().model_dump(),
            "source_locator": {"worksheet": "S3", "nested": {"row": 39158}},
        }
    )
    with pytest.raises(TypeError):
        immutable_evidence.source_locator["worksheet"] = "changed"  # type: ignore[index]
    nested_copy = immutable_evidence.source_locator["nested"]
    assert isinstance(nested_copy, dict)
    nested_copy["row"] = 1
    assert immutable_evidence.source_locator["nested"] == {"row": 39158}
    assert (
        EvidenceDetail.model_validate_json(immutable_evidence.model_dump_json())
        == immutable_evidence
    )


def test_locus_detail_binds_summary_lineages_to_public_assertions() -> None:
    detail = LocusDetailData(
        locus=_locus(),
        public_assertions=(_assertion(), _viral_assertion()),
    )
    assert detail.locus.viral_lineages == (_viral_lineage(),)

    with pytest.raises(ValidationError, match="must equal the public viral"):
        LocusDetailData(locus=_locus(), public_assertions=(_assertion(),))

    with pytest.raises(ValidationError, match="must equal the public viral"):
        LocusDetailData(
            locus=_locus(viral_lineages=()),
            public_assertions=(_viral_assertion(),),
        )


def test_represented_summary_counts_must_be_positive() -> None:
    assembly_payload = _assembly().model_dump()
    assembly_payload["included_locus_count"] = 0
    with pytest.raises(ValidationError):
        AssemblySummary.model_validate(assembly_payload)

    with pytest.raises(ValidationError):
        SourceTaxonSummary(
            lineage=_source_lineage(),
            represented_assembly_count=0,
            included_locus_count=1,
        )


def test_page_metadata_matches_items_total_and_fixed_sort() -> None:
    page = PageInfo(
        limit=1,
        returned_count=1,
        total_count=10,
        next_cursor="abc_DEF-123",
        sort_key="locus_key",
    )
    assert LocusPageData(items=(_locus(),), page=page).page.total_count == 10

    with pytest.raises(ValidationError, match="returned_count must equal"):
        LocusPageData(
            items=(_locus(),),
            page=page.model_copy(update={"returned_count": 0, "next_cursor": None}),
        )

    with pytest.raises(ValidationError, match="locus_key sorting"):
        LocusPageData(
            items=(_locus(),),
            page=page.model_copy(update={"sort_key": "assembly_accession"}),
        )

    with pytest.raises(ValidationError, match="full page"):
        PageInfo(
            limit=2,
            returned_count=1,
            total_count=10,
            next_cursor="abc",
            sort_key="locus_key",
        )

    with pytest.raises(ValidationError, match="additional results"):
        PageInfo(
            limit=1,
            returned_count=1,
            total_count=1,
            next_cursor="abc",
            sort_key="locus_key",
        )

    with pytest.raises(ValidationError, match="empty locus page"):
        LocusPageData(
            items=(),
            page=PageInfo(
                limit=50,
                returned_count=0,
                total_count=1,
                sort_key="locus_key",
            ),
        )


@pytest.mark.parametrize(
    ("metric_key", "unit", "deduplication_key"),
    [
        ("distinct_included_locus_count", "loci", "release_key+locus_key"),
        (
            "distinct_contig_count",
            "contigs",
            "assembly_accession_version+sequence_accession_version",
        ),
        ("distinct_assembly_count", "assemblies", "assembly_accession_version"),
        ("distinct_source_taxon_count", "source_taxa", "snapshot_key+term_key"),
        ("detection_call_count", "source_calls", "release_key+call_key"),
    ],
)
def test_aggregate_metric_metadata_is_frozen(
    metric_key: str,
    unit: str,
    deduplication_key: str,
) -> None:
    payload = {
        "metric_key": metric_key,
        "value": 0,
        "unit": unit,
        "deduplication_key": deduplication_key,
    }
    assert AggregateData.model_validate(payload).value == 0

    payload["unit"] = "loci" if unit != "loci" else "assemblies"
    with pytest.raises(ValidationError, match="approved contract"):
        AggregateData.model_validate(payload)


def test_structured_result_requires_sorted_typed_scientific_limitations() -> None:
    data = LocusPageData(
        items=(_locus(),),
        page=PageInfo(
            limit=50,
            returned_count=1,
            total_count=1,
            sort_key="locus_key",
        ),
    )
    required = (
        "assembly_local_locus_is_not_independent_integration_event",
        "assembly_source_taxon_is_not_ancient_host",
        "coordinates_are_zero_based_half_open",
    )
    result = _result(data, *required)
    assert tuple(item.code for item in result.limitations) == required
    assert "generated_at" not in result.model_dump(mode="json")

    with pytest.raises(ValidationError, match="required limitation codes are missing"):
        _result(data, "assembly_source_taxon_is_not_ancient_host")

    invalid = result.model_dump()
    invalid["limitations"] = ["coordinates are canonical"]
    with pytest.raises(ValidationError):
        StructuredResult.model_validate(invalid)

    with pytest.raises(ValidationError, match="canonical order"):
        StructuredResult(
            plan_sha256=SHA_B,
            release=_release(),
            data=data,
            limitations=tuple(reversed(_limitations(*required))),
        )

    with pytest.raises(ValidationError, match="unexpected limitation codes"):
        _result(
            data,
            *required,
            "source_confidence_is_not_release_validation",
        )


def test_zero_result_requires_non_absence_limitation() -> None:
    data = AggregateData(
        metric_key="distinct_assembly_count",
        value=0,
        unit="assemblies",
        deduplication_key="assembly_accession_version",
    )
    with pytest.raises(ValidationError, match="zero_matches"):
        _result(data)

    result = _result(data, "zero_matches_do_not_establish_biological_absence")
    assert result.data.value == 0


def test_published_release_provenance_is_exact_and_timezone_aware() -> None:
    release = _release()
    assert release.status == "published"
    with pytest.raises(ValidationError, match="immutable grammar"):
        PublishedReleaseRef(
            dataset_key="dataset:endoviho-rag",
            release_key="latest",
            schema_version="milestone-1-v1",
            manifest_sha256=SHA_A,
            published_at=datetime(2026, 8, 27, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        PublishedReleaseRef(
            dataset_key="dataset:endoviho-rag",
            release_key=RELEASE,
            schema_version="milestone-1-v1",
            manifest_sha256=SHA_A,
            published_at=datetime(2026, 8, 27),
        )
    with pytest.raises(ValidationError, match="dataset:endoviho-rag"):
        PublishedReleaseRef(
            dataset_key="dataset:other",  # type: ignore[arg-type]
            release_key=RELEASE,
            schema_version="milestone-1-v1",
            manifest_sha256=SHA_A,
            published_at=datetime(2026, 8, 27, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="immutable grammar"):
        PublishedReleaseRef(
            dataset_key="dataset:endoviho-rag",
            release_key="release:other:v0:20260827:002",
            schema_version="milestone-1-v1",
            manifest_sha256=SHA_A,
            published_at=datetime(2026, 8, 27, tzinfo=UTC),
        )


def test_error_response_is_typed_fail_closed_and_strict() -> None:
    response = ErrorResponse(
        error=StructuredError(
            code="release_not_published",
            message="The requested release is not published.",
        )
    )
    parsed = TypeAdapter(StructuredResponse).validate_python(response)
    assert parsed.response_kind == "error"
    assert parsed.fact_retrieval_executed is False
    assert parsed.structured_result is None
    assert TypeAdapter(StructuredResponse).validate_json(response.model_dump_json()) == response

    invalid = response.model_dump()
    invalid["sql"] = "SELECT *"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TypeAdapter(StructuredResponse).validate_python(invalid)

    with pytest.raises(ValidationError):
        StructuredError(code="not_an_approved_code", message="No.")  # type: ignore[arg-type]


def test_plan_and_query_success_are_discriminated_and_fact_exact() -> None:
    plan = _plan()
    audit = _audit()
    entity = ResolvedEntity(
        original_input=ASSEMBLY,
        entity_kind="assembly",
        match_mode="exact_identifier",
        stable_key=f"assembly:ncbi:{ASSEMBLY}",
        canonical_name="Margaritifera margaritifera",
    )
    plan_response = PlanSuccess(
        query_plan=plan,
        planning_audit=audit,
        resolved_entities=(entity,),
    )
    query_response = QuerySuccess(
        query_plan=plan,
        planning_audit=audit,
        resolved_entities=(entity,),
        structured_result=StructuredResult(
            plan_sha256=canonical_plan_sha256(plan),
            release=_release(),
            data=AssemblyDetailData(assembly=_assembly()),
            limitations=_limitations("assembly_source_taxon_is_not_ancient_host"),
        ),
    )

    adapter = TypeAdapter(StructuredResponse)
    assert adapter.validate_python(plan_response).response_kind == "plan_success"
    parsed_query = adapter.validate_python(query_response)
    assert parsed_query.response_kind == "query_success"
    assert parsed_query.fact_retrieval_executed is True
    assert parsed_query.structured_result.plan_sha256 == canonical_plan_sha256(plan)
    assert "generated_at" not in parsed_query.model_dump(mode="json")
    assert adapter.validate_json(query_response.model_dump_json()) == query_response

    with pytest.raises(ValidationError, match="Input should be False"):
        PlanSuccess(
            query_plan=plan,
            planning_audit=audit,
            fact_retrieval_executed=True,  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError, match="frozen"):
        query_response.fact_retrieval_executed = False  # type: ignore[misc]


def test_success_rejects_incomplete_planning_audit() -> None:
    with pytest.raises(ValidationError, match="at least one extracted condition"):
        PlanSuccess(query_plan=_plan(), planning_audit=PlanningAudit())

    audit = _audit().model_copy(
        update={
            "unconsumed_semantic_spans": (
                SemanticSpan(source_text="except", source_start=0, source_end=6),
            )
        }
    )
    with pytest.raises(ValidationError, match="semantic span"):
        PlanSuccess(query_plan=_plan(), planning_audit=audit)

    with pytest.raises(ValidationError, match="resolved_entities"):
        PlanSuccess(query_plan=_plan(), planning_audit=_audit())


def test_query_success_binds_hash_release_intent_and_detail_identity() -> None:
    plan = _plan()
    valid_result = StructuredResult(
        plan_sha256=canonical_plan_sha256(plan),
        release=_release(),
        data=AssemblyDetailData(assembly=_assembly()),
        limitations=_limitations("assembly_source_taxon_is_not_ancient_host"),
    )

    with pytest.raises(ValidationError, match="plan_sha256"):
        QuerySuccess(
            query_plan=plan,
            planning_audit=_audit(),
            structured_result=valid_result.model_copy(update={"plan_sha256": SHA_B}),
        )

    with pytest.raises(ValidationError, match="release_key"):
        QuerySuccess(
            query_plan=plan,
            planning_audit=_audit(),
            structured_result=valid_result.model_copy(
                update={"release": _release(release_key="release:endoviho-rag:v0:20260827:003")}
            ),
        )

    wrong_kind = StructuredResult(
        plan_sha256=canonical_plan_sha256(plan),
        release=_release(),
        data=AggregateData(
            metric_key="distinct_assembly_count",
            value=1,
            unit="assemblies",
            deduplication_key="assembly_accession_version",
        ),
    )
    with pytest.raises(ValidationError, match="data kind"):
        QuerySuccess(
            query_plan=plan,
            planning_audit=_audit(),
            structured_result=wrong_kind,
        )

    other_assembly = AssemblySummary(
        assembly_key="assembly:ncbi:GCA_000000001.1",
        assembly_accession_version="GCA_000000001.1",
        source_organism_name="Other species",
        source_taxon=_source_lineage(),
        included_locus_count=1,
    )
    wrong_identity = valid_result.model_copy(
        update={"data": AssemblyDetailData(assembly=other_assembly)}
    )
    with pytest.raises(ValidationError, match="does not match its plan filter"):
        QuerySuccess(
            query_plan=plan,
            planning_audit=_audit(),
            structured_result=wrong_identity,
        )


def test_query_success_binds_aggregate_metric_and_list_limit() -> None:
    aggregate_plan = AggregatePlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="aggregate",
        original_question="Count assemblies in this release.",
        scope=EntireReleaseScope(
            scope_type="entire_release",
            explicitly_requested=True,
        ),
        metric_key="distinct_assembly_count",
    )
    aggregate_result = StructuredResult(
        plan_sha256=canonical_plan_sha256(aggregate_plan),
        release=_release(),
        data=AggregateData(
            metric_key="distinct_contig_count",
            value=1,
            unit="contigs",
            deduplication_key=("assembly_accession_version+sequence_accession_version"),
        ),
        limitations=_limitations("assembly_local_locus_is_not_independent_integration_event"),
    )
    with pytest.raises(ValidationError, match="metric does not match"):
        QuerySuccess(
            query_plan=aggregate_plan,
            planning_audit=_audit(),
            structured_result=aggregate_result,
        )

    list_plan = ListLociPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_loci",
        original_question="List all loci in this release.",
        scope=EntireReleaseScope(
            scope_type="entire_release",
            explicitly_requested=True,
        ),
        page=PageSpec(limit=50),
    )
    list_result = StructuredResult(
        plan_sha256=canonical_plan_sha256(list_plan),
        release=_release(),
        data=LocusPageData(
            items=(_locus(),),
            page=PageInfo(
                limit=51,
                returned_count=1,
                total_count=1,
                sort_key="locus_key",
            ),
        ),
        limitations=_limitations(
            "assembly_local_locus_is_not_independent_integration_event",
            "assembly_source_taxon_is_not_ancient_host",
            "coordinates_are_zero_based_half_open",
        ),
    )
    with pytest.raises(ValidationError, match="page limit does not match"):
        QuerySuccess(
            query_plan=list_plan,
            planning_audit=_audit(),
            structured_result=list_result,
        )


def test_post_query_error_requires_plan_and_audit_but_never_partial_result() -> None:
    with pytest.raises(ValidationError, match="post-query errors"):
        ErrorResponse(
            error=StructuredError(
                code="structured_query_failed",
                message="The structured query failed.",
            ),
            fact_retrieval_executed=True,
        )

    response = ErrorResponse(
        query_plan=_plan(),
        planning_audit=_audit(),
        error=StructuredError(
            code="structured_query_failed",
            message="The structured query failed.",
        ),
        fact_retrieval_executed=True,
    )
    assert response.structured_result is None

    with pytest.raises(ValidationError, match="cannot follow public fact retrieval"):
        ErrorResponse(
            query_plan=_plan(),
            planning_audit=_audit(),
            error=StructuredError(
                code="release_not_published",
                message="The release is not published.",
            ),
            fact_retrieval_executed=True,
        )

    incomplete_audit = _audit().model_copy(
        update={
            "unconsumed_semantic_spans": (
                SemanticSpan(source_text="except", source_start=0, source_end=6),
            )
        }
    )
    with pytest.raises(ValidationError, match="semantic span"):
        ErrorResponse(
            query_plan=_plan(),
            planning_audit=incomplete_audit,
            error=StructuredError(
                code="structured_query_failed",
                message="The structured query failed.",
            ),
            fact_retrieval_executed=True,
        )


def test_release_error_cannot_expose_entity_suggestions() -> None:
    suggestion = EntitySuggestion(
        entity_kind="assembly",
        stable_key=f"assembly:ncbi:{ASSEMBLY}",
        canonical_name="Margaritifera margaritifera",
    )
    with pytest.raises(ValidationError, match="release errors"):
        ErrorResponse(
            error=StructuredError(
                code="release_not_found",
                message="The release was not found.",
                suggestions=(suggestion,),
            )
        )


def test_resolved_entities_require_complete_lineage_provenance_and_order() -> None:
    assembly = ResolvedEntity(
        original_input=ASSEMBLY,
        entity_kind="assembly",
        match_mode="exact_identifier",
        stable_key=f"assembly:ncbi:{ASSEMBLY}",
        canonical_name="Margaritifera margaritifera",
    )
    lineage = ResolvedEntity(
        original_input="Bivalvia",
        entity_kind="source_lineage",
        match_mode="exact_canonical_name",
        stable_key="lineage-term:ncbi:taxid-6544",
        canonical_name="Bivalvia",
        snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )
    assert (assembly.entity_kind, lineage.entity_kind) == ("assembly", "source_lineage")

    with pytest.raises(ValidationError, match="complete snapshot provenance"):
        ResolvedEntity(
            original_input="Bivalvia",
            entity_kind="source_lineage",
            match_mode="exact_canonical_name",
            stable_key="lineage-term:ncbi:taxid-6544",
            canonical_name="Bivalvia",
        )

    with pytest.raises(ValidationError, match="role and scheme_kind"):
        ResolvedEntity(
            original_input="Orthopolintovirales",
            entity_kind="viral_lineage",
            match_mode="exact_canonical_name",
            stable_key="lineage-term:study:orthopolintovirales",
            canonical_name="Orthopolintovirales",
            snapshot_key="lineage-snapshot:study:zhao-v4",
            authority_namespace="study-defined:zhao-v4",
            snapshot_version="v4",
            scheme_kind="formal_taxonomy",
            role="study_viral_lineage",
        )

    with pytest.raises(ValidationError, match="lineage suggestions require"):
        EntitySuggestion(
            entity_kind="source_lineage",
            stable_key="lineage-term:ncbi:taxid-6544",
            canonical_name="Bivalvia",
        )
