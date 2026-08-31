"""Controlled-English parsing, condition coverage, and planning tests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from eve_relation_rag.planning.parser import (
    ControlledEnglishPlanner,
    StructuredQueryRequest,
)
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    PageSpec,
    SourceLineageFilter,
    ViralLineageFilter,
)
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LocusResolverRecord,
)
from eve_relation_rag.retrieval.structured.results import ErrorResponse, PlanSuccess

RELEASE = "release:endoviho-rag:v0:20260827:002"
ASSEMBLY = "GCA_029931535.1"
LOCUS = f"locus:eve:v1:sha256:{'a' * 64}"
SOURCE_TERM = "ncbi-taxonomy:taxid:6544"
SOURCE_SNAPSHOT = "lineage-snapshot:ncbi-taxonomy:test"
FORMAL_TERM = "ictv:orthopolintovirales"
FORMAL_SNAPSHOT = "lineage-snapshot:ictv:test"
STUDY_TERM = "study:orthopolintovirales"
STUDY_SNAPSHOT = "lineage-snapshot:study:zhao-v4"
EXTENDED_TERM = "extended:asfa-like"
EXTENDED_SNAPSHOT = "lineage-snapshot:extended:asfa-like-v1"


def _resolver() -> CatalogReleaseResolver:
    return CatalogReleaseResolver(
        release_key=RELEASE,
        assemblies=(
            AssemblyResolverRecord(
                accession_version=ASSEMBLY,
                canonical_name="Margaritifera margaritifera",
            ),
        ),
        loci=(LocusResolverRecord(locus_key=LOCUS),),
        lineages=(
            LineageResolverRecord(
                entity_kind="source_lineage",
                term_key=SOURCE_TERM,
                canonical_name="Bivalvia",
                aliases=("Pelecypoda",),
                snapshot_key=SOURCE_SNAPSHOT,
                authority_namespace="ncbi-taxonomy",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="assembly_source_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key=FORMAL_TERM,
                canonical_name="Orthopolintovirales",
                snapshot_key=FORMAL_SNAPSHOT,
                authority_namespace="ictv",
                snapshot_version="test-v1",
                scheme_kind="formal_taxonomy",
                role="formal_viral_taxonomy",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key=STUDY_TERM,
                canonical_name="Orthopolintovirales",
                aliases=("polinton-like",),
                snapshot_key=STUDY_SNAPSHOT,
                authority_namespace="study-defined:zhao-v4",
                snapshot_version="v4",
                scheme_kind="study_defined",
                role="study_viral_lineage",
            ),
            LineageResolverRecord(
                entity_kind="viral_lineage",
                term_key=EXTENDED_TERM,
                canonical_name="Asfa-like",
                aliases=("Asfarviridae-like",),
                snapshot_key=EXTENDED_SNAPSHOT,
                authority_namespace="curated-extended-viral-lineage",
                snapshot_version="test-v1",
                scheme_kind="study_defined",
                role="extended_viral_lineage",
            ),
        ),
    )


def _plan(question: str, *, page: PageSpec | None = None) -> PlanSuccess | ErrorResponse:
    request = StructuredQueryRequest(release_key=RELEASE, question=question, page=page)
    return ControlledEnglishPlanner().plan(request, _resolver())


def _success(question: str, *, page: PageSpec | None = None) -> PlanSuccess:
    response = _plan(question, page=page)
    assert isinstance(response, PlanSuccess), response
    return response


def _error(question: str, expected_code: str, *, page: PageSpec | None = None) -> ErrorResponse:
    response = _plan(question, page=page)
    assert isinstance(response, ErrorResponse), response
    assert response.error.code == expected_code
    assert response.fact_retrieval_executed is False
    assert response.structured_result is None
    return response


def test_request_is_strict_frozen_question_first_transport() -> None:
    request = StructuredQueryRequest(
        release_key=RELEASE,
        question="List all loci in this release.",
    )

    assert request.request_schema_version == "structured-query-request-v1"
    assert request.page is None
    with pytest.raises(ValidationError):
        StructuredQueryRequest.model_validate(
            {**request.model_dump(), "sql": "SELECT * FROM eve_locus"}
        )
    with pytest.raises(ValidationError):
        StructuredQueryRequest(release_key=RELEASE, question="Question\nsecond line")
    with pytest.raises(ValidationError):
        StructuredQueryRequest(release_key=RELEASE, question="Question", page={"limit": "50"})


@pytest.mark.parametrize(
    ("question", "expected_type", "expected_intent"),
    [
        (f"Show assembly {ASSEMBLY}.", AssemblyDetailPlan, "assembly_detail"),
        (f"Show locus {LOCUS}.", LocusDetailPlan, "locus_detail"),
        ("List all loci in this release.", ListLociPlan, "list_loci"),
        ("List all assemblies in this release.", ListAssembliesPlan, "list_assemblies"),
        (
            "List source taxa represented in this release.",
            ListSourceTaxaPlan,
            "list_source_taxa",
        ),
        (
            "Count distinct included loci in this release.",
            AggregatePlan,
            "aggregate",
        ),
    ],
)
def test_all_six_intents_have_deterministic_controlled_english(
    question: str,
    expected_type: type[Any],
    expected_intent: str,
) -> None:
    response = _success(question)

    assert isinstance(response.query_plan, expected_type)
    assert response.query_plan.intent == expected_intent
    if expected_intent in {"assembly_detail", "locus_detail"}:
        assert isinstance(response.query_plan.scope, FilteredScope)
    else:
        assert isinstance(response.query_plan.scope, EntireReleaseScope)
    assert response.planning_audit.unresolved_condition_ids == ()
    assert response.planning_audit.unconsumed_semantic_spans == ()
    assert set(response.planning_audit.mapped_condition_ids) == {
        item.condition_id for item in response.planning_audit.extracted_conditions
    }


def test_list_page_defaults_and_caller_page_are_canonical() -> None:
    default = _success("List all loci in this release.")
    supplied = _success(
        "List all loci in this release.",
        page=PageSpec(limit=17, cursor="signed_cursor"),
    )

    assert isinstance(default.query_plan, ListLociPlan)
    assert isinstance(supplied.query_plan, ListLociPlan)
    assert default.query_plan.page == PageSpec()
    assert supplied.query_plan.page.limit == 17
    assert supplied.query_plan.page.cursor == "signed_cursor"


def test_combined_filters_resolve_independently_and_audit_every_condition() -> None:
    question = (
        "List loci assigned exactly to source lineage Pelecypoda and with study viral "
        "lineage Orthopolintovirales exactly."
    )
    response = _success(question)

    assert isinstance(response.query_plan, ListLociPlan)
    assert isinstance(response.query_plan.scope, FilteredScope)
    filters = response.query_plan.scope.filters
    assert tuple(item.filter_type for item in filters) == (
        "source_lineage",
        "viral_lineage",
    )
    source_filter, viral_filter = filters
    assert isinstance(source_filter, SourceLineageFilter)
    assert source_filter.term_key == SOURCE_TERM
    assert source_filter.include_descendants is False
    assert isinstance(viral_filter, ViralLineageFilter)
    assert viral_filter.term_key == STUDY_TERM
    assert viral_filter.include_descendants is False
    assert tuple(item.match_mode for item in response.resolved_entities) == (
        "exact_curated_alias",
        "exact_canonical_name",
    )

    audit = response.planning_audit
    kinds = tuple(item.condition_kind for item in audit.extracted_conditions)
    assert kinds.count("entity") == 2
    assert kinds.count("scope") == 2
    assert kinds.count("logical_operator") == 1
    assert audit.unresolved_condition_ids == ()
    assert audit.unconsumed_semantic_spans == ()
    for condition in audit.extracted_conditions:
        assert question[condition.source_start : condition.source_end] == condition.source_text


def test_assembly_filter_and_all_five_metrics_map_exactly() -> None:
    metrics = {
        "distinct included loci": "distinct_included_locus_count",
        "distinct contigs": "distinct_contig_count",
        "distinct assemblies": "distinct_assembly_count",
        "distinct source taxa": "distinct_source_taxon_count",
        "detection calls": "detection_call_count",
    }
    for phrase, metric_key in metrics.items():
        response = _success(f"Count {phrase} in assembly {ASSEMBLY}.")
        assert isinstance(response.query_plan, AggregatePlan)
        assert response.query_plan.metric_key == metric_key
        assert isinstance(response.query_plan.scope, FilteredScope)
        query_filter = response.query_plan.scope.filters[0]
        assert isinstance(query_filter, AssemblyFilter)
        assert query_filter.assembly_key == f"assembly:ncbi:{ASSEMBLY}"


def test_exact_snapshot_qualified_lineage_and_descendant_bit_are_preserved() -> None:
    question = (
        f"List loci assigned to source lineage term {SOURCE_TERM} in snapshot "
        f"{SOURCE_SNAPSHOT} including descendants."
    )
    response = _success(question)

    assert isinstance(response.query_plan, ListLociPlan)
    assert isinstance(response.query_plan.scope, FilteredScope)
    query_filter = response.query_plan.scope.filters[0]
    assert isinstance(query_filter, SourceLineageFilter)
    assert query_filter.snapshot_key == SOURCE_SNAPSHOT
    assert query_filter.term_key == SOURCE_TERM
    assert query_filter.include_descendants is True
    assert response.resolved_entities[0].match_mode == "exact_stable_key"


def test_formal_study_and_extended_role_words_select_separate_namespaces() -> None:
    formal = _success("List loci with formal viral lineage Orthopolintovirales exactly.")
    study = _success("List loci with study viral lineage Orthopolintovirales exactly.")
    extended = _success("List loci with extended viral lineage asfa-like including descendants.")

    assert formal.resolved_entities[0].stable_key == FORMAL_TERM
    assert study.resolved_entities[0].stable_key == STUDY_TERM
    assert extended.resolved_entities[0].stable_key == EXTENDED_TERM
    assert formal.resolved_entities[0].role == "formal_viral_taxonomy"
    assert study.resolved_entities[0].role == "study_viral_lineage"
    assert extended.resolved_entities[0].role == "extended_viral_lineage"
    assert isinstance(extended.query_plan.scope, FilteredScope)
    extended_filter = extended.query_plan.scope.filters[0]
    assert isinstance(extended_filter, ViralLineageFilter)
    assert extended_filter.include_descendants is True


def test_extended_snapshot_qualified_reference_preserves_exact_identity() -> None:
    response = _success(
        f"List loci with extended viral lineage term {EXTENDED_TERM} in snapshot "
        f"{EXTENDED_SNAPSHOT} exactly."
    )

    assert isinstance(response.query_plan.scope, FilteredScope)
    query_filter = response.query_plan.scope.filters[0]
    assert isinstance(query_filter, ViralLineageFilter)
    assert query_filter.role == "extended_viral_lineage"
    assert query_filter.snapshot_key == EXTENDED_SNAPSHOT
    assert query_filter.term_key == EXTENDED_TERM
    assert query_filter.include_descendants is False


@pytest.mark.parametrize(
    "question",
    [
        "List loci not assigned exactly to source lineage Bivalvia.",
        "List loci in assembly GCA_029931535.1 or in assembly GCF_000001405.40.",
        "Count distinct included loci between 2 and 10.",
        "Count distinct contigs more than 3 in this release.",
    ],
)
def test_negation_or_and_ranges_fail_closed_with_unconsumed_audit(question: str) -> None:
    response = _error(question, "unsupported_question")

    assert response.query_plan is None
    assert response.planning_audit is not None
    assert response.planning_audit.unresolved_condition_ids
    assert response.planning_audit.unconsumed_semantic_spans


def test_missing_full_scope_role_and_lineage_scope_have_stable_errors() -> None:
    _error("List loci in this release.", "full_release_scope_not_explicit")
    ambiguous = _error(
        "List loci with viral lineage Orthopolintovirales exactly.",
        "lineage_role_ambiguous",
    )
    assert "formal, study, or extended" in ambiguous.error.message
    _error(
        "List loci with study viral lineage Orthopolintovirales.",
        "lineage_scope_ambiguous",
    )


def test_duplicate_and_intent_incompatible_filters_are_never_partially_planned() -> None:
    duplicate = _error(
        f"List loci in assembly {ASSEMBLY} and in assembly {ASSEMBLY}.",
        "unsupported_question",
    )
    incompatible = _error(
        "List source taxa assigned exactly to source lineage Bivalvia.",
        "intent_filter_incompatible",
    )

    assert duplicate.query_plan is None
    assert incompatible.query_plan is None
    assert incompatible.planning_audit is not None
    assert incompatible.planning_audit.unresolved_condition_ids


def test_versionless_unknown_absent_and_snapshot_mismatch_errors_are_exact() -> None:
    versionless = _error(
        "List loci in assembly GCA_029931535.",
        "assembly_accession_version_required",
    )
    unresolved = _error(
        "List loci assigned exactly to source lineage Mollusca-ish.",
        "entity_unresolved",
    )
    absent = _error(
        f"Show locus locus:eve:v1:sha256:{'b' * 64}.",
        "entity_not_in_release",
    )
    mismatch = _error(
        f"List loci assigned exactly to source lineage term {SOURCE_TERM} in snapshot "
        "lineage-snapshot:ncbi-taxonomy:wrong.",
        "lineage_snapshot_mismatch",
    )

    assert versionless.error.suggestions[0].stable_key == f"assembly:ncbi:{ASSEMBLY}"
    assert unresolved.query_plan is None
    assert absent.error.suggestions == ()
    assert mismatch.error.suggestions == ()


@pytest.mark.parametrize(
    "reference",
    [
        f"term {'t' * 256} in snapshot snapshot:valid",
        f"term term:valid in snapshot {'s' * 256}",
    ],
)
def test_overlong_embedded_lineage_keys_return_stable_request_error(reference: str) -> None:
    response = _error(
        f"List loci assigned exactly to source lineage {reference}.",
        "request_schema_invalid",
    )

    assert response.planning_audit is not None
    assert response.planning_audit.unresolved_condition_ids
    assert response.fact_retrieval_executed is False


def test_multiple_mentions_are_all_resolved_even_when_one_fails() -> None:
    response = _error(
        "List loci assigned exactly to source lineage UnknownTaxon and with study viral "
        "lineage Orthopolintovirales exactly.",
        "entity_unresolved",
    )

    assert tuple(item.stable_key for item in response.resolved_entities) == (STUDY_TERM,)
    assert response.planning_audit is not None
    assert len(response.planning_audit.unresolved_condition_ids) == 1
    assert any(
        item.mapped_target == "scope.filter:viral_lineage.term"
        for item in response.planning_audit.extracted_conditions
    )


def test_pagination_is_rejected_for_detail_and_aggregate_intents() -> None:
    detail = _error(
        f"Show assembly {ASSEMBLY}.",
        "pagination_not_allowed",
        page=PageSpec(),
    )
    aggregate = _error(
        "Count distinct contigs in this release.",
        "pagination_not_allowed",
        page=PageSpec(),
    )

    assert detail.planning_audit is not None
    assert detail.planning_audit.unresolved_condition_ids == ()
    assert aggregate.planning_audit is not None
    assert aggregate.planning_audit.unresolved_condition_ids == ()


def test_cross_release_resolver_is_rejected_before_parsing_or_resolution() -> None:
    resolver = CatalogReleaseResolver(release_key="release:endoviho-rag:v0:20260828:001")
    request = StructuredQueryRequest(
        release_key=RELEASE,
        question=f"Show assembly {ASSEMBLY}.",
    )

    response = ControlledEnglishPlanner().plan(request, resolver)

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "release_dependencies_incomplete"
    assert response.planning_audit is None
    assert response.resolved_entities == ()


def test_unsupported_wording_never_calls_any_resolver_method() -> None:
    class ExplodingResolver:
        release_key = RELEASE

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"resolver method unexpectedly requested: {name}")

    request = StructuredQueryRequest(
        release_key=RELEASE,
        question="Please explore whatever data seem biologically interesting.",
    )

    response = ControlledEnglishPlanner().plan(request, ExplodingResolver())  # type: ignore[arg-type]

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "unsupported_question"
    assert response.fact_retrieval_executed is False
