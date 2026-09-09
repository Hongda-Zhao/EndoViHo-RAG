"""Derive a small human worksheet from the exact reviewed authoring packet.

This is workload planning, not scientific annotation. No evidence is selected,
no answers are inferred, and no human or execution approval is issued.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewLedger,
)
from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    verify_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
    ACTIVE_REVISION,
    REVISED_CHINESE,
    load_core53_candidates,
    revision_policy,
)
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import _read_input
from eve_relation_rag.literature.hashing import canonical_json_sha256

ENTITY_LABELS = {
    "ASSEMBLY_A": "一个 Assembly（完整 accession.version）",
    "ASSEMBLY_SOURCE_TAXON_A": "一个 Assembly 来源物种／分类单元",
    "EVE_LOCUS_A": "位点 A（完整 locus key）",
    "EVE_LOCUS_B": "位点 B（完整 locus key）",
    "EVE_LOCUS_C": "位点 C（完整 locus key）",
    "REPORTED_REGION_A": "一处文献报告的病毒区域（附论文及定位）",
    "SOURCE_TAXON_LINEAGE_A": "一个来源分类群（说明是否包含下级分类）",
    "VIRAL_LINEAGE_A": "病毒谱系 A（保留来源角色、版本）",
    "VIRAL_LINEAGE_B": "病毒谱系 B（保留来源角色、版本）",
}
FAMILY_LABELS = {
    "structured": "查数据库",
    "literature": "查文献",
    "hybrid": "两边对照",
    "unsupported": "回答边界",
}


def build_annotation_workload(classified_package: Path) -> dict[str, object]:
    """Verify the historical packet, then list only annotations it actually uses."""

    assignment = verify_classified_package(classified_package)
    ledger = AuthoringReviewLedger.model_validate_json(
        _read_input(classified_package / "ledger.json")
    )
    candidates = load_core53_candidates(classified_package)
    source = {row.template_id: row for row in ledger.records}
    family_counts = Counter(row.family for row in candidates)
    slots = sorted({slot for row in candidates for slot in row.entity_slots})
    questions = []
    for row in candidates:
        original = source[row.template_id]
        needs_literature = row.family in {"literature", "hybrid"}
        if row.family == "unsupported":
            instruction = "确认可回答条件或拒答边界；不能统一填全拒答。"
        elif row.template_id == "UNSUP-09":
            instruction = "核实名称对应表、原标签、角色、版本和无法对应项；附证据。"
        elif needs_literature:
            instruction = "填人工核实的事实／证据编号；说明完整范围和必要限制。"
        else:
            instruction = "核实精确记录集合、字段和来源；可引用同一事实表。"
        questions.append({
            "question_id": row.template_id,
            "family": row.family,
            "family_label_zh": FAMILY_LABELS[row.family],
            "question_zh": REVISED_CHINESE.get(
                row.template_id, original.proposed_question_zh or original.original_question_zh,
            ),
            "question_text_template": row.question_text_template,
            "question_record_sha256": row.record_sha256,
            "entity_slots": list(row.entity_slots),
            "needs_literature_evidence": needs_literature,
            "what_to_review_zh": instruction,
            "accepted_response_policy_from_historical_ledger": original.response_policy,
            "active_source_evidence_policy": revision_policy(),
            "policy_history_note": (
                "UNSUP-09 family is now hybrid; the historical pending reclassification "
                "sentence is superseded by family_assignment, not a new review request."
                if row.template_id == "UNSUP-09" else None
            ),
            "review_status": "pending",
            "gold_reference_ids": [],
            "oracle_reference_ids": [],
            "approval": None,
        })
    entities = [{
        "entity_slot": slot,
        "label_zh": ENTITY_LABELS[slot],
        "used_by_question_ids": [r.template_id for r in candidates if slot in r.entity_slots],
        "selected_value": None,
        "approval": None,
    } for slot in slots]
    payload: dict[str, object] = {
        "schema_version": "rag-value-annotation-workload-v1",
        "question_revision": ACTIVE_REVISION,
        "artifact_kind": "authoring_only",
        "family_assignment_sha256": assignment.manifest_sha256,
        "question_count": len(candidates),
        "family_counts": dict(sorted(family_counts.items())),
        "shared_entity_count": len(entities),
        "answerable_family_question_count": sum(
            row.family != "unsupported" for row in candidates
        ),
        "literature_or_hybrid_question_count": sum(
            row.family in {"literature", "hybrid"} for row in candidates
        ),
        "boundary_question_count": family_counts["unsupported"],
        "distinct_evidence_count": None,
        "trusted_question_count": 0,
        "execution_authorized": False,
        "entities": entities,
        "questions": questions,
        "reuse_policy": {
            "human_confirmed_evidence_may_be_referenced_by_multiple_questions": True,
            "per_question_scope_and_completeness_review_required": True,
            "oracle_use_requires_explicit_separate_human_approval": True,
            "retriever_or_model_output_is_not_gold": True,
            "one_batch_signoff_requires_exact_reviewed_payload_and_membership": True,
        },
        "post_run_blind_review_minimum": {
            "real_answers": 20,
            "actual_atomic_claims": 100,
            "independent_reviewers": 2,
        },
    }
    return {**payload, "workload_sha256": canonical_json_sha256(payload)}
