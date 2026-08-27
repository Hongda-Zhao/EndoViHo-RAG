"""Planning/contract oracle for the 31 Milestone 2 gold questions.

The synthetic evaluator freezes expected scientific sets but is not the production SQL
repository.  Production compiler/repository equality is a separate PostgreSQL acceptance matrix.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

import pytest

from eve_relation_rag.domain.keys import canonical_json
from eve_relation_rag.planning.parser import ControlledEnglishPlanner, StructuredQueryRequest
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
    LocusFilter,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
    canonical_plan_json,
)
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LocusResolverRecord,
)
from eve_relation_rag.retrieval.structured.results import ErrorResponse, PlanSuccess
from tests.benchmark.gold_cases import (
    ASSEMBLIES,
    CAPABILITY_KIND,
    CASES,
    CATALOG_KEY,
    LINEAGES,
    LOCI,
    RELEASE_KEY,
    GoldCase,
    SyntheticLocus,
)


@dataclass(frozen=True, slots=True)
class SyntheticPublishedCapabilityForTests:
    """Marker capability for the isolated benchmark; it is never a public release ref."""

    capability_kind: Literal["tests_only_synthetic_published_capability"] = CAPABILITY_KIND
    release_key: str = RELEASE_KEY
    catalog_key: str = CATALOG_KEY
    real_public_release: Literal[False] = False


CAPABILITY = SyntheticPublishedCapabilityForTests()


def _resolver() -> CatalogReleaseResolver:
    return CatalogReleaseResolver(
        release_key=RELEASE_KEY,
        assemblies=tuple(
            AssemblyResolverRecord(
                accession_version=accession,
                canonical_name=name,
            )
            for accession, name in ASSEMBLIES.items()
        ),
        loci=tuple(
            LocusResolverRecord(
                locus_key=locus.locus_key,
                canonical_name=f"Synthetic locus {locus.locus_key[-1].upper()}",
            )
            for locus in LOCI
        ),
        lineages=tuple(LineageResolverRecord.model_validate(record) for record in LINEAGES),
    )


def _http_status_for(error_code: str) -> int:
    if error_code in {"entity_unresolved", "entity_not_in_release", "release_not_found"}:
        return 404
    if error_code in {
        "entity_ambiguous",
        "release_not_published",
        "release_dependencies_incomplete",
    }:
        return 409
    if error_code in {"cursor_invalid", "cursor_plan_mismatch"}:
        return 400
    return 422


def _matching_loci(plan: StructuredPlan) -> tuple[SyntheticLocus, ...]:
    matches = list(LOCI)
    if isinstance(plan.scope, EntireReleaseScope):
        return tuple(matches)
    assert isinstance(plan.scope, FilteredScope)
    for query_filter in plan.scope.filters:
        if isinstance(query_filter, AssemblyFilter):
            accession = query_filter.assembly_key.removeprefix("assembly:ncbi:")
            matches = [item for item in matches if item.assembly_accession == accession]
        elif isinstance(query_filter, LocusFilter):
            matches = [item for item in matches if item.locus_key == query_filter.locus_key]
        elif isinstance(query_filter, SourceLineageFilter):
            if query_filter.include_descendants:
                matches = [
                    item for item in matches if query_filter.term_key in item.source_ancestors
                ]
            else:
                matches = [item for item in matches if query_filter.term_key == item.source_term]
        elif isinstance(query_filter, ViralLineageFilter):
            target = (query_filter.role, query_filter.term_key)
            # The synthetic fixture has no deeper viral descendants; exact and descendant
            # scopes therefore select the same explicitly frozen leaf memberships.
            matches = [item for item in matches if target in item.viral_terms]
    return tuple(sorted(matches, key=lambda item: item.locus_key))


def _constraint_for_filter(query_filter: object) -> str:
    if isinstance(query_filter, AssemblyFilter):
        return f"assembly={query_filter.assembly_key}"
    if isinstance(query_filter, LocusFilter):
        return f"locus={query_filter.locus_key}"
    assert isinstance(query_filter, (SourceLineageFilter, ViralLineageFilter))
    return (
        f"{query_filter.filter_type}={query_filter.snapshot_key}:{query_filter.term_key}:"
        f"{query_filter.role}:descendants={str(query_filter.include_descendants).lower()}"
    )


def _limitations(plan: StructuredPlan) -> tuple[str, ...]:
    codes: set[str] = set()
    if isinstance(plan, AssemblyDetailPlan):
        codes.add("assembly_source_taxon_is_not_ancient_host")
    elif isinstance(plan, LocusDetailPlan):
        codes.update(
            {
                "assembly_local_locus_is_not_independent_integration_event",
                "assembly_source_taxon_is_not_ancient_host",
                "coordinates_are_zero_based_half_open",
                "detection_calls_are_not_loci",
                "source_confidence_is_not_release_validation",
            }
        )
    elif isinstance(plan, ListLociPlan):
        codes.update(
            {
                "assembly_local_locus_is_not_independent_integration_event",
                "assembly_source_taxon_is_not_ancient_host",
                "coordinates_are_zero_based_half_open",
            }
        )
    elif isinstance(plan, (ListAssembliesPlan, ListSourceTaxaPlan)):
        codes.add("assembly_source_taxon_is_not_ancient_host")
    elif isinstance(plan, AggregatePlan):
        if plan.metric_key in {"distinct_included_locus_count", "distinct_contig_count"}:
            codes.add("assembly_local_locus_is_not_independent_integration_event")
        elif plan.metric_key == "distinct_source_taxon_count":
            codes.add("assembly_source_taxon_is_not_ancient_host")
        elif plan.metric_key == "detection_call_count":
            codes.add("detection_calls_are_not_loci")
    return tuple(sorted(codes))


def _exact_numbers(plan: StructuredPlan, matches: tuple[SyntheticLocus, ...]) -> dict[str, int]:
    if isinstance(plan, AssemblyDetailPlan):
        return {
            "included_locus_count": len(matches),
            "distinct_contig_count": len(
                {(item.assembly_accession, item.contig_accession) for item in matches}
            ),
            "detection_call_count": sum(len(item.source_record_keys) for item in matches),
        }
    if isinstance(plan, LocusDetailPlan):
        assert len(matches) == 1
        locus = matches[0]
        return {
            "interval_length": locus.end0 - locus.start0,
            "detection_call_count": len(locus.source_record_keys),
        }
    if isinstance(plan, (ListLociPlan, ListAssembliesPlan, ListSourceTaxaPlan)):
        if isinstance(plan, ListLociPlan):
            total = len(matches)
        elif isinstance(plan, ListAssembliesPlan):
            total = len({item.assembly_accession for item in matches})
        else:
            total = len({item.source_term for item in matches})
        return {"total_count": total}
    assert isinstance(plan, AggregatePlan)
    if plan.metric_key == "distinct_included_locus_count":
        value = len({item.locus_key for item in matches})
    elif plan.metric_key == "distinct_contig_count":
        value = len({(item.assembly_accession, item.contig_accession) for item in matches})
    elif plan.metric_key == "distinct_assembly_count":
        value = len({item.assembly_accession for item in matches})
    elif plan.metric_key == "distinct_source_taxon_count":
        value = len({item.source_term for item in matches})
    else:
        value = sum(len(item.source_record_keys) for item in matches)
    return {"metric_value": value}


def _item_keys(plan: StructuredPlan, matches: tuple[SyntheticLocus, ...]) -> tuple[str, ...]:
    if isinstance(plan, AssemblyDetailPlan):
        assert isinstance(plan.scope, FilteredScope)
        query_filter = plan.scope.filters[0]
        assert isinstance(query_filter, AssemblyFilter)
        return (query_filter.assembly_key,)
    if isinstance(plan, LocusDetailPlan):
        return tuple(item.locus_key for item in matches)
    if isinstance(plan, ListLociPlan):
        return tuple(sorted(item.locus_key for item in matches))
    if isinstance(plan, ListAssembliesPlan):
        return tuple(sorted({f"assembly:ncbi:{item.assembly_accession}" for item in matches}))
    if isinstance(plan, ListSourceTaxaPlan):
        return tuple(sorted({item.source_term for item in matches}))
    return ()


def _execute_synthetic(plan: StructuredPlan) -> dict[str, Any]:
    """Evaluate a plan against facts, not against any case's expected result fields."""

    matches = _matching_loci(plan)
    constraints = [
        "public_membership:release_assertion",
        "public_membership:release_locus",
        f"release_key={plan.release_key}",
    ]
    if isinstance(plan.scope, FilteredScope):
        constraints.extend(_constraint_for_filter(item) for item in plan.scope.filters)
    source_records = tuple(sorted(record for item in matches for record in item.source_record_keys))
    return {
        "exact_result_keys": {
            "items": _item_keys(plan, matches),
            "matched_loci": tuple(sorted(item.locus_key for item in matches)),
        },
        "exact_numbers": _exact_numbers(plan, matches),
        "limitations": _limitations(plan),
        "provenance": {
            "capability_kind": CAPABILITY.capability_kind,
            "catalog_key": CAPABILITY.catalog_key,
            "release_key": CAPABILITY.release_key,
            "real_public_release": CAPABILITY.real_public_release,
            "source_record_keys": source_records,
        },
        "applied_constraints": tuple(sorted(constraints)),
    }


def test_gold_set_has_every_contract_category_and_is_tests_only() -> None:
    counts = Counter(case.category for case in CASES)

    assert len(CASES) == 31
    assert counts >= Counter(
        {
            "assembly_detail": 4,
            "locus_detail": 4,
            "source_lineage": 4,
            "viral_lineage": 4,
            "combined": 5,
            "aggregate": 4,
            "invalid": 5,
        }
    )
    assert len({case.case_id for case in CASES}) == len(CASES)
    assert CAPABILITY.real_public_release is False
    assert CAPABILITY.capability_kind.startswith("tests_only_")
    assert all(case.provenance["real_public_release"] is False for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_gold_question_planning_is_exact(case: GoldCase) -> None:
    request = StructuredQueryRequest.model_validate(case.request)
    response = ControlledEnglishPlanner().plan(request, _resolver())

    if case.expected_response_kind == "error":
        assert isinstance(response, ErrorResponse)
        assert response.error.code == case.expected_error_code
        assert response.resolved_entities == case.expected_resolved_entities
        assert response.query_plan is None
        assert response.fact_retrieval_executed is False
        assert _http_status_for(response.error.code) == case.expected_http_status
        return

    assert isinstance(response, PlanSuccess)
    assert response.query_plan.intent == case.expected_intent
    assert response.response_kind == case.expected_response_kind
    assert response.fact_retrieval_executed is False
    assert (
        tuple(item.model_dump(mode="json") for item in response.resolved_entities)
        == case.expected_resolved_entities
    )
    assert case.expected_canonical_plan is not None
    assert canonical_plan_json(response.query_plan) == canonical_json(case.expected_canonical_plan)
    assert case.expected_http_status == 200


@pytest.mark.parametrize(
    "case",
    tuple(case for case in CASES if case.expected_response_kind == "plan_success"),
    ids=lambda case: case.case_id,
)
def test_gold_synthetic_fact_oracle_is_exact_not_subset_acceptance(case: GoldCase) -> None:
    request = StructuredQueryRequest.model_validate(case.request)
    response = ControlledEnglishPlanner().plan(request, _resolver())
    assert isinstance(response, PlanSuccess)

    actual = _execute_synthetic(response.query_plan)

    assert actual["exact_result_keys"] == case.exact_result_keys
    assert actual["exact_numbers"] == case.exact_numbers
    assert actual["limitations"] == case.limitations
    assert actual["provenance"] == case.provenance
    assert actual["applied_constraints"] == case.applied_constraints
