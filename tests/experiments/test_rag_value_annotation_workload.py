from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.annotation_workload import (
    build_annotation_workload,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import AuthoringReviewError
from eve_relation_rag.literature.hashing import canonical_json_sha256

PACKAGE = Path(__file__).resolve().parents[2] / (
    "benchmark/rag_value_ablation/authoring_review_core53_classified"
)


def test_workload_derives_nine_shared_objects_without_unused_assembly() -> None:
    result = build_annotation_workload(PACKAGE)
    assert result["shared_entity_count"] == 9
    assert result["question_count"] == 53
    assert result["family_counts"] == {
        "structured": 16, "literature": 16, "hybrid": 9, "unsupported": 12,
    }
    assert result["answerable_family_question_count"] == 41
    assert result["literature_or_hybrid_question_count"] == 25
    assert result["boundary_question_count"] == 12
    entities = result["entities"]
    assert isinstance(entities, list)
    assert "ASSEMBLY_B" not in {row["entity_slot"] for row in entities}
    assert len(next(r for r in entities if r["entity_slot"] == "VIRAL_LINEAGE_A")[
        "used_by_question_ids"
    ]) == 27


def test_no_gold_oracle_or_fake_approval_is_created() -> None:
    result = build_annotation_workload(PACKAGE)
    assert result["execution_authorized"] is False
    assert result["trusted_question_count"] == 0
    assert result["distinct_evidence_count"] is None
    rows = result["questions"]
    assert isinstance(rows, list)
    for row in rows:
        assert row["review_status"] == "pending"
        assert row["approval"] is None
        assert row["gold_reference_ids"] == row["oracle_reference_ids"] == []
    hybrid = next(row for row in rows if row["question_id"] == "UNSUP-09")
    assert hybrid["family"] == "hybrid"
    assert hybrid["needs_literature_evidence"] is True
    assert "superseded" in hybrid["policy_history_note"]
    for qid in ("HOST-H-02", "VIRUS-H-02", "REL-H-02"):
        row = next(row for row in rows if row["question_id"] == qid)
        assert "Count distinct publications, not chunks." in row[
            "accepted_response_policy_from_historical_ledger"
        ]
    for qid in ("UNSUP-01", "UNSUP-02", "UNSUP-06"):
        row = next(row for row in rows if row["question_id"] == qid)
        assert "不能统一填全拒答" in row["what_to_review_zh"]


def test_plan_is_deterministic_and_checksum_bound() -> None:
    first = build_annotation_workload(PACKAGE)
    assert first == build_annotation_workload(PACKAGE)
    assert first["workload_sha256"] == canonical_json_sha256({
        key: value for key, value in first.items() if key != "workload_sha256"
    })


def test_tampered_or_symlinked_authoring_packet_is_rejected(tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(PACKAGE, target_is_directory=True)
    with pytest.raises(AuthoringReviewError):
        build_annotation_workload(alias)
    copy = tmp_path / "copy"
    shutil.copytree(PACKAGE, copy)
    candidate = copy / "classified_candidates.jsonl"
    candidate.write_bytes(candidate.read_bytes().replace(b"pending", b"approved", 1))
    with pytest.raises(AuthoringReviewError):
        build_annotation_workload(copy)
