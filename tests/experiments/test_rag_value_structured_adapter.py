"""All biological entities and approvals are synthetic parser fixtures."""

from __future__ import annotations

import pytest

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    build_evaluation_question,
    build_question_manifest,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import revise_candidates
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    ApprovedEntityBindings,
    build_core53_question_scope,
)
from eve_relation_rag.experiments.rag_value_ablation.structured_adapter import (
    StructuredAdapterError,
    compile_core53_structured_requests,
)
from eve_relation_rag.planning.parser import ControlledEnglishPlanner
from eve_relation_rag.planning.resolver import (
    AssemblyResolverRecord,
    CatalogReleaseResolver,
    LineageResolverRecord,
    LocusResolverRecord,
)
from eve_relation_rag.retrieval.structured.results import PlanSuccess
from tests.experiments import test_rag_value_scoped_admission as fixtures

entities = fixtures.entities
RELEASE = "release:endoviho-rag:v0:20990101:001"


@pytest.fixture
def bundle(entities):
    bound = ApprovedEntityBindings.model_validate_json(
        fixtures._hashed(
            {
                **entities.model_dump(mode="json"),
                "dataset_release_key": RELEASE,
            }
        )
    )
    scope = build_core53_question_scope(fixtures.PACKAGE, bound)
    values = {b.entity_slot: b.question_value for b in bound.bindings}
    questions = []
    for row in revise_candidates(scope.candidates):
        text = row.question_text_template
        for slot, value in values.items():
            text = text.replace("{" + slot + "}", value)
        questions.append(
            build_evaluation_question(
                question_id=row.template_id,
                question_text=text,
                family=row.family,
                review_status="approved",
                approval=fixtures._approval(),
                gold=fixtures._gold(row.family, release=RELEASE),
            )
        )
    manifest = build_question_manifest(
        questions,
        dataset_release_key=RELEASE,
        dataset_manifest_sha256="d" * 64,
        corpus_release_key=fixtures.CORPUS,
        corpus_manifest_sha256="e" * 64,
    )
    lineages = []
    for b in bound.bindings:
        if b.selected_lineage_role is None:
            continue
        source = b.selected_lineage_role == "assembly_source_taxonomy"
        lineages.append(
            LineageResolverRecord(
                entity_kind="source_lineage" if source else "viral_lineage",
                term_key=b.selected_stable_key,
                canonical_name=b.selected_display_name,
                snapshot_key=b.selected_snapshot_key,
                authority_namespace="tests-only",
                snapshot_version="tests-only",
                role=b.selected_lineage_role,
                scheme_kind="formal_taxonomy" if source else "study_defined",
            )
        )
    resolver = CatalogReleaseResolver(
        release_key=RELEASE,
        assemblies=tuple(
            AssemblyResolverRecord(accession_version=b.question_value)
            for b in bound.bindings
            if b.required_entity_type == "assembly"
        ),
        loci=tuple(
            LocusResolverRecord(locus_key=b.selected_stable_key)
            for b in bound.bindings
            if b.required_entity_type == "eve_locus"
        ),
        lineages=tuple(lineages),
    )
    return scope, manifest, resolver


def test_all_25_compile_through_existing_parser_with_exact_keys(bundle):
    scope, manifest, resolver = bundle
    checked = 0
    for question in manifest.questions:
        if question.family not in {"structured", "hybrid"}:
            continue
        requests = compile_core53_structured_requests(scope, manifest, question.question_id)
        assert len(requests) == {"REL-S-04": 2, "RECORD-S-04": 3}.get(question.question_id, 1)
        for request in requests:
            result = ControlledEnglishPlanner().plan(request, resolver)
            assert isinstance(result, PlanSuccess), result
            assert result.query_plan.release_key == RELEASE
        checked += 1
    assert checked == 25


def test_no_literature_or_unknown_question_is_silently_treated_as_structured(bundle):
    scope, manifest, _ = bundle
    for question_id in ("UNSUP-01", "HOST-L-01", "made-up"):
        with pytest.raises(StructuredAdapterError):
            compile_core53_structured_requests(scope, manifest, question_id)


def test_detail_rehearsal_preserves_result_and_refusal_stops(monkeypatch):
    from types import SimpleNamespace

    from eve_relation_rag.experiments.rag_value_ablation import structured_adapter as adapter
    from eve_relation_rag.planning.parser import StructuredQueryRequest
    from tests.experiments.test_rag_value_association_projection import _success

    success = _success()
    request = StructuredQueryRequest(
        release_key=success.query_plan.release_key,
        question=success.query_plan.original_question,
    )
    monkeypatch.setattr(adapter, "compile_core53_structured_requests", lambda *args: (request,))
    calls = []

    def query(request):
        calls.append(request)
        return success

    manifest = SimpleNamespace(
        dataset_manifest_sha256=success.structured_result.release.manifest_sha256,
        corpus_release_key="corpus:tests-only",
        corpus_manifest_sha256="a" * 64,
    )
    result = adapter.execute_core53_structured_question(
        SimpleNamespace(query=query),
        None,
        manifest,
        "RECORD-S-02",
    )
    assert result["groups"][0]["details"] == (success,)
    assert result["generation_executed"] is False
    assert len(calls) == 1
    assert result["deterministic_answer"][0]["locus_count"] == 1
    assert result["deterministic_answer"][0]["complete_for_exact_subquery"] is True
    from tests.experiments.test_rag_value_association_projection import _bindings

    bindings = _bindings()
    with pytest.raises(StructuredAdapterError, match="approved identity"):
        adapter.execute_core53_structured_question(
            SimpleNamespace(query=query),
            None,
            manifest,
            "RECORD-S-02",
            projection_bindings=bindings,
            approved_projection_binding_sha256="f" * 64,
        )
    assert len(calls) == 1
    projected = adapter.execute_core53_structured_question(
        SimpleNamespace(query=query),
        None,
        manifest,
        "RECORD-S-02",
        projection_bindings=bindings,
        approved_projection_binding_sha256=bindings.binding_sha256,
    )
    assert len(calls) == 2
    group = projected["groups"][0]
    assert group["association_projections"][0].structured_result == success.structured_result
    assert group["displayed_associations"][0].mapping_key is None
    assert projected["deterministic_answer"][0]["association_projection_complete"] is True
    calls.clear()
    with pytest.raises(StructuredAdapterError, match="refused"):
        adapter.execute_core53_structured_question(
            SimpleNamespace(query=lambda request: calls.append(request)),
            None,
            manifest,
            "RECORD-S-02",
        )
    assert len(calls) == 1
