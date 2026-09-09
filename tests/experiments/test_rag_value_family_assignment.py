"""An approved scoring group changes neither historical evidence nor runtime authority."""

from __future__ import annotations

import hashlib
import json
import warnings
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.authoring_package import (
    verify_authoring_package,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationQuestion,
    HumanApproval,
    OracleEvidenceEntry,
    QuestionManifest,
)
from eve_relation_rag.experiments.rag_value_ablation.family_assignment import (
    HYBRID_DECISION_TEXT,
    HybridFamilyAssignment,
    build_hybrid_family_assignment,
    classified_package_files,
    verify_classified_package,
    write_classified_package,
)
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import (
    Core53ScopeAmendment,
    core53_package_files,
    verify_core53_package,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / "benchmark/rag_value_ablation/authoring_review"
CORE53 = ROOT / "benchmark/rag_value_ablation/authoring_review_core53"


@pytest.fixture
def ledger() -> AuthoringReviewLedger:
    # These are existing authoring decisions, not invented scientific Gold labels.
    return verify_authoring_package(HISTORICAL)


@pytest.fixture
def scope() -> Core53ScopeAmendment:
    return verify_core53_package(CORE53)


@pytest.fixture
def assignment(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
) -> HybridFamilyAssignment:
    return build_hybrid_family_assignment(ledger, scope, decision_text=HYBRID_DECISION_TEXT)


def _rows(raw: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in raw.splitlines()]


def test_exact_decision_and_sources_are_bound(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    source = next(row for row in ledger.records if row.template_id == "UNSUP-09")
    assert source.family is None
    assert assignment.template_id == source.template_id
    assert assignment.source_record_sha256 == source.record_sha256
    assert assignment.source_scope_sha256 == scope.manifest_sha256
    assert assignment.source_package_manifest_sha256 == hashlib.sha256(
        core53_package_files(ledger, scope)["package_manifest.json"]
    ).hexdigest()
    assert assignment.previous_family is None
    assert assignment.assigned_family == "hybrid"
    assert assignment.decision_text == HYBRID_DECISION_TEXT == "可以，推进"
    assert assignment.proposal_text
    payload = assignment.model_dump(mode="json", exclude={"manifest_sha256"})
    assert assignment.manifest_sha256 == canonical_json_sha256(payload)


def test_new_projection_preserves_both_historical_packages(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    histories = {
        path: {p.name: p.read_bytes() for p in path.iterdir()}
        for path in (HISTORICAL, CORE53)
    }
    source_files = core53_package_files(ledger, scope)
    files = classified_package_files(ledger, scope, assignment)
    assert files == classified_package_files(ledger, scope, assignment)
    assert set(files) == set(source_files) | {
        "family_assignment.json", "classified_candidates.jsonl",
    }
    for name in (
        "ledger.json", "scope_amendment.json", "core_candidates.jsonl",
        "excluded_decisions.jsonl", "external_extension_proposals.jsonl",
        "entity_bindings_template.json", "run_authorization_template.json",
    ):
        assert files[name] == source_files[name] == histories[CORE53][name]
    for path, historical in histories.items():
        assert historical == {p.name: p.read_bytes() for p in path.iterdir()}
    # The unchanged source ledger and original scope describe historical facts.
    assert next(row for row in ledger.records if row.template_id == "UNSUP-09").family is None
    assert scope.unresolved_family_ids == ("UNSUP-09",)


def test_all_53_projection_rows_bound_to_unchanged_wording(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    files = classified_package_files(ledger, scope, assignment)
    rows = _rows(files["classified_candidates.jsonl"])
    sources = {r.template_id: r for r in ledger.records if r.disposition == "core"}
    assert len(rows) == len(sources) == 53
    assert {r["template_id"] for r in rows} == set(sources)
    assert Counter(r["family"] for r in rows) == {
        "structured": 16, "literature": 16, "hybrid": 9, "unsupported": 12,
    }
    for row in rows:
        source = sources[str(row["template_id"])]
        assert row["source_record_sha256"] == source.record_sha256
        assert row["source_scope_sha256"] == scope.manifest_sha256
        assert row["family_assignment_sha256"] == assignment.manifest_sha256
        assert row["family"] == ("hybrid" if source.template_id == "UNSUP-09" else source.family)
        assert row["question_text_template"] == source.question_text_template
        assert row["question_text_sha256"] == hashlib.sha256(
            source.question_text_template.encode()
        ).hexdigest()
        assert row["entity_slots"] == list(source.entity_slots)
        assert row["benchmark_review_status"] == "pending"
        assert row["gold"] is row["oracle"] is row["approval"] is None
        assert row["executable"] is False
        unhashed = {key: value for key, value in row.items() if key != "record_sha256"}
        assert row["record_sha256"] == canonical_json_sha256(unhashed)


def test_empty_gold_and_oracle_bind_effective_projection_not_source_record(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    files = classified_package_files(ledger, scope, assignment)
    candidates = {r["template_id"]: r for r in _rows(files["classified_candidates.jsonl"])}
    for kind in ("gold", "oracle"):
        rows = _rows(files[f"{kind}_annotation_template.jsonl"])
        assert len(rows) == 53
        assert {r["template_id"] for r in rows} == set(candidates)
        for row in rows:
            candidate = candidates[row["template_id"]]
            assert str(row["schema_version"]).endswith("-v2")
            assert row["candidate_record_sha256"] == candidate["record_sha256"]
            assert row["source_candidate_record_sha256"] == candidate["source_record_sha256"]
            assert row["source_scope_sha256"] == scope.manifest_sha256
            assert row["family_assignment_sha256"] == assignment.manifest_sha256
            assert row["review_status"] == "pending"
            assert row["approval"] is None
            assert row["executable"] is False
            assert row["bound_question_text_sha256"] is None
            if kind == "gold":
                assert row["family"] == candidate["family"]
                assert row["gold"] is None
            else:
                assert row["structured_facts"] is row["literature_chunk_keys"] is None
            trusted_type = EvaluationQuestion if kind == "gold" else OracleEvidenceEntry
            with pytest.raises(ValidationError):
                trusted_type.model_validate_json(canonical_json_bytes(row))


def test_readiness_resolves_only_family_assignment(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    old = json.loads(core53_package_files(ledger, scope)["readiness.json"])
    files = classified_package_files(ledger, scope, assignment)
    readiness = json.loads(files["readiness.json"])
    assert readiness["core_family_counts"] == {
        "structured": 16, "literature": 16, "hybrid": 9, "unsupported": 12,
    }
    assert readiness["unresolved_family_ids"] == []
    assert "family_assignment_unresolved" not in readiness["blocker_codes"]
    assert (set(old["blocker_codes"]) - {"family_assignment_unresolved"}) <= set(
        readiness["blocker_codes"]
    )
    for key in ("trusted_question_count", "gold_annotation_count", "oracle_annotation_count"):
        assert readiness[key] == 0
    for key in ("trusted_execution_ready", "production_dependencies_constructed",
                "scope_runtime_admission_implemented"):
        assert readiness[key] is False
    assert readiness.pop("report_sha256") == canonical_json_sha256(readiness)
    authorization = json.loads(files["run_authorization_template.json"])
    assert authorization["provider"] is authorization["model_id"] is None
    assert authorization["executable"] is False
    for trusted_type in (HumanApproval, QuestionManifest, OracleEvidenceEntry):
        with pytest.raises(ValidationError):
            trusted_type.model_validate_json(files["family_assignment.json"])


@pytest.mark.parametrize("decision_text", ["", "可以", "同意", "按这 53 题继续"])
def test_other_decision_text_rejected(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment, decision_text: str,
) -> None:
    with pytest.raises(AuthoringReviewError):
        build_hybrid_family_assignment(ledger, scope, decision_text=decision_text)


@pytest.mark.parametrize("field", [
    "source_record_sha256", "source_scope_sha256", "source_package_manifest_sha256",
])
def test_rechecksummed_source_substitution_rejected(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment, field: str,
) -> None:
    payload = assignment.model_dump(mode="json", exclude={"manifest_sha256"})
    payload[field] = "a" * 64
    # A coherent self-checksum must never be enough to approve different inputs.
    with pytest.raises((AuthoringReviewError, ValidationError)):
        forged = HybridFamilyAssignment.model_validate_json(canonical_json_bytes({
            **payload, "manifest_sha256": canonical_json_sha256(payload),
        }))
        classified_package_files(ledger, scope, forged)


@pytest.mark.parametrize("construction", ["copy", "construct"])
@pytest.mark.parametrize("changes", [
    {"template_id": "UNSUP-08"}, {"assigned_family": "unsupported"},
    {"previous_family": "unsupported"}, {"executable": True},
    {"scientific_approval": {"approved": True}}, {"runtime_authorization": {"approved": True}},
    {"proposal_text": "Approve a different question."}, {"manifest_sha256": "0" * 64},
])
def test_model_copy_cannot_bypass_assignment_validation(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment, changes: dict[str, object], construction: str,
) -> None:
    with warnings.catch_warnings(), pytest.raises(AuthoringReviewError):
        warnings.simplefilter("ignore", UserWarning)
        forged = (
            assignment.model_copy(update=changes) if construction == "copy"
            else HybridFamilyAssignment.model_construct(**{
                **assignment.model_dump(mode="python"), **changes,
            })
        )
        classified_package_files(ledger, scope, forged)


def test_assignment_subclass_rejected(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    class DerivedAssignment(HybridFamilyAssignment):
        pass

    derived = DerivedAssignment.model_validate_json(canonical_json_bytes(assignment))
    with pytest.raises(AuthoringReviewError):
        classified_package_files(ledger, scope, derived)


def test_other_self_consistent_ledger_rejected(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
) -> None:
    payload = ledger.model_dump(mode="json", exclude={"manifest_sha256"})
    payload["original_workbook_sha256"] = "a" * 64
    changed = AuthoringReviewLedger.model_validate_json(canonical_json_bytes({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }))
    with pytest.raises(AuthoringReviewError):
        build_hybrid_family_assignment(changed, scope, decision_text=HYBRID_DECISION_TEXT)


def test_self_consistent_scope_with_substituted_candidate_rejected(
    ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
) -> None:
    payload = scope.model_dump(mode="json", exclude={"manifest_sha256"})
    payload["candidates"][0]["record_sha256"] = "a" * 64
    changed = Core53ScopeAmendment.model_validate_json(canonical_json_bytes({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }))
    with pytest.raises(AuthoringReviewError):
        build_hybrid_family_assignment(ledger, changed, decision_text=HYBRID_DECISION_TEXT)


def test_write_verify_and_no_overwrite(
    tmp_path: Path, ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    target = tmp_path / "classified"
    write_classified_package(ledger, scope, assignment, target)
    assert verify_classified_package(target) == assignment
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(AuthoringReviewError):
        write_classified_package(ledger, scope, assignment, target)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(AuthoringReviewError):
        verify_authoring_package(target)
    with pytest.raises(AuthoringReviewError):
        verify_core53_package(target)


@pytest.mark.parametrize("kind", [
    "extra", "missing", "changed_projection", "changed_gold", "symlink", "oversized",
])
def test_corrupted_or_substituted_package_rejected(
    tmp_path: Path, ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment, kind: str,
) -> None:
    target = tmp_path / "classified"
    write_classified_package(ledger, scope, assignment, target)
    member = target / "family_assignment.json"
    if kind == "extra":
        (target / "extra.txt").write_text("unexpected")
    elif kind == "missing":
        member.unlink()
    elif kind == "symlink":
        copy = tmp_path / "copy.json"
        copy.write_bytes(member.read_bytes())
        member.unlink()
        member.symlink_to(copy)
    elif kind == "oversized":
        member.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    elif kind == "changed_gold":
        (target / "gold_annotation_template.jsonl").write_text('{"gold":"unapproved"}\n')
    else:
        rows = _rows((target / "classified_candidates.jsonl").read_bytes())
        target_row = next(r for r in rows if r["template_id"] == "UNSUP-09")
        target_row["family"] = "unsupported"
        (target / "classified_candidates.jsonl").write_bytes(
            b"\n".join(canonical_json_bytes(row) for row in rows) + b"\n"
        )
    with pytest.raises(AuthoringReviewError):
        verify_classified_package(target)


def test_symlink_output_cannot_overwrite_existing_packet(
    tmp_path: Path, ledger: AuthoringReviewLedger, scope: Core53ScopeAmendment,
    assignment: HybridFamilyAssignment,
) -> None:
    original = tmp_path / "original"
    write_classified_package(ledger, scope, assignment, original)
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    before = {p.name: p.read_bytes() for p in original.iterdir()}
    with pytest.raises(AuthoringReviewError):
        write_classified_package(ledger, scope, assignment, alias)
    with pytest.raises(AuthoringReviewError):
        verify_classified_package(alias)
    assert before == {p.name: p.read_bytes() for p in original.iterdir()}
