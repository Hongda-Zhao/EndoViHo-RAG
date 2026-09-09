from __future__ import annotations

import subprocess
import sys
from dataclasses import replace

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.associations import (
    ASSOCIATION_DIMENSIONS,
    build_association_contract_v1,
)
from eve_relation_rag.experiments.rag_value_ablation.preflight import (
    AnchorEvidence,
    ApprovedArtifactEvidence,
    AssociationEvidence,
    BindingEvidence,
    DatabaseRoleEvidence,
    Phase3PreflightBlocked,
    Phase3PreflightDecision,
    Phase3PreflightInput,
    QuestionEvidence,
    RawContextEvidence,
    ReleaseEvidence,
    RequestValidationEvidence,
    RetrievalEvidence,
    StructuredQueryCapabilityEvidence,
    build_phase3_preflight_input,
    construct_phase3_dependencies,
    is_issued_phase3_preflight_decision,
    run_phase3_preflight,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_REVISION,
    FTS_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
)

DATASET_KEY = "release:endoviho-rag:v0:20990101:001"
CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
DATASET_MANIFEST_SHA = "a" * 64
CORPUS_MANIFEST_SHA = "b" * 64


def test_ready_preflight_is_checksum_bound_but_cannot_release_factory() -> None:
    evidence = _ready_input()
    decision = run_phase3_preflight(evidence)

    assert is_issued_phase3_preflight_decision(decision)
    assert decision.report.input_sha256 == evidence.input_sha256
    assert decision.report.ready is True
    assert tuple(item.system_key for item in decision.report.systems) == (
        "S1",
        "S2",
        "S3",
        "S4",
    )
    assert all(item.ready and not item.blocker_codes for item in decision.report.systems)

    calls: list[str] = []

    def factory() -> object:
        calls.append("constructed")
        return object()

    with pytest.raises(Phase3PreflightBlocked) as error:
        construct_phase3_dependencies(decision, factory)
    assert calls == []
    assert "phase3_live_execution_authority_required" in (
        error.value.blocker_codes
    )


def test_association_contract_and_preflight_share_one_canonical_dimension_order() -> None:
    contract = build_association_contract_v1()
    evidence = _ready_input()

    assert contract.dimensions == ASSOCIATION_DIMENSIONS
    assert evidence.associations.association_dimensions == contract.dimensions
    assert run_phase3_preflight(evidence).report.ready is True

    with pytest.raises(ValidationError, match="canonical v1 chain order"):
        AssociationEvidence.model_validate(
            {
                **evidence.associations.model_dump(mode="python"),
                "association_dimensions": tuple(reversed(ASSOCIATION_DIMENSIONS)),
            }
        )


def test_candidate_and_validated_releases_are_never_treated_as_published() -> None:
    ready = _ready_input()
    evidence = _rebuild(
        ready,
        dataset_release=ready.dataset_release.model_copy(update={"status": "candidate"}),
        corpus_release=ready.corpus_release.model_copy(update={"status": "validated"}),
    )
    decision = run_phase3_preflight(evidence)
    systems = {item.system_key: set(item.blocker_codes) for item in decision.report.systems}

    assert "dataset_release_not_published" in systems["S1"]
    assert "dataset_release_not_published" in systems["S4"]
    assert "corpus_release_not_published" in systems["S1"]
    assert "corpus_release_not_published" in systems["S2"]
    assert "corpus_release_not_published" in systems["S3"]
    assert decision.report.ready is False


def test_any_blocker_stops_before_dependency_factory() -> None:
    ready = _ready_input()
    blocked = _rebuild(
        ready,
        database_role=ready.database_role.model_copy(
            update={"runtime_transaction_read_only": False}
        ),
    )
    decision = run_phase3_preflight(blocked)
    calls: list[str] = []

    with pytest.raises(Phase3PreflightBlocked) as error:
        construct_phase3_dependencies(decision, lambda: calls.append("called"))

    assert calls == []
    assert "database_role_not_strictly_read_only" in error.value.blocker_codes


def test_exact_hash_approval_and_integrity_fail_closed_per_system() -> None:
    ready = _ready_input()
    bad_bge = ApprovedArtifactEvidence(
        approval_status="pending",
        observed_sha256="1" * 64,
        approved_sha256="2" * 64,
        integrity_status="failed",
    )
    blocked = _rebuild(
        ready,
        retrieval=ready.retrieval.model_copy(
            update={
                "bge_artifact": bad_bge,
                "bge_complete_file_set_verified": False,
                "offline_model_policy_enforced": False,
            }
        ),
    )
    decision = run_phase3_preflight(blocked)
    systems = {item.system_key: set(item.blocker_codes) for item in decision.report.systems}

    expected = {
        "bge_artifact_not_approved",
        "bge_artifact_hash_mismatch",
        "bge_artifact_integrity_not_verified",
        "bge_complete_file_set_not_verified",
        "offline_model_policy_not_enforced",
    }
    assert expected <= systems["S3"]
    assert expected.isdisjoint(systems["S2"])


def test_question_and_association_requirements_are_explicit() -> None:
    ready = _ready_input()
    evidence = _rebuild(
        ready,
        questions=ready.questions.model_copy(
            update={
                "approved_question_count": 48,
                "approved_family_counts": {
                    "structured": 16,
                    "literature": 16,
                    "hybrid": 16,
                    "unsupported": 0,
                },
            }
        ),
        associations=ready.associations.model_copy(
            update={
                "role_qualified_viral_lineage_count": 1,
                "reported_viral_region_count": 0,
            }
        ),
    )
    decision = run_phase3_preflight(evidence)
    systems = {item.system_key: set(item.blocker_codes) for item in decision.report.systems}

    assert all("approved_question_count_out_of_range" in codes for codes in systems.values())
    assert all(
        "approved_unsupported_question_count_out_of_range" in codes
        for codes in systems.values()
    )
    assert all("viral_lineage_diversity_insufficient" in codes for codes in systems.values())
    assert all(
        "reported_viral_region_coverage_missing" in codes for codes in systems.values()
    )


def test_raw_context_identity_and_budget_only_block_s1() -> None:
    ready = _ready_input()
    raw = ready.raw_context.model_copy(
        update={
            "dataset_manifest_sha256": "f" * 64,
            "reserved_output_tokens": 4096,
            "truncation_policy_explicit": False,
            "omission_policy_explicit": False,
        }
    )
    decision = run_phase3_preflight(_rebuild(ready, raw_context=raw))
    systems = {item.system_key: set(item.blocker_codes) for item in decision.report.systems}

    assert {
        "raw_context_release_identity_mismatch",
        "raw_context_token_budget_invalid",
        "raw_context_truncation_policy_missing",
        "raw_context_omission_policy_missing",
    } <= systems["S1"]
    assert "raw_context_release_identity_mismatch" not in systems["S2"]


def test_structured_query_capability_is_bound_and_complete_for_s4() -> None:
    ready = _ready_input()
    blocked = _rebuild(
        ready,
        structured_query_capability=ready.structured_query_capability.model_copy(
            update={
                "question_manifest_sha256": "f" * 64,
                "route_accepted_question_count": 31,
                "query_plan_validated_question_count": 30,
                "association_projection_validated_question_count": 0,
            }
        ),
    )
    systems = {
        item.system_key: set(item.blocker_codes)
        for item in run_phase3_preflight(blocked).report.systems
    }

    assert {
        "structured_query_capability_question_identity_mismatch",
        "structured_query_route_coverage_incomplete",
        "structured_query_plan_coverage_incomplete",
        "structured_association_projection_coverage_incomplete",
    } <= systems["S4"]
    assert "structured_query_route_coverage_incomplete" not in systems["S1"]


def test_request_validation_replay_is_required_for_every_phase3_system() -> None:
    ready = _ready_input()
    blocked = _rebuild(
        ready,
        request_validation=ready.request_validation.model_copy(
            update={
                "replayed_question_count": 63,
                "validated_scope_refusal_question_count": 0,
                "downstream_calls_after_scope_refusal": 1,
            }
        ),
    )
    systems = run_phase3_preflight(blocked).report.systems

    assert all(
        {
            "request_validation_replay_coverage_incomplete",
            "request_validation_refusal_coverage_incomplete",
            "request_validation_downstream_after_refusal",
        }
        <= set(system.blocker_codes)
        for system in systems
    )


def test_s5_preparation_evidence_is_optional_and_does_not_block_phase3() -> None:
    ready = _ready_input()
    assert ready.anchors is not None
    assert ready.binding is not None
    without_s5 = _rebuild(ready, anchors=None, binding=None)
    stale_s5 = _rebuild(
        ready,
        anchors=ready.anchors.model_copy(
            update={"required_target_coverage_complete": False}
        ),
        binding=ready.binding.model_copy(
            update={"corpus_manifest_sha256": "f" * 64}
        ),
    )

    assert run_phase3_preflight(without_s5).report.ready is True
    assert run_phase3_preflight(stale_s5).report.ready is True


def test_decision_cannot_be_forged_or_replaced_by_serialized_shape() -> None:
    issued = run_phase3_preflight(_ready_input())

    with pytest.raises(TypeError, match="only be issued"):
        Phase3PreflightDecision(report=issued.report, _issuer=object())
    assert is_issued_phase3_preflight_decision(issued.report.model_dump()) is False
    with pytest.raises(TypeError, match="issued"):
        construct_phase3_dependencies(issued.report.model_dump(), lambda: object())  # type: ignore[arg-type]


def test_replaced_blocked_decision_cannot_release_dependency_factory() -> None:
    ready = _ready_input()
    blocked = _rebuild(
        ready,
        database_role=ready.database_role.model_copy(
            update={"runtime_transaction_read_only": False}
        ),
    )
    issued = run_phase3_preflight(blocked)
    forged_report = issued.report.model_copy(update={"ready": True})
    replaced = replace(issued, report=forged_report)
    calls: list[str] = []

    assert is_issued_phase3_preflight_decision(replaced) is False
    with pytest.raises(TypeError, match="issued"):
        construct_phase3_dependencies(replaced, lambda: calls.append("called"))
    assert calls == []


def test_unmodified_decision_copy_has_no_runtime_authority() -> None:
    issued = run_phase3_preflight(_ready_input())
    copied = replace(issued)
    calls: list[str] = []

    assert copied == issued
    assert copied is not issued
    assert is_issued_phase3_preflight_decision(copied) is False
    with pytest.raises(TypeError, match="issued"):
        construct_phase3_dependencies(copied, lambda: calls.append("called"))
    assert calls == []


def test_input_and_report_checksums_reject_tampering() -> None:
    evidence = _ready_input()
    payload = evidence.model_dump(mode="python")
    payload["input_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="input checksum"):
        Phase3PreflightInput.model_validate(payload)

    report = run_phase3_preflight(evidence).report
    report_payload = report.model_dump(mode="python")
    report_payload["report_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="report checksum"):
        type(report).model_validate(report_payload)


def test_import_does_not_read_production_settings_or_load_runtime_dependencies() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import eve_relation_rag.experiments.rag_value_ablation.preflight; "
            "forbidden=('eve_relation_rag.config.settings', 'sentence_transformers'); "
            "assert not any(name in sys.modules for name in forbidden); print('offline')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "offline"
    assert completed.stderr == ""


def _artifact(character: str) -> ApprovedArtifactEvidence:
    checksum = character * 64
    return ApprovedArtifactEvidence(
        approval_status="approved",
        observed_sha256=checksum,
        approved_sha256=checksum,
        integrity_status="passed",
    )


def _release(
    release_key: str,
    manifest_sha256: str,
    receipt_character: str,
    snapshot_character: str,
) -> ReleaseEvidence:
    return ReleaseEvidence(
        release_key=release_key,
        status="published",
        manifest=ApprovedArtifactEvidence(
            approval_status="approved",
            observed_sha256=manifest_sha256,
            approved_sha256=manifest_sha256,
            integrity_status="passed",
        ),
        validation_receipt=_artifact(receipt_character),
        receipt_status="passed",
        receipt_trusted=True,
        snapshot_fingerprint=_artifact(snapshot_character),
    )


def _ready_input() -> Phase3PreflightInput:
    return build_phase3_preflight_input(
        questions=QuestionEvidence(
            question_manifest=_artifact("1"),
            gold_manifest=_artifact("2"),
            entity_binding_manifest=_artifact("3"),
            approved_question_count=64,
            approved_family_counts={
                "structured": 16,
                "literature": 16,
                "hybrid": 16,
                "unsupported": 16,
            },
            dataset_release_key=DATASET_KEY,
            dataset_manifest_sha256=DATASET_MANIFEST_SHA,
            corpus_release_key=CORPUS_KEY,
            corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
        ),
        associations=AssociationEvidence(
            association_contract=_artifact("4"),
            structured_association_manifest=_artifact("5"),
            association_dimensions=ASSOCIATION_DIMENSIONS,
            represented_source_taxon_count=4,
            represented_assembly_count=6,
            represented_eve_locus_count=12,
            reported_viral_region_count=12,
            role_qualified_viral_lineage_count=3,
            evidence_source_count=4,
        ),
        database_role=DatabaseRoleEvidence(
            audit=_artifact("6"),
            role_default_transaction_read_only=True,
            runtime_transaction_read_only=True,
            schema_create_denied=True,
            table_create_denied=True,
            dml_denied=True,
        ),
        dataset_release=_release(DATASET_KEY, DATASET_MANIFEST_SHA, "7", "8"),
        corpus_release=_release(CORPUS_KEY, CORPUS_MANIFEST_SHA, "9", "c"),
        raw_context=RawContextEvidence(
            material_manifest=_artifact("d"),
            construction_policy=_artifact("e"),
            tokenizer_artifact=_artifact("f"),
            dataset_release_key=DATASET_KEY,
            dataset_manifest_sha256=DATASET_MANIFEST_SHA,
            corpus_release_key=CORPUS_KEY,
            corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
            tokenizer_id="example/tokenizer",
            tokenizer_revision="3" * 40,
            tokenizer_artifact_manifest_sha256="f" * 64,
            model_context_limit_tokens=4096,
            reserved_output_tokens=512,
            truncation_policy_explicit=True,
            omission_policy_explicit=True,
        ),
        retrieval=RetrievalEvidence(
            policy_manifest=_artifact("1"),
            fts_policy_key=FTS_POLICY_KEY,
            hybrid_retrieval_policy_key=RETRIEVAL_POLICY_KEY,
            embedding_model_key=EMBEDDING_MODEL_KEY,
            embedding_revision=EMBEDDING_REVISION,
            branch_candidate_depth=100,
            rrf_k=60,
            summary_branch_enabled=True,
            bge_artifact=_artifact("2"),
            bge_complete_file_set_verified=True,
            offline_model_policy_enforced=True,
        ),
        structured_query_capability=StructuredQueryCapabilityEvidence(
            validation_receipt=_artifact("5"),
            question_manifest_sha256="1" * 64,
            applicable_question_count=32,
            route_accepted_question_count=32,
            query_plan_validated_question_count=32,
            association_projection_validated_question_count=32,
        ),
        request_validation=RequestValidationEvidence(
            validation_receipt=_artifact("6"),
            question_manifest_sha256="1" * 64,
            replayed_question_count=64,
            scope_admitted_question_count=63,
            scope_refused_question_count=1,
            validated_scope_refusal_question_count=1,
            downstream_calls_after_scope_refusal=0,
        ),
        anchors=AnchorEvidence(
            manifest=_artifact("3"),
            corpus_release_key=CORPUS_KEY,
            corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
            structured_target_anchor_count=24,
            required_target_coverage_complete=True,
        ),
        binding=BindingEvidence(
            manifest=_artifact("4"),
            dataset_release_key=DATASET_KEY,
            dataset_manifest_sha256=DATASET_MANIFEST_SHA,
            corpus_release_key=CORPUS_KEY,
            corpus_manifest_sha256=CORPUS_MANIFEST_SHA,
        ),
    )


def _rebuild(value: Phase3PreflightInput, **updates: object) -> Phase3PreflightInput:
    payload = value.model_dump(mode="python")
    payload.pop("input_schema_version")
    payload.pop("input_sha256")
    payload.update(updates)
    return build_phase3_preflight_input(**payload)
