from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.workspace_readiness import (
    PHASE3_EXECUTION_SYSTEM_KEYS,
    PHASE4_DEFERRED_SYSTEM_KEYS,
    Phase3WorkspaceAuditReport,
    audit_public_phase3_workspace,
    render_phase3_workspace_audit,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_current_public_workspace_fails_closed_without_constructing_dependencies() -> None:
    report = audit_public_phase3_workspace(REPOSITORY_ROOT, source_tree_clean=False)

    assert report.ready is False
    assert report.execution_system_keys == PHASE3_EXECUTION_SYSTEM_KEYS
    assert report.deferred_system_keys == PHASE4_DEFERRED_SYSTEM_KEYS
    assert report.production_dependencies_constructed is False
    assert {
        "approved_question_manifest_missing",
        "approved_gold_missing",
        "approved_entity_bindings_missing",
        "approved_corpus_binding_missing",
        "approved_s1_raw_context_missing",
        "approved_bge_artifact_missing",
        "association_projection_runtime_missing",
        "dataset_postgresql_activation_missing",
        "database_role_audit_missing",
        "source_tree_not_clean",
        "phase3_live_execution_authority_required",
        "request_validation_receipt_missing",
        "retrieval_runtime_validation_missing",
    } <= set(report.blocker_codes)

    rendered = render_phase3_workspace_audit(report)
    assert rendered.startswith("Phase 3 workspace readiness: BLOCKED\n")
    assert "Execution scope: S1, S2, S3, S4" in rendered
    assert "Deferred to Phase 4: S5" in rendered
    assert "No database, retriever, embedding provider, or LLM was constructed." in rendered


def test_workspace_report_checksum_rejects_tampering() -> None:
    report = audit_public_phase3_workspace(REPOSITORY_ROOT, source_tree_clean=True)
    payload = report.model_dump(mode="python")
    payload["report_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="workspace audit checksum"):
        Phase3WorkspaceAuditReport.model_validate(payload)
