"""Synthetic complete-set boundaries, including no query broadening after refusal."""

from __future__ import annotations

import pytest

from eve_relation_rag.experiments.rag_value_ablation.complete_sets import (
    CompleteSetError,
    collect_complete_loci,
)
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import (
    EntireReleaseScope,
    ListLociPlan,
    PageSpec,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.results import (
    Limitation,
    LocusPageData,
    PageInfo,
    QuerySuccess,
)
from tests.experiments.test_rag_value_association_projection import RELEASE, _success

QUESTION = "List all loci in the entire release."


def page(index, *, total=2, cursor=None):
    source = _success(calls=())
    plan = ListLociPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        original_question=QUESTION,
        intent="list_loci",
        scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
        page=PageSpec(limit=1),
    )
    locus = source.structured_result.data.locus.model_copy(
        update={"locus_key": f"locus:eve:v1:sha256:{index:064x}"},
    )
    data = LocusPageData(
        items=(locus,) if total else (),
        page=PageInfo(
            limit=1,
            returned_count=1 if total else 0,
            total_count=total,
            next_cursor=cursor,
            sort_key="locus_key",
        ),
    )
    value = source.model_copy(
        update={
            "query_plan": plan,
            "resolved_entities": (),
            "structured_result": source.structured_result.model_copy(
                update={
                    "plan_sha256": canonical_plan_sha256(plan),
                    "data": data,
                        "limitations": source.structured_result.limitations if total else (
                            Limitation(code="zero_matches_do_not_establish_biological_absence",
                                       message="Synthetic empty result is not biological absence."),
                        ),
                }
            ),
        }
    )
    return QuerySuccess.model_validate_json(value.model_dump_json())


def collect(responses, *, max_pages=1000):
    calls = []
    iterator = iter(responses)

    def query(request):
        calls.append(request)
        return next(iterator)

    result = collect_complete_loci(
        StructuredQueryRequest(release_key=RELEASE, question=QUESTION),
        query,
        max_pages=max_pages,
    )
    return result, calls


def test_all_pages_keep_exact_question_release_and_cursor():
    result, calls = collect([page(1, cursor="next"), page(2)])
    assert len(result.items) == 2
    assert calls[1].page.cursor == "next"
    assert all(call.question == QUESTION and call.release_key == RELEASE for call in calls)
    empty, _ = collect([page(0, total=0)])
    assert empty.items == ()


@pytest.mark.parametrize(
    "responses",
    [
        [page(1, cursor="next"), page(1)],
        [page(1, cursor="next"), page(2, total=3)],
        [page(1)],
        [page(1, total=3, cursor="cycle"), page(2, total=3, cursor="cycle")],
    ],
)
def test_partial_drift_overlap_and_cycles_are_errors(responses):
    with pytest.raises(CompleteSetError):
        collect(responses)


def test_page_budget_and_refusal_do_not_trigger_fallback():
    with pytest.raises(CompleteSetError, match="budget"):
        collect([page(1, cursor="next")], max_pages=1)
    calls = []
    with pytest.raises(CompleteSetError, match="refused"):
        collect_complete_loci(
            StructuredQueryRequest(release_key=RELEASE, question=QUESTION),
            lambda request: calls.append(request),
        )
    assert len(calls) == 1


def test_shared_scope_refusal_has_zero_downstream_calls():
    calls = []
    with pytest.raises(CompleteSetError, match="scope refused"):
        collect_complete_loci(
            StructuredQueryRequest(release_key=RELEASE, question="Use external knowledge."),
            lambda request: calls.append(request),
        )
    assert calls == []
