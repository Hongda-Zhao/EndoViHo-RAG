"""Multi-query transport and provenance boundaries using synthetic fixture identities."""

import json
from types import SimpleNamespace

import pytest

from eve_relation_rag.experiments.rag_value_ablation.answer_validation import validate_answer_output
from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    project_exact_associations,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationEvidencePack,
    build_evidence_pack,
    model_visible_evidence,
)
from eve_relation_rag.experiments.rag_value_ablation.literature_adapter import (
    LiteratureAdapterError,
    LiteratureEvidenceAdapter,
)
from eve_relation_rag.experiments.rag_value_ablation.structured_evidence import (
    StructuredEvidenceGroup,
)
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    SystemPolicyError,
    build_system_definitions,
    validate_evidence_for_system,
)
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import canonical_plan_sha256
from eve_relation_rag.retrieval.hybrid.anchors import StructuredAnchorResolution
from eve_relation_rag.retrieval.structured.results import QuerySuccess
from tests.experiments.test_rag_value_association_projection import LOCUS, _bindings, _success
from tests.experiments.test_rag_value_complete_sets import QUESTION, RELEASE, page


def detail(index=1):
    source = _success()
    key = LOCUS if index == 1 else f"locus:eve:v1:sha256:{index:064x}"
    data = json.loads(source.model_dump_json().replace(LOCUS, key))
    data["query_plan"]["original_question"] = "Show locus " + key
    plan = source.query_plan.model_validate_json(json.dumps(data["query_plan"]))
    data["structured_result"]["plan_sha256"] = canonical_plan_sha256(plan)
    return QuerySuccess.model_validate_json(json.dumps(data))


def group(index=1, *, projected=False):
    source = detail(index)
    bindings = _bindings() if projected else None
    projections = () if bindings is None else (project_exact_associations(
        source, bindings=bindings, expected_binding_sha256=bindings.binding_sha256,
        expected_source_sha256=canonical_model_sha256(source),
    ),)
    return StructuredEvidenceGroup(
        request=StructuredQueryRequest(release_key=RELEASE,
                                       question=source.query_plan.original_question),
        details=(source,), projection_bindings=bindings, association_projections=projections,
    )


def pack(groups):
    return build_evidence_pack(
        question_id="development-multiquery", question_text="Describe the supplied synthetic loci.",
        structured_groups=groups, policy_sha256="a" * 64, tokenizer_key="tests-only",
        model_context_limit_tokens=32768, reserved_output_tokens=8192,
        input_token_count=100, context_token_count=50,
    )


def test_multiple_detail_queries_survive_serialization_and_only_s5_s6_admit_them():
    original = pack((group(1), group(2)))
    restored = EvaluationEvidencePack.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.pack_schema_version == "rag-value-evidence-pack-v2"
    visible = model_visible_evidence(restored)
    assert len(visible["structured_queries"]) == 2
    assert visible["structured_queries"][1]["controlled_question"] == group(2).request.question
    assert "approval" not in json.dumps(visible)
    for system in build_system_definitions(None):
        if system.system_key == "S5":
            validate_evidence_for_system(system, restored)
        elif system.system_key != "S6":
            with pytest.raises(SystemPolicyError):
                validate_evidence_for_system(system, restored)


def test_duplicate_groups_and_unbound_projections_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        pack((group(), group()))
    data = group(projected=True).model_dump(mode="json")
    data["projection_bindings"] = None
    with pytest.raises(ValueError, match="bindings"):
        StructuredEvidenceGroup.model_validate_json(json.dumps(data))


def test_projected_association_is_checked_against_exact_source_tuple():
    evidence = pack((group(projected=True),))
    assoc = evidence.structured_groups[0].association_projections[0].associations[0]
    answer = {"answer_text": "One source-qualified association is supplied.", "abstained": False,
              "claims": [{"claim_id": "C1", "text": "The source reports this association.",
                          "claim_type": "structured_fact"}],
              "structured_facts": {"exact_association_set": [assoc.model_dump(mode="json")]}}
    checked = validate_answer_output(json.dumps(answer), evidence)
    assert checked.report.structured_evidence_status == "passed"
    assert checked.report.semantic_support_status == "not_assessed"
    answer["structured_facts"]["exact_association_set"][0]["assembly_accession_version"] = (
        "GCA_888888888.1"
    )
    checked = validate_answer_output(json.dumps(answer), evidence)
    assert checked.report.structured_evidence_status == "failed"


def test_empty_complete_list_is_distinct_from_missing_or_partial_pages():
    request = StructuredQueryRequest(release_key=RELEASE, question=QUESTION)
    empty = StructuredEvidenceGroup(request=request, pages=(page(0, total=0),))
    assert empty.details == ()
    for pages in ((), (page(1, cursor="more"),), (page(0, total=0), page(0, total=0))):
        with pytest.raises(ValueError):
            StructuredEvidenceGroup(request=request, pages=pages)


def test_paginated_list_requires_each_exact_detail():
    request = StructuredQueryRequest(release_key=RELEASE, question=QUESTION)
    # These fixture page IDs differ from detail(1); missing or unrelated hydration must fail.
    for details in ((), (detail(),), (detail(2), detail())):
        with pytest.raises(ValueError):
            StructuredEvidenceGroup(request=request, pages=(page(1, cursor="next"), page(2)),
                                    details=details)
    first = int("1" * 64, 16)
    complete = StructuredEvidenceGroup(
        request=request, pages=(page(first, cursor="next"), page(first + 1)),
        details=(detail(), detail(first + 1)),
    )
    assert len(complete.details) == 2
    assert len(pack((complete,)).structured_groups[0].pages) == 2


def test_s5_resolves_persisted_anchors_before_any_literature_query():
    adapter = object.__new__(LiteratureEvidenceAdapter)
    adapter._bge = object()
    adapter.published = SimpleNamespace(capability=object())
    calls = []

    def resolve(success, capability):
        calls.append(("resolve", success.structured_result.data.locus.locus_key))
        return StructuredAnchorResolution(targets=(), anchors=(), unmatched_targets=(),
                                          diagnostics=("structured_anchor_unmatched",))

    def retrieve(question, *, anchors):
        calls.append(("retrieve", anchors))
        return (), ()

    adapter._anchor_resolver = SimpleNamespace(resolve=resolve)
    adapter._retrieve = retrieve
    keys, citations, resolutions = adapter.retrieve_structured("Describe evidence", (group(),))
    assert calls == [("resolve", LOCUS), ("retrieve", ())]
    assert not keys and not citations
    assert resolutions[0].diagnostics == ("structured_anchor_unmatched",)
    before = list(calls)
    with pytest.raises(LiteratureAdapterError):
        adapter.retrieve_structured("Run arbitrary SQL", (group(),))
    assert calls == before
