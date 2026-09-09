"""Synthetic-only verification of the experiment's offline association adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.association_projection import (
    AssociationProjectionBindings,
    AssociationProjectionError,
    ExactAssociationProjection,
    SourceAssertionProjectionBinding,
    build_association_projection_bindings,
    project_exact_associations,
)
from eve_relation_rag.hybrid.contracts import canonical_model_sha256
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    LocusDetailPlan,
    LocusFilter,
    PlanningAudit,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    CallDetail,
    EvidenceDetail,
    ExactPlacement,
    Limitation,
    LineageRef,
    LocusDetailData,
    LocusSummary,
    PublicAssertionDetail,
    PublishedReleaseRef,
    QuerySuccess,
    ResolvedEntity,
    StructuredResult,
    ValidationCandidateReleaseRef,
)

RELEASE = "release:endoviho-rag:v0:20990101:001"
LOCUS = f"locus:eve:v1:sha256:{'1' * 64}"
ASSEMBLY = "GCA_999999999.1"
ARTIFACT_SHA = "a" * 64
RELEASE_SHA = "b" * 64
TEST_ONLY = "Synthetic software fixture; not a scientific or human Gold annotation."


def _source_lineage() -> LineageRef:
    return LineageRef(
        term_key="lineage-term:tests-only:source-species",
        canonical_name="Synthetic assembly-source species",
        snapshot_key="lineage-snapshot:tests-only:source-v1",
        snapshot_version="synthetic-source-v1",
        authority_namespace="tests-only:source",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )


def _viral_lineage() -> LineageRef:
    return LineageRef(
        term_key="lineage-term:tests-only:viral-affinity",
        canonical_name="Synthetic viral affinity",
        snapshot_key="lineage-snapshot:tests-only:viral-v1",
        snapshot_version="synthetic-viral-v1",
        authority_namespace="tests-only:viral",
        scheme_kind="study_defined",
        role="study_viral_lineage",
    )


def _call(*, index: int = 1) -> CallDetail:
    return CallDetail(
        call_key=f"detection-call:tests-only:{index}",
        source_method_key="method:tests-only:source-call",
        process_run_key="process-run:tests-only:v1",
        source_record_key=f"source-record:tests-only:{index}",
        artifact_key="source-artifact:tests-only:v1",
        artifact_sha256=ARTIFACT_SHA,
        worksheet="Synthetic",
        row_number=index,
    )


def _assertion(kind: str = "viral_major_taxon", *, index: int = 1) -> PublicAssertionDetail:
    values = {
        "viral_major_taxon": "Synthetic source label",
        "hcvr": "Yes",
        "vr_type": "Integration",
    }
    return PublicAssertionDetail(
        assertion_key=f"assertion:tests-only:{index}:{kind}",
        assertion_type=kind,  # type: ignore[arg-type]
        predicate_key=f"source:{kind}",
        asserted_value=values[kind],
        source_label="Yes" if kind == "hcvr" else None,
        source_confidence="source_high" if kind == "hcvr" else None,
        lineage=_viral_lineage() if kind == "viral_major_taxon" else None,
        method_definition_key="method:tests-only:projection-source",
        method_version="synthetic-method-v1",
        process_run_key="process-run:tests-only:v1",
        supporting_evidence=EvidenceDetail(
            evidence_key=f"evidence:tests-only:{index}",
            evidence_type="source_row",
            evidence_sha256="c" * 64,
            source_locator={
                "excel_row": index,
                "label": f"Synthetic!{index}",
                "worksheet": "Synthetic",
            },
            artifact_key="source-artifact:tests-only:v1",
            artifact_sha256=ARTIFACT_SHA,
            source_uri="https://example.invalid/tests-only",
            verified_license_key="tests-only:synthetic",
        ),
    )


def _success(
    *,
    calls: tuple[CallDetail, ...] | None = None,
    assertions: tuple[PublicAssertionDetail, ...] | None = None,
) -> QuerySuccess:
    assertions = assertions if assertions is not None else (_assertion(),)
    calls = calls if calls is not None else (_call(),)
    plan = LocusDetailPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        original_question=f"Show locus {LOCUS}.",
        intent="locus_detail",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(LocusFilter(filter_type="locus", locus_key=LOCUS),),
        ),
    )
    lineage_by_key = {
        (item.lineage.snapshot_key, item.lineage.term_key, item.lineage.role): item.lineage
        for item in assertions
        if item.lineage is not None
    }
    limitation_codes = [
        "assembly_local_locus_is_not_independent_integration_event",
        "assembly_source_taxon_is_not_ancient_host",
        "coordinates_are_zero_based_half_open",
    ]
    if calls:
        limitation_codes.append("detection_calls_are_not_loci")
    if any(item.assertion_type == "hcvr" for item in assertions):
        limitation_codes.append("source_confidence_is_not_release_validation")
    return QuerySuccess(
        query_plan=plan,
        planning_audit=PlanningAudit(
            extracted_conditions=(
                ExtractedCondition(
                    condition_id="c1",
                    source_text="Show",
                    source_start=0,
                    source_end=4,
                    condition_kind="intent",
                    mapped_target="intent",
                ),
            ),
            mapped_condition_ids=("c1",),
        ),
        resolved_entities=(
            ResolvedEntity(
                original_input=LOCUS,
                entity_kind="locus",
                match_mode="exact_stable_key",
                stable_key=LOCUS,
            ),
        ),
        structured_result=StructuredResult(
            plan_sha256=canonical_plan_sha256(plan),
            release=PublishedReleaseRef(
                dataset_key="dataset:endoviho-rag",
                release_key=RELEASE,
                schema_version="tests-only-v1",
                manifest_sha256=RELEASE_SHA,
                published_at=datetime(2099, 1, 1, tzinfo=UTC),
            ),
            data=LocusDetailData(
                locus=LocusSummary(
                    locus_key=LOCUS,
                    assembly_key=f"assembly:ncbi:{ASSEMBLY}",
                    assembly_accession_version=ASSEMBLY,
                    source_organism_name="Synthetic assembly-source species",
                    source_taxon=_source_lineage(),
                    placement=ExactPlacement(
                        sequence_key="sequence:insdc:TEST000001.1",
                        sequence_accession_version="TEST000001.1",
                        start0=12,
                        end0=34,
                        strand="unknown",
                    ),
                    viral_lineages=tuple(lineage_by_key[key] for key in sorted(lineage_by_key)),
                ),
                calls=tuple(sorted(calls, key=lambda item: item.call_key)),
                public_assertions=tuple(sorted(assertions, key=lambda item: item.assertion_key)),
            ),
            limitations=tuple(
                Limitation(code=code, message=TEST_ONLY)  # type: ignore[arg-type]
                for code in sorted(limitation_codes)
            ),
        ),
    )


def _bindings() -> AssociationProjectionBindings:
    release = _success().structured_result.release
    assert isinstance(release, PublishedReleaseRef)
    return build_association_projection_bindings(
        release=release,
        permitted_lineages=(_viral_lineage(), _source_lineage()),
        permitted_assertions=tuple(
            SourceAssertionProjectionBinding(
                assertion_type=kind,  # type: ignore[arg-type]
                predicate_key=f"source:{kind}",
                method_definition_key="method:tests-only:projection-source",
                method_version="synthetic-method-v1",
                artifact_key="source-artifact:tests-only:v1",
                artifact_sha256=ARTIFACT_SHA,
                evidence_type="source_row",
                verified_license_key="tests-only:synthetic",
                locator_policy="data-s1-excel-row-v1",
            )
            for kind in ("hcvr", "viral_major_taxon", "vr_type")
        ),
    )


def _project(
    success: QuerySuccess | None = None,
    bindings: AssociationProjectionBindings | None = None,
) -> ExactAssociationProjection:
    success = success if success is not None else _success()
    bindings = bindings if bindings is not None else _bindings()
    return project_exact_associations(
        success,
        bindings=bindings,
        expected_binding_sha256=bindings.binding_sha256,
        expected_source_sha256=canonical_model_sha256(success),
    )


def test_projection_is_deterministic_and_preserves_the_immutable_structured_result() -> None:
    source = _success()
    before = source.model_dump_json()
    first = _project(source)
    second = _project(source)

    assert first == second
    assert first.structured_result == source.structured_result
    assert source.model_dump_json() == before
    assert first.structured_result.data.kind == "locus_detail"
    assert first.structured_result.data.locus.placement.start0 == 12
    assert first.structured_result.data.locus.placement.sequence_accession_version == "TEST000001.1"
    assert first.scope == "one_complete_locus_detail"
    assert first.complete_for_release is False
    assert first.literature_alignment_performed is False
    assert first.associations[0].evidence_source.source_record_keys == (
        "source-record:tests-only:1",
    )
    assert first.associations[0].viral_lineage_affinity.include_descendants is False
    assert first.associations[0].viral_lineage_affinity.role == "study_viral_lineage"
    assert first.associations[0].viral_lineage_affinity.canonical_name == "Synthetic viral affinity"
    assert first.associations[0].source_annotations is not None
    assert first.associations[0].source_annotations.viral_major_taxon == "Synthetic source label"
    assert ExactAssociationProjection.model_validate_json(first.model_dump_json()) == first


def test_source_labels_are_not_reclassified_or_used_as_synonyms() -> None:
    projected = _project(
        _success(assertions=(_assertion(), _assertion("hcvr"), _assertion("vr_type")))
    )
    annotations = projected.associations[0].source_annotations
    assert annotations is not None
    assert annotations.hcvr == "Yes"
    assert annotations.vr_type == "Integration"
    assert "relation_class" not in projected.model_dump_json()
    assert "Transferred gene" not in projected.model_dump_json()
    assert "Integrated virus" not in projected.model_dump_json()


def test_associations_from_different_source_records_are_not_merged() -> None:
    source = _success(
        calls=(_call(), _call(index=2)),
        assertions=(_assertion(), _assertion("hcvr"), _assertion(index=2)),
    )
    projected = _project(source)
    by_source = {item.evidence_source.source_record_keys: item for item in projected.associations}
    assert len(by_source) == 2
    first = by_source[("source-record:tests-only:1",)].source_annotations
    second = by_source[("source-record:tests-only:2",)].source_annotations
    assert first is not None and first.hcvr == "Yes"
    assert second is not None and second.hcvr is None


def test_identically_named_study_and_formal_lineages_preserve_their_distinct_roles() -> None:
    formal = _viral_lineage().model_copy(
        update={
            "term_key": "lineage-term:tests-only:formal",
            "snapshot_key": "lineage-snapshot:tests-only:formal-v1",
            "snapshot_version": "synthetic-formal-v1",
            "authority_namespace": "tests-only:formal",
            "role": "formal_viral_taxonomy",
            "scheme_kind": "formal_taxonomy",
        }
    )
    second_assertion = _assertion().model_copy(
        update={"assertion_key": "assertion:tests-only:1:formal", "lineage": formal}
    )
    original = _bindings()
    bindings = build_association_projection_bindings(
        release=original.release,
        permitted_lineages=(*original.permitted_lineages, formal),
        permitted_assertions=original.permitted_assertions,
    )
    projected = _project(_success(assertions=(_assertion(), second_assertion)), bindings)
    assert len(projected.associations) == 2
    assert {item.viral_lineage_affinity.role for item in projected.associations} == {
        "study_viral_lineage",
        "formal_viral_taxonomy",
    }
    assert len({item.viral_lineage_affinity.snapshot_key for item in projected.associations}) == 2
    assert all(
        item.viral_lineage_affinity.canonical_name == "Synthetic viral affinity"
        for item in projected.associations
    )


@pytest.mark.parametrize("target", ["source", "bindings"])
def test_independently_expected_input_checksums_are_required(target: str) -> None:
    source = _success()
    bindings = _bindings()
    with pytest.raises(AssociationProjectionError, match="expected checksum"):
        project_exact_associations(
            source,
            bindings=bindings,
            expected_binding_sha256="0" * 64 if target == "bindings" else bindings.binding_sha256,
            expected_source_sha256="0" * 64
            if target == "source"
            else canonical_model_sha256(source),
        )


@pytest.mark.parametrize(
    "field,value", [("snapshot_version", "different"), ("canonical_name", "other")]
)
def test_lineage_full_identity_must_match_the_frozen_allowlist(field: str, value: str) -> None:
    changed = _viral_lineage().model_copy(update={field: value})
    source = _success(assertions=(_assertion().model_copy(update={"lineage": changed}),))
    with pytest.raises(AssociationProjectionError, match="lineage identity"):
        _project(source)


@pytest.mark.parametrize("field,value", [("role", None), ("snapshot_version", None)])
def test_constructed_lineages_cannot_skip_role_or_version_validation(
    field: str, value: Any
) -> None:
    changed = _viral_lineage().model_copy(update={field: value})
    source = _success()
    detail = source.structured_result.data
    assert isinstance(detail, LocusDetailData)
    forged_detail = detail.model_copy(
        update={
            "locus": detail.locus.model_copy(update={"viral_lineages": (changed,)}),
            "public_assertions": (_assertion().model_copy(update={"lineage": changed}),),
        }
    )
    source = source.model_copy(
        update={
            "structured_result": source.structured_result.model_copy(update={"data": forged_detail})
        }
    )
    with pytest.raises(AssociationProjectionError, match="revalidation"):
        _project(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_sha256", "0" * 64),
        ("schema_version", "different"),
        ("published_at", datetime(2099, 1, 2, tzinfo=UTC)),
    ],
)
def test_complete_release_reference_is_pinned(field: str, value: Any) -> None:
    source = _success()
    result = source.structured_result
    source = source.model_copy(
        update={
            "structured_result": result.model_copy(
                update={"release": result.release.model_copy(update={field: value})}
            )
        }
    )
    with pytest.raises(AssociationProjectionError, match="pinned projection release"):
        _project(source)


def test_cross_release_plan_and_result_mismatch_is_rejected_before_projection() -> None:
    source = _success()
    result = source.structured_result
    source = source.model_copy(
        update={
            "structured_result": result.model_copy(
                update={
                    "release": result.release.model_copy(
                        update={"release_key": "release:endoviho-rag:v0:20990102:001"}
                    )
                }
            )
        }
    )
    with pytest.raises(AssociationProjectionError, match="revalidation"):
        _project(source)


def test_validation_candidate_is_not_promoted_to_published_evidence() -> None:
    source = _success()
    candidate = ValidationCandidateReleaseRef(
        dataset_key="dataset:endoviho-rag",
        release_key=RELEASE,
        schema_version="tests-only-v1",
        manifest_sha256=RELEASE_SHA,
        candidate_created_at=datetime(2099, 1, 1, tzinfo=UTC),
        candidate_validation_input_sha256="1" * 64,
        candidate_capability_sha256="2" * 64,
    )
    source = source.model_copy(
        update={
            "structured_result": source.structured_result.model_copy(update={"release": candidate})
        }
    )
    with pytest.raises(AssociationProjectionError, match="published release"):
        _project(source)


def test_aggregate_is_not_silently_expanded_into_a_broad_locus_query() -> None:
    source = _success()
    plan = AggregatePlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        original_question="How many included loci are in the entire release?",
        intent="aggregate",
        scope=EntireReleaseScope(scope_type="entire_release", explicitly_requested=True),
        metric_key="distinct_included_locus_count",
    )
    result = StructuredResult(
        plan_sha256=canonical_plan_sha256(plan),
        release=source.structured_result.release,
        data=AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        ),
        limitations=(
            Limitation(
                code="assembly_local_locus_is_not_independent_integration_event", message=TEST_ONLY
            ),
        ),
    )
    source = QuerySuccess(
        query_plan=plan, planning_audit=source.planning_audit, structured_result=result
    )
    with pytest.raises(AssociationProjectionError, match="only complete locus_detail"):
        _project(source)


@pytest.mark.parametrize("field", ["predicate_key", "method_definition_key", "method_version"])
def test_unapproved_assertion_semantics_or_method_is_rejected(field: str) -> None:
    source = _success(assertions=(_assertion().model_copy(update={field: "unapproved"}),))
    with pytest.raises(AssociationProjectionError, match="unbound"):
        _project(source)


@pytest.mark.parametrize(
    "field", ["artifact_key", "artifact_sha256", "evidence_type", "verified_license_key"]
)
def test_unapproved_evidence_artifact_kind_or_license_is_rejected(field: str) -> None:
    assertion = _assertion()
    value = "0" * 64 if field == "artifact_sha256" else "unapproved"
    changed = assertion.model_copy(
        update={
            "supporting_evidence": assertion.supporting_evidence.model_copy(update={field: value})
        }
    )
    with pytest.raises(AssociationProjectionError, match="unbound"):
        _project(_success(assertions=(changed,)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_key", "other"),
        ("artifact_sha256", "0" * 64),
        ("worksheet", "Other"),
        ("row_number", 2),
        ("process_run_key", "other"),
    ],
)
def test_source_call_is_joined_on_all_exact_provenance_fields(field: str, value: Any) -> None:
    with pytest.raises(AssociationProjectionError, match="one exact source record"):
        _project(_success(calls=(_call().model_copy(update={field: value}),)))


def test_missing_or_ambiguous_source_call_fails_closed() -> None:
    with pytest.raises(AssociationProjectionError, match="one exact source record"):
        _project(_success(calls=()))
    duplicate = _call().model_copy(
        update={
            "call_key": "detection-call:tests-only:2",
            "source_record_key": "source-record:tests-only:other",
        }
    )
    with pytest.raises(AssociationProjectionError, match="one exact source record"):
        _project(_success(calls=(_call(), duplicate)))


def test_multiple_calls_for_one_identical_source_record_do_not_duplicate_associations() -> None:
    duplicate = _call().model_copy(update={"call_key": "detection-call:tests-only:2"})
    assert len(_project(_success(calls=(_call(), duplicate))).associations) == 1


def test_one_source_record_cannot_refer_to_two_physical_rows() -> None:
    changed = _call().model_copy(
        update={"call_key": "detection-call:tests-only:2", "row_number": 2}
    )
    with pytest.raises(AssociationProjectionError, match="conflicting physical provenance"):
        _project(_success(calls=(_call(), changed)))


@pytest.mark.parametrize(
    "locator",
    [
        {"excel_row": 1, "label": "Wrong!1", "worksheet": "Synthetic"},
        {"excel_row": "1", "label": "Synthetic!1", "worksheet": "Synthetic"},
        {"excel_row": True, "label": "Synthetic!True", "worksheet": "Synthetic"},
        {
            "excel_row": 1,
            "label": "Synthetic!1",
            "worksheet": "Synthetic",
            "relation_class": "Integrated virus",
        },
        {"row": 1, "worksheet": "Synthetic"},
    ],
)
def test_source_locator_shape_is_explicit_and_no_fallback_is_taken(locator: dict[str, Any]) -> None:
    assertion = _assertion()
    changed = assertion.model_copy(
        update={
            "supporting_evidence": assertion.supporting_evidence.model_copy(
                update={"source_locator": locator}
            )
        }
    )
    with pytest.raises(AssociationProjectionError, match="source locator"):
        _project(_success(assertions=(changed,)))


def test_legacy_worksheet_locator_requires_its_explicit_frozen_policy() -> None:
    assertion = _assertion()
    assertion = assertion.model_copy(
        update={
            "supporting_evidence": assertion.supporting_evidence.model_copy(
                update={"source_locator": {"row": 1, "worksheet": "Synthetic"}}
            )
        }
    )
    original = _bindings()
    bindings = build_association_projection_bindings(
        release=original.release,
        permitted_lineages=original.permitted_lineages,
        permitted_assertions=tuple(
            item.model_copy(update={"locator_policy": "worksheet-row-v1"})
            for item in original.permitted_assertions
        ),
    )
    assert len(_project(_success(assertions=(assertion,)), bindings).associations) == 1


def test_ambiguous_locator_policy_does_not_guess_a_permitted_shape() -> None:
    original = _bindings()
    viral_binding = next(
        item for item in original.permitted_assertions if item.assertion_type == "viral_major_taxon"
    )
    bindings = build_association_projection_bindings(
        release=original.release,
        permitted_lineages=original.permitted_lineages,
        permitted_assertions=(
            *original.permitted_assertions,
            viral_binding.model_copy(update={"locator_policy": "worksheet-row-v1"}),
        ),
    )
    with pytest.raises(AssociationProjectionError, match="ambiguous policy bindings"):
        _project(bindings=bindings)


def test_conflicting_annotations_are_not_arbitrarily_selected() -> None:
    changed = _assertion("vr_type").model_copy(
        update={"assertion_key": "assertion:tests-only:1:other", "asserted_value": "Viral contig"}
    )
    with pytest.raises(AssociationProjectionError, match="conflicting source annotations"):
        _project(_success(assertions=(_assertion(), _assertion("vr_type"), changed)))


def test_annotations_are_not_moved_from_an_unlinked_source_to_a_viral_assertion() -> None:
    with pytest.raises(AssociationProjectionError, match="lack a source-linked viral affinity"):
        _project(
            _success(
                calls=(_call(), _call(index=2)),
                assertions=(_assertion(), _assertion("hcvr", index=2)),
            )
        )


def test_missing_viral_assertions_do_not_create_an_empty_successful_relationship() -> None:
    with pytest.raises(AssociationProjectionError, match="no public viral-lineage assertion"):
        _project(_success(assertions=()))


def test_root_subclasses_and_model_copy_checksum_bypass_are_rejected() -> None:
    class UntrustedResponse(QuerySuccess):
        pass

    source = UntrustedResponse.model_validate_json(_success().model_dump_json())
    with pytest.raises(AssociationProjectionError, match="exact structured response"):
        _project(source)
    with pytest.raises(AssociationProjectionError, match="revalidation"):
        _project(bindings=_bindings().model_copy(update={"binding_sha256": "0" * 64}))


def test_bindings_reject_new_fields_duplicate_terms_and_missing_snapshot_versions() -> None:
    bindings = _bindings()
    payload = bindings.model_dump(mode="python")
    payload["automatically_classify_integrated_virus"] = True
    with pytest.raises(ValidationError):
        AssociationProjectionBindings.model_validate(payload)
    with pytest.raises(ValidationError, match="ordered and unique"):
        build_association_projection_bindings(
            release=bindings.release,
            permitted_lineages=(*bindings.permitted_lineages, bindings.permitted_lineages[0]),
            permitted_assertions=bindings.permitted_assertions,
        )


def test_portable_mini_release_is_not_silently_renamed_to_fit_the_production_contract() -> None:
    release = _bindings().release.model_dump(mode="python")
    release["release_key"] = "release:endoviho-rag:mini:v1:20990101:001"
    with pytest.raises(ValidationError, match="immutable grammar"):
        PublishedReleaseRef.model_validate(release)
