"""Authoring migration tests; the approved fixtures are exclusively synthetic."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.annotation_workload import (
    build_annotation_workload,
)
from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    require_trusted_question_set,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    QuestionManifest,
    build_evaluation_question,
)
from eve_relation_rag.experiments.rag_value_ablation.question_revision import (
    ACTIVE_REVISION,
    REVISED_ENGLISH,
    load_core53_candidates,
    revise_candidates,
    revision_package_files,
    verify_revision_package,
)
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import Core53QuestionScope
from eve_relation_rag.experiments.rag_value_ablation.workspace_readiness import (
    audit_public_phase3_workspace,
)
from tests.experiments import test_rag_value_scoped_admission as fixtures

entities = fixtures.entities
scope = fixtures.scope
questions = fixtures.questions
PACKAGE = fixtures.PACKAGE


def test_only_three_candidates_change_and_no_approval_is_added() -> None:
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PACKAGE.iterdir()}
    old = load_core53_candidates(PACKAGE, revision="classified-v1")
    new = load_core53_candidates(PACKAGE)
    assert {a.template_id for a, b in zip(old, new, strict=True) if a != b} == set(REVISED_ENGLISH)
    assert Counter(r.family for r in new) == {
        "structured": 16,
        "literature": 16,
        "hybrid": 9,
        "unsupported": 12,
    }
    assert len({s for r in new for s in r.entity_slots}) == 9
    assert all(r.gold is r.oracle is r.approval is None and not r.executable for r in new)
    assert all(r.benchmark_review_status == "pending" for r in new)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in PACKAGE.iterdir()}
    with pytest.raises(ValueError, match="exact historical"):
        revise_candidates(new)


def test_package_reproducibility_and_tamper_rejection(tmp_path: Path) -> None:
    files = revision_package_files(PACKAGE)
    assert files == revision_package_files(PACKAGE)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    verify_revision_package(PACKAGE, tmp_path)
    (tmp_path / "authoring_policy.json").write_text("{}")
    with pytest.raises(ValueError):
        verify_revision_package(PACKAGE, tmp_path)


def test_active_revision_is_shared_by_workload_and_workspace() -> None:
    workload = build_annotation_workload(PACKAGE)
    assert workload["question_revision"] == ACTIVE_REVISION
    rows = workload["questions"]
    assert isinstance(rows, list)
    for row in rows:
        if row["question_id"] in REVISED_ENGLISH:
            assert row["question_text_template"] == REVISED_ENGLISH[row["question_id"]]
            assert "至少一篇" in row["question_zh"]
    report = audit_public_phase3_workspace(PACKAGE.parents[2], source_tree_clean=False)
    observations = {check.check_key: check.observation for check in report.checks}
    assert "templates=53" in observations["scientific_questions"]
    assert "single-source-v2" in observations["scientific_questions"]
    assert "bindings=9" in observations["entity_bindings"]


def test_revised_admission_requires_exact_new_question_text(
    scope: Core53QuestionScope,
    questions: QuestionManifest,
) -> None:
    with pytest.raises(AnnotationError, match="text or family"):
        require_trusted_question_set(questions, scope=scope, revision=ACTIVE_REVISION)
    values = {row.entity_slot: row.question_value for row in scope.entities.bindings}
    rows = []
    for question in questions.questions:
        if question.question_id not in REVISED_ENGLISH:
            rows.append(question)
            continue
        text = REVISED_ENGLISH[question.question_id]
        for slot, value in values.items():
            text = text.replace("{" + slot + "}", value)
        rows.append(
            build_evaluation_question(
                question_id=question.question_id,
                family=question.family,
                question_text=text,
                review_status="approved",
                approval=question.approval,
                gold=question.gold,
                authoring_notes="Synthetic amendment test only.",
            )
        )
    revised = fixtures._manifest(rows)
    assert len(require_trusted_question_set(revised, scope=scope, revision=ACTIVE_REVISION)) == 53
    with pytest.raises(AnnotationError, match="text or family"):
        require_trusted_question_set(revised, scope=scope)


def test_preflight_core53_needs_bound_text_not_just_counts(scope, questions):
    from eve_relation_rag.experiments.rag_value_ablation.preflight import run_phase3_preflight
    from tests.experiments.test_rag_value_phase3_preflight import _ready_input, _rebuild

    ready = _ready_input()
    counts = {"structured": 16, "literature": 16, "hybrid": 9, "unsupported": 12}
    evidence = ready.questions.model_copy(
        update={
            "scope_revision": "single-source-v2",
            "approved_question_count": 53,
            "approved_family_counts": counts,
        }
    )
    report = run_phase3_preflight(_rebuild(ready, questions=evidence)).report
    assert all("core53_bound_scope_missing" in s.blocker_codes for s in report.systems)
    assert all(
        "approved_question_count_out_of_range" not in s.blocker_codes for s in report.systems
    )
    evidence = evidence.model_copy(
        update={
            "core53_scope": scope,
            "bound_question_manifest": questions,
        }
    )
    report = run_phase3_preflight(_rebuild(ready, questions=evidence)).report
    assert all("core53_bound_scope_invalid" in s.blocker_codes for s in report.systems)
