"""A scope decision preserves evidence gates and cannot authorize another question set."""

from __future__ import annotations

import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.authoring_package import (
    authoring_package_files,
    verify_authoring_package,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import QuestionManifest
from eve_relation_rag.experiments.rag_value_ablation.scope_amendment import (
    CORE53_DECISION_TEXT,
    Core53ScopeAmendment,
    build_core53_scope_amendment,
    core53_package_files,
    verify_core53_package,
    write_core53_package,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / "benchmark/rag_value_ablation/authoring_review"


@pytest.fixture
def ledger() -> AuthoringReviewLedger:
    # Wording decisions are real authoring inputs, never biological Gold/test results.
    return verify_authoring_package(HISTORICAL)


@pytest.fixture
def amendment(ledger: AuthoringReviewLedger) -> Core53ScopeAmendment:
    return build_core53_scope_amendment(ledger, decision_text=CORE53_DECISION_TEXT)


def test_exact_53_members_and_historical_bytes_preserved(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment,
) -> None:
    old = authoring_package_files(ledger)
    new = core53_package_files(ledger, amendment)
    changed = {"README.md", "readiness.json", "package_manifest.json"}
    assert set(new) == set(old) | {"scope_amendment.json"}
    for name in old.keys() - changed:
        assert old[name] == new[name] == (HISTORICAL / name).read_bytes()
    assert len(amendment.candidates) == 53
    assert {r.template_id for r in amendment.candidates} == {
        r.template_id for r in ledger.records if r.disposition == "core"
    }
    assert "UNSUP-14" not in {r.template_id for r in amendment.candidates}
    assert "UNSUP-07" not in {r.template_id for r in amendment.candidates}
    assert core53_package_files(ledger, amendment) == new


def test_count_decision_changes_only_authoring_quota(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment,
) -> None:
    files = core53_package_files(ledger, amendment)
    readiness = json.loads(files["readiness.json"])
    assert readiness["quota_changed"] is True
    assert readiness["effective_authoring_quota"]["exact_total"] == 53
    assert readiness["preregistered_quota"]["total_min"] == 60
    assert readiness["core_family_counts"] == {
        "structured": 16, "literature": 16, "hybrid": 8, "unsupported": 12, "unresolved": 1,
    }
    assert readiness["unresolved_family_ids"] == ["UNSUP-09"]
    assert readiness["scope_runtime_admission_implemented"] is False
    assert readiness["trusted_execution_ready"] is False
    assert readiness["production_dependencies_constructed"] is False
    assert readiness["trusted_question_count"] == 0
    assert readiness["gold_annotation_count"] == readiness["oracle_annotation_count"] == 0
    assert "trusted_question_quota_not_met" not in readiness["blocker_codes"]
    assert {
        "scope_amendment_runtime_admission_not_implemented",
        "family_assignment_unresolved", "approved_gold_missing", "approved_oracle_missing",
    } <= set(readiness["blocker_codes"])
    assert readiness.pop("report_sha256") == canonical_json_sha256(readiness)
    assert "不再要求每类15–20题" in files["README.md"].decode()
    assert "旧通用gate仍保留历史配额" in files["README.md"].decode()
    for field in ("source_message_timestamp", "reviewer_identity",
                  "scientific_approval", "runtime_authorization"):
        assert getattr(amendment, field) is None
    with pytest.raises(ValidationError):
        QuestionManifest.model_validate_json(files["scope_amendment.json"])


@pytest.mark.parametrize("text", ["", "同意", "按54题继续"])
def test_different_decision_rejected(ledger: AuthoringReviewLedger, text: str) -> None:
    with pytest.raises(AuthoringReviewError):
        build_core53_scope_amendment(ledger, decision_text=text)


def test_different_valid_ledger_cannot_inherit_decision(ledger: AuthoringReviewLedger) -> None:
    payload = ledger.model_dump(mode="json", exclude={"manifest_sha256"})
    payload["original_workbook_sha256"] = "a" * 64
    changed = AuthoringReviewLedger.model_validate_json(canonical_json_bytes({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }))
    with pytest.raises(AuthoringReviewError, match="exact reviewed"):
        build_core53_scope_amendment(changed, decision_text=CORE53_DECISION_TEXT)


@pytest.mark.parametrize("field", ["source_core_file_sha256", "source_package_manifest_sha256",
                                   "candidate_record", "candidate_id"])
def test_rechecksummed_substitution_rejected(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment, field: str,
) -> None:
    payload = amendment.model_dump(mode="json", exclude={"manifest_sha256"})
    if field == "candidate_record":
        payload["candidates"][0]["record_sha256"] = "a" * 64
    elif field == "candidate_id":
        payload["candidates"][0]["template_id"] = "AAA-substituted"
    else:
        payload[field] = "a" * 64
    forged = Core53ScopeAmendment.model_validate_json(canonical_json_bytes({
        **payload, "manifest_sha256": canonical_json_sha256(payload),
    }))
    with pytest.raises(AuthoringReviewError, match="reviewed membership"):
        core53_package_files(ledger, forged)


@pytest.mark.parametrize("changes", [
    {"executable": True}, {"fixed_candidate_count": 54},
    {"scientific_approval": {"approved": True}}, {"manifest_sha256": "0" * 64},
    {"unresolved_family_ids": ()},
])
def test_copy_bypass_rejected(
    ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment, changes: dict[str, object],
) -> None:
    expected_warning = (
        pytest.warns(UserWarning, match="Pydantic serializer warnings")
        if "scientific_approval" in changes or "unresolved_family_ids" in changes
        else nullcontext()
    )
    with expected_warning, pytest.raises(AuthoringReviewError):
        core53_package_files(ledger, amendment.model_copy(update=changes))


def test_subclass_rejected(ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment) -> None:
    class DerivedAmendment(Core53ScopeAmendment):
        pass

    derived = DerivedAmendment.model_validate_json(canonical_json_bytes(amendment))
    with pytest.raises(AuthoringReviewError, match="exact amendment type"):
        core53_package_files(ledger, derived)


def test_write_verify_and_no_overwrite(
    tmp_path: Path, ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment,
) -> None:
    target = tmp_path / "core53"
    write_core53_package(ledger, amendment, target)
    assert verify_core53_package(target) == amendment
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(AuthoringReviewError, match="already exists"):
        write_core53_package(ledger, amendment, target)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(AuthoringReviewError):
        verify_authoring_package(target)


@pytest.mark.parametrize("kind", ["extra", "missing", "changed", "symlink", "oversized"])
def test_changed_package_rejected(
    tmp_path: Path, ledger: AuthoringReviewLedger, amendment: Core53ScopeAmendment, kind: str,
) -> None:
    target = tmp_path / "core53"
    write_core53_package(ledger, amendment, target)
    member = target / "scope_amendment.json"
    if kind == "extra":
        (target / "unexpected.txt").write_text("unexpected")
    elif kind == "missing":
        member.unlink()
    elif kind == "symlink":
        copy = tmp_path / "copy.json"
        copy.write_bytes(member.read_bytes())
        member.unlink()
        member.symlink_to(copy)
    elif kind == "oversized":
        member.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    else:
        (target / "gold_annotation_template.csv").write_text("unreviewed annotation")
    with pytest.raises(AuthoringReviewError):
        verify_core53_package(target)


def test_cli_amend_and_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    spec = importlib.util.spec_from_file_location(
        "scope_import_cli", ROOT / "scripts/import_rag_value_reviews.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "scope"
    monkeypatch.setattr("sys.argv", ["import_rag_value_reviews.py", "amend-core53",
                                    str(HISTORICAL), "--decision-text", CORE53_DECISION_TEXT,
                                    "--output", str(target)])
    assert module.main() == 0
    monkeypatch.setattr("sys.argv", ["import_rag_value_reviews.py", "verify-scope", str(target)])
    assert module.main() == 0
    assert "Scope only: 53 candidates; executable=false." in capsys.readouterr().out


def test_committed_scope_package_matches_decision() -> None:
    amendment = verify_core53_package(ROOT / "benchmark/rag_value_ablation/authoring_review_core53")
    assert amendment.decision_text == CORE53_DECISION_TEXT
