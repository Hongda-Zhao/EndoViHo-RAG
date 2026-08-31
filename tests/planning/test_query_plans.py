from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from eve_relation_rag.planning.query_plans import (
    PLAN_VERSION,
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    PageSpec,
    PlanningAudit,
    SemanticSpan,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
    canonical_plan_json,
    canonical_plan_sha256,
)

RELEASE_KEY = "release:endoviho-rag:v0:20260827:001"
ASSEMBLY_KEY = "assembly:ncbi:GCA_029931535.1"
LOCUS_KEY = f"locus:eve:v1:sha256:{'a' * 64}"
HOST_SNAPSHOT = "lineage-snapshot:ncbi-taxonomy:2026-08-26"
HOST_TERM = "ncbi-taxonomy:taxid:6544"
VIRAL_SNAPSHOT = "lineage-snapshot:study-zhao-v4"
VIRAL_TERM = "study-viral-major-taxon:orthopolintovirales"


def _assembly_filter() -> AssemblyFilter:
    return AssemblyFilter(filter_type="assembly", assembly_key=ASSEMBLY_KEY)


def _locus_filter() -> LocusFilter:
    return LocusFilter(filter_type="locus", locus_key=LOCUS_KEY)


def _source_lineage_filter() -> SourceLineageFilter:
    return SourceLineageFilter(
        filter_type="source_lineage",
        snapshot_key=HOST_SNAPSHOT,
        term_key=HOST_TERM,
        role="assembly_source_taxonomy",
        include_descendants=False,
    )


def _viral_lineage_filter() -> ViralLineageFilter:
    return ViralLineageFilter(
        filter_type="viral_lineage",
        snapshot_key=VIRAL_SNAPSHOT,
        term_key=VIRAL_TERM,
        role="study_viral_lineage",
        include_descendants=False,
    )


def test_extended_viral_lineage_role_is_a_valid_typed_filter() -> None:
    query_filter = ViralLineageFilter(
        filter_type="viral_lineage",
        snapshot_key="lineage-snapshot:extended:asfa-like-v1",
        term_key="extended:asfa-like",
        role="extended_viral_lineage",
        include_descendants=True,
    )

    assert query_filter.role == "extended_viral_lineage"
    assert query_filter.include_descendants is True


def _base_fields(question: str) -> dict[str, str]:
    return {
        "plan_version": PLAN_VERSION,
        "route": "structured",
        "release_key": RELEASE_KEY,
        "original_question": question,
    }


def _list_loci_plan(*, cursor: str | None = None, limit: int = 50) -> ListLociPlan:
    return ListLociPlan(
        **_base_fields("List loci under the requested filters."),
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                _viral_lineage_filter(),
                _assembly_filter(),
                _source_lineage_filter(),
            ),
        ),
        page=PageSpec(limit=limit, cursor=cursor),
    )


def test_all_six_intents_validate_through_discriminated_union() -> None:
    plans: tuple[StructuredPlan, ...] = (
        AssemblyDetailPlan(
            **_base_fields("Show assembly GCA_029931535.1."),
            intent="assembly_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_assembly_filter(),)),
        ),
        LocusDetailPlan(
            **_base_fields(f"Show locus {LOCUS_KEY}."),
            intent="locus_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_locus_filter(),)),
        ),
        _list_loci_plan(),
        ListAssembliesPlan(
            **_base_fields("List assemblies under the requested viral lineage."),
            intent="list_assemblies",
            scope=FilteredScope(scope_type="filtered", filters=(_viral_lineage_filter(),)),
            page=PageSpec(),
        ),
        ListSourceTaxaPlan(
            **_base_fields("List source taxa represented in this release."),
            intent="list_source_taxa",
            scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
            page=PageSpec(),
        ),
        AggregatePlan(
            **_base_fields("Count distinct contigs in this release."),
            intent="aggregate",
            scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
            metric_key="distinct_contig_count",
        ),
    )
    adapter = TypeAdapter(StructuredPlan)

    validated = tuple(adapter.validate_json(plan.model_dump_json()) for plan in plans)

    assert tuple(type(plan) for plan in validated) == tuple(type(plan) for plan in plans)


@pytest.mark.parametrize(
    "release_key",
    [
        "release:eve-relation:v0:20260827:001",
        "release:endoviho-rag:v1:20260827:001",
        "release:endoviho-rag:v0:20260230:001",
        "latest",
    ],
)
def test_release_key_requires_exact_project_v0_grammar(release_key: str) -> None:
    with pytest.raises(ValidationError):
        AssemblyDetailPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=release_key,
            original_question="Show the assembly.",
            intent="assembly_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_assembly_filter(),)),
        )


@pytest.mark.parametrize(
    "assembly_key",
    [
        "GCA_029931535.1",
        "assembly:ncbi:GCA_029931535",
        "assembly:ncbi:GCA_029931535.0",
        "assembly:ensembl:GCA_029931535.1",
    ],
)
def test_assembly_filter_requires_versioned_ncbi_stable_key(assembly_key: str) -> None:
    with pytest.raises(ValidationError):
        AssemblyFilter(filter_type="assembly", assembly_key=assembly_key)


def test_assembly_filter_accepts_versioned_gcf_key() -> None:
    query_filter = AssemblyFilter(
        filter_type="assembly", assembly_key="assembly:ncbi:GCF_000001405.40"
    )

    assert query_filter.assembly_key == "assembly:ncbi:GCF_000001405.40"


@pytest.mark.parametrize(
    "locus_key",
    [
        f"locus:eve:sha256:{'a' * 64}",
        f"locus:eve:v1:sha256:{'A' * 64}",
        f"locus:eve:v1:sha256:{'a' * 63}",
    ],
)
def test_locus_filter_requires_v1_lowercase_sha256_key(locus_key: str) -> None:
    with pytest.raises(ValidationError):
        LocusFilter(filter_type="locus", locus_key=locus_key)


def test_scope_rejects_empty_duplicate_and_too_many_filters() -> None:
    with pytest.raises(ValidationError):
        FilteredScope(scope_type="filtered", filters=())
    with pytest.raises(ValidationError, match="each filter_type may appear at most once"):
        FilteredScope(scope_type="filtered", filters=(_assembly_filter(), _assembly_filter()))
    with pytest.raises(ValidationError):
        FilteredScope(
            scope_type="filtered",
            filters=(
                _assembly_filter(),
                _locus_filter(),
                _source_lineage_filter(),
                _viral_lineage_filter(),
            ),
        )


def test_scope_canonicalizes_filter_order() -> None:
    scope = FilteredScope(
        scope_type="filtered",
        filters=(
            _viral_lineage_filter(),
            _source_lineage_filter(),
            _assembly_filter(),
        ),
    )

    assert tuple(item.filter_type for item in scope.filters) == (
        "assembly",
        "source_lineage",
        "viral_lineage",
    )


def test_entire_release_scope_requires_explicit_literal_true() -> None:
    with pytest.raises(ValidationError):
        EntireReleaseScope(scope_type="entire_release", explicitly_requested=False)
    with pytest.raises(ValidationError):
        EntireReleaseScope.model_validate({"scope_type": "entire_release"})


def test_detail_intents_require_their_one_exact_filter() -> None:
    with pytest.raises(ValidationError, match="requires exactly these filters: assembly"):
        AssemblyDetailPlan(
            **_base_fields("Show one assembly."),
            intent="assembly_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_locus_filter(),)),
        )
    with pytest.raises(ValidationError, match="requires a filtered scope"):
        LocusDetailPlan(
            **_base_fields("Show one locus."),
            intent="locus_detail",
            scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
        )


@pytest.mark.parametrize(
    ("plan_class", "intent", "query_filter"),
    [
        (ListLociPlan, "list_loci", _locus_filter()),
        (ListAssembliesPlan, "list_assemblies", _assembly_filter()),
        (ListSourceTaxaPlan, "list_source_taxa", _source_lineage_filter()),
    ],
)
def test_list_intents_reject_incompatible_filters(
    plan_class: type[ListLociPlan | ListAssembliesPlan | ListSourceTaxaPlan],
    intent: str,
    query_filter: AssemblyFilter | LocusFilter | SourceLineageFilter,
) -> None:
    with pytest.raises(ValidationError, match="filters incompatible with this intent"):
        plan_class(
            **_base_fields("List records."),
            intent=intent,
            scope=FilteredScope(scope_type="filtered", filters=(query_filter,)),
            page=PageSpec(),
        )


def test_aggregate_rejects_locus_filter() -> None:
    with pytest.raises(ValidationError, match="filters incompatible with this intent"):
        AggregatePlan(
            **_base_fields("Count records for one locus."),
            intent="aggregate",
            scope=FilteredScope(scope_type="filtered", filters=(_locus_filter(),)),
            metric_key="distinct_included_locus_count",
        )


def test_list_requires_page_and_forbids_metric() -> None:
    adapter = TypeAdapter(StructuredPlan)
    payload = _list_loci_plan().model_dump(mode="json")
    payload.pop("page")
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(payload))

    payload = _list_loci_plan().model_dump(mode="json")
    payload["metric_key"] = "distinct_included_locus_count"
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(payload))


def test_aggregate_requires_metric_and_forbids_page() -> None:
    adapter = TypeAdapter(StructuredPlan)
    plan = AggregatePlan(
        **_base_fields("Count loci in this release."),
        intent="aggregate",
        scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
        metric_key="distinct_included_locus_count",
    )
    payload = plan.model_dump(mode="json")
    payload.pop("metric_key")
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(payload))

    payload = plan.model_dump(mode="json")
    payload["page"] = {"limit": 50, "cursor": None}
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(payload))


def test_strict_types_unknown_fields_question_and_cursor_bounds() -> None:
    with pytest.raises(ValidationError):
        PageSpec(limit="50")
    with pytest.raises(ValidationError):
        PageSpec(limit=50, cursor="not.base64url")
    with pytest.raises(ValidationError):
        AssemblyFilter(filter_type="assembly", assembly_key=ASSEMBLY_KEY, sql="SELECT 1")
    with pytest.raises(ValidationError):
        AssemblyDetailPlan(
            **_base_fields("Show an assembly.\nDROP TABLE dataset;"),
            intent="assembly_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_assembly_filter(),)),
        )
    with pytest.raises(ValidationError):
        AssemblyDetailPlan(
            **_base_fields("   "),
            intent="assembly_detail",
            scope=FilteredScope(scope_type="filtered", filters=(_assembly_filter(),)),
        )
    with pytest.raises(ValidationError):
        SourceLineageFilter(
            filter_type="source_lineage",
            snapshot_key="snapshot\u200bhidden",
            term_key=HOST_TERM,
            role="assembly_source_taxonomy",
            include_descendants=False,
        )


def test_models_are_frozen() -> None:
    plan = _list_loci_plan()

    with pytest.raises(ValidationError):
        plan.original_question = "Changed question."


def test_canonical_json_sorts_filters_and_nulls_cursor() -> None:
    plan = _list_loci_plan(cursor="signed_cursor_1")
    expected = (
        '{"intent":"list_loci","original_question":"List loci under the requested filters.",'
        '"page":{"cursor":null,"limit":50},'
        f'"plan_version":"{PLAN_VERSION}","release_key":"{RELEASE_KEY}",'
        '"route":"structured","scope":{"filters":['
        f'{{"assembly_key":"{ASSEMBLY_KEY}","filter_type":"assembly"}},'
        '{"filter_type":"source_lineage","include_descendants":false,'
        f'"role":"assembly_source_taxonomy","snapshot_key":"{HOST_SNAPSHOT}",'
        f'"term_key":"{HOST_TERM}"}},'
        '{"filter_type":"viral_lineage","include_descendants":false,'
        f'"role":"study_viral_lineage","snapshot_key":"{VIRAL_SNAPSHOT}",'
        f'"term_key":"{VIRAL_TERM}"}}],"scope_type":"filtered"}}'
        "}"
    )

    assert canonical_plan_json(plan) == expected
    assert canonical_plan_sha256(plan) == hashlib.sha256(expected.encode()).hexdigest()


def test_cursor_and_filter_input_order_do_not_change_plan_hash() -> None:
    first = _list_loci_plan(cursor="cursor_one")
    second = ListLociPlan(
        **_base_fields("List loci under the requested filters."),
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                _source_lineage_filter(),
                _viral_lineage_filter(),
                _assembly_filter(),
            ),
        ),
        page=PageSpec(limit=50, cursor="cursor_two"),
    )

    assert canonical_plan_json(first) == canonical_plan_json(second)
    assert canonical_plan_sha256(first) == canonical_plan_sha256(second)


def test_limit_and_original_question_change_plan_hash() -> None:
    baseline = _list_loci_plan(limit=50)
    different_limit = _list_loci_plan(limit=51)
    different_question = baseline.model_copy(
        update={"original_question": "List the same loci under the requested filters."}
    )

    assert canonical_plan_sha256(baseline) != canonical_plan_sha256(different_limit)
    assert canonical_plan_sha256(baseline) != canonical_plan_sha256(different_question)


def test_every_filter_and_release_semantic_changes_plan_hash() -> None:
    baseline = _list_loci_plan()
    variants = (
        baseline.model_copy(update={"release_key": "release:endoviho-rag:v0:20260827:002"}),
        baseline.model_copy(
            update={
                "scope": FilteredScope(
                    scope_type="filtered",
                    filters=(
                        AssemblyFilter(
                            filter_type="assembly",
                            assembly_key="assembly:ncbi:GCA_000000001.1",
                        ),
                        _source_lineage_filter(),
                        _viral_lineage_filter(),
                    ),
                )
            }
        ),
        baseline.model_copy(
            update={
                "scope": FilteredScope(
                    scope_type="filtered",
                    filters=(
                        _assembly_filter(),
                        SourceLineageFilter(
                            filter_type="source_lineage",
                            snapshot_key=f"{HOST_SNAPSHOT}-other",
                            term_key=HOST_TERM,
                            role="assembly_source_taxonomy",
                            include_descendants=False,
                        ),
                        _viral_lineage_filter(),
                    ),
                )
            }
        ),
        baseline.model_copy(
            update={
                "scope": FilteredScope(
                    scope_type="filtered",
                    filters=(
                        _assembly_filter(),
                        SourceLineageFilter(
                            filter_type="source_lineage",
                            snapshot_key=HOST_SNAPSHOT,
                            term_key=f"{HOST_TERM}-other",
                            role="assembly_source_taxonomy",
                            include_descendants=True,
                        ),
                        _viral_lineage_filter(),
                    ),
                )
            }
        ),
        baseline.model_copy(
            update={
                "scope": FilteredScope(
                    scope_type="filtered",
                    filters=(
                        _assembly_filter(),
                        _source_lineage_filter(),
                        ViralLineageFilter(
                            filter_type="viral_lineage",
                            snapshot_key=f"{VIRAL_SNAPSHOT}-other",
                            term_key=f"{VIRAL_TERM}-other",
                            role="formal_viral_taxonomy",
                            include_descendants=True,
                        ),
                    ),
                )
            }
        ),
        baseline.model_copy(
            update={
                "scope": EntireReleaseScope(
                    scope_type="entire_release",
                    explicitly_requested=True,
                )
            }
        ),
    )
    baseline_hash = canonical_plan_sha256(baseline)

    assert all(canonical_plan_sha256(variant) != baseline_hash for variant in variants)


def test_metric_changes_aggregate_plan_hash() -> None:
    baseline = AggregatePlan(
        **_base_fields("Count records in this release."),
        intent="aggregate",
        scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
        metric_key="distinct_included_locus_count",
    )
    variant = baseline.model_copy(update={"metric_key": "distinct_contig_count"})

    assert canonical_plan_sha256(baseline) != canonical_plan_sha256(variant)


def test_planning_audit_requires_exact_mapped_unresolved_partition() -> None:
    mapped = ExtractedCondition(
        condition_id="c-intent",
        source_text="List loci",
        source_start=0,
        source_end=10,
        condition_kind="intent",
        mapped_target="intent",
    )
    unresolved = ExtractedCondition(
        condition_id="c-negation",
        source_text="except",
        source_start=11,
        source_end=17,
        condition_kind="negation",
        mapped_target=None,
    )
    audit = PlanningAudit(
        extracted_conditions=(mapped, unresolved),
        mapped_condition_ids=("c-intent",),
        unresolved_condition_ids=("c-negation",),
        unconsumed_semantic_spans=(
            SemanticSpan(source_text="except", source_start=11, source_end=17),
        ),
    )

    assert audit.mapped_condition_ids == ("c-intent",)
    assert audit.unresolved_condition_ids == ("c-negation",)

    with pytest.raises(ValidationError, match="must partition extracted conditions"):
        PlanningAudit(
            extracted_conditions=(mapped,),
            mapped_condition_ids=(),
            unresolved_condition_ids=(),
        )
    with pytest.raises(ValidationError, match="mapped conditions require mapped_target"):
        PlanningAudit(
            extracted_conditions=(unresolved,),
            mapped_condition_ids=("c-negation",),
            unresolved_condition_ids=(),
        )


def test_semantic_span_rejects_empty_or_reversed_interval() -> None:
    with pytest.raises(ValidationError):
        SemanticSpan(source_text="term", source_start=5, source_end=5)
    with pytest.raises(ValidationError):
        SemanticSpan(source_text="term", source_start=6, source_end=5)
