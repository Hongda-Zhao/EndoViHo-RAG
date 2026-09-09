"""Derived authoring packets are reproducible, blank, isolated, and fail closed."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.authoring_package import (
    authoring_package_files,
    render_reviewed_questions,
    verify_authoring_package,
    write_authoring_package,
)
from eve_relation_rag.experiments.rag_value_ablation.authoring_review import (
    AuthoringReviewError,
    AuthoringReviewLedger,
    AuthoringReviewRecord,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationQuestion,
    OracleEvidenceEntry,
    QuestionManifest,
)
from eve_relation_rag.experiments.rag_value_ablation.scientific_questions import (
    scientific_entity_bindings_template_bytes,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from tests.experiments.test_rag_value_authoring_review import _load, _synthetic_sheets


@pytest.fixture
def ledger(tmp_path: Path) -> AuthoringReviewLedger:
    return _load(tmp_path, *_synthetic_sheets())


def _jsonl(raw: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in raw.splitlines()]


def test_exact_candidate_split_excludes_extension_and_deletions_from_annotation_forms(
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    core = _jsonl(files["core_candidates.jsonl"])
    extension = _jsonl(files["external_extension_proposals.jsonl"])
    excluded = _jsonl(files["excluded_decisions.jsonl"])
    assert (len(core), len(extension), len(excluded)) == (53, 1, 10)
    assert extension[0]["template_id"] == "UNSUP-14"
    all_ids = [row["template_id"] for row in (*core, *extension, *excluded)]
    assert len(all_ids) == len(set(all_ids)) == 64
    assert set(all_ids) == {record.template_id for record in ledger.records}
    for filename in ("gold_annotation_template.jsonl", "oracle_annotation_template.jsonl"):
        annotations = _jsonl(files[filename])
        assert [row["template_id"] for row in annotations] == [row["template_id"] for row in core]
        assert len(annotations) == 53


def test_gold_and_oracle_are_empty_unbound_authoring_worksheets(
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    source = {record.template_id: record for record in ledger.records}
    for filename in ("gold_annotation_template.jsonl", "oracle_annotation_template.jsonl"):
        for row in _jsonl(files[filename]):
            assert row["artifact_kind"] == "authoring_only"
            assert row["review_status"] == "pending"
            assert row["executable"] is False
            assert row["approval"] is None
            assert row["bound_question_text_sha256"] is None
            for field in (
                "dataset_release_key",
                "dataset_manifest_sha256",
                "corpus_release_key",
                "corpus_manifest_sha256",
            ):
                assert row[field] is None
            template_id = str(row["template_id"])
            assert row["candidate_record_sha256"] == source[template_id].record_sha256
            if filename.startswith("gold"):
                assert row["gold"] is None
                assert row["bound_question_text"] is None
                assert (
                    row["template_text_sha256"]
                    == hashlib.sha256(
                        source[template_id].question_text_template.encode(),
                    ).hexdigest()
                )
            else:
                for field in (
                    "evidence_disposition",
                    "structured_facts",
                    "literature_chunk_keys",
                    "source_attestation",
                ):
                    assert row[field] is None


def test_family_resolution_and_original_trusted_quota_remain_blocked(
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    readiness = json.loads(files["readiness.json"])
    assert readiness["core_family_counts"] == {
        "structured": 16,
        "literature": 16,
        "hybrid": 8,
        "unsupported": 12,
        "unresolved": 1,
    }
    assert readiness["unresolved_family_ids"] == ["UNSUP-09"]
    assert readiness["trusted_execution_ready"] is False
    assert readiness["production_dependencies_constructed"] is False
    assert readiness["quota_changed"] is False
    assert readiness["preregistered_quota"] == {
        "total_min": 60,
        "total_max": 80,
        "per_family_min": 15,
        "per_family_max": 20,
    }
    assert all(
        readiness[field] == 0
        for field in (
            "trusted_question_count",
            "gold_annotation_count",
            "oracle_annotation_count",
        )
    )
    assert {
        "family_assignment_unresolved",
        "trusted_question_quota_not_met",
        "approved_gold_missing",
        "approved_oracle_missing",
    }.issubset(readiness["blocker_codes"])
    unresolved = next(
        row
        for row in _jsonl(files["gold_annotation_template.jsonl"])
        if row["template_id"] == "UNSUP-09"
    )
    assert unresolved["family"] is None
    checksum = readiness.pop("report_sha256")
    assert checksum == canonical_json_sha256(readiness)


def test_authoring_documents_are_not_executable_question_or_oracle_contracts(
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    with pytest.raises(ValidationError):
        QuestionManifest.model_validate_json(files["ledger.json"])
    with pytest.raises(ValidationError):
        EvaluationQuestion.model_validate_json(files["core_candidates.jsonl"].splitlines()[0])
    with pytest.raises(ValidationError):
        EvaluationQuestion.model_validate_json(
            files["gold_annotation_template.jsonl"].splitlines()[0]
        )
    with pytest.raises(ValidationError):
        OracleEvidenceEntry.model_validate_json(
            files["oracle_annotation_template.jsonl"].splitlines()[0],
        )
    assert "question_manifest.json" not in files
    assert not {"answer_metrics.csv", "retrieval_metrics.csv", "summary.json"} & set(files)


def test_entity_and_runtime_templates_supply_no_facts_credentials_or_grants(
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    assert files["entity_bindings_template.json"] == scientific_entity_bindings_template_bytes()
    bindings = json.loads(files["entity_bindings_template.json"])
    assert len(bindings["bindings"]) == 10
    assert all(
        row["review_status"] == "pending" and row["selected_stable_key"] is None
        for row in bindings["bindings"]
    )
    authority = json.loads(files["run_authorization_template.json"])
    assert authority["ledger_sha256"] == ledger.manifest_sha256
    assert authority["executable"] is False
    assert authority["credentials_must_not_be_written_here"] is True
    for key in (
        "provider",
        "model_id",
        "exact_revision",
        "credential_reference",
        "egress_policy",
        "maximum_cost",
        "dataset_release_binding",
        "corpus_release_binding",
        "reviewer_key",
        "reviewed_at_utc",
        "approved_question_manifest_sha256",
        "approved_oracle_manifest_sha256",
    ):
        assert authority[key] is None


def test_package_manifest_and_outputs_are_deterministic(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
) -> None:
    files = authoring_package_files(ledger)
    assert files == authoring_package_files(ledger)
    manifest = json.loads(files["package_manifest.json"])
    checksum = manifest.pop("manifest_sha256")
    assert checksum == canonical_json_sha256(manifest)
    assert manifest["benchmark_results_created"] is False
    assert manifest["executable"] is False
    assert manifest["files"] == {
        name: hashlib.sha256(raw).hexdigest()
        for name, raw in files.items()
        if name != "package_manifest.json"
    }
    first, second = tmp_path / "packet-one", tmp_path / "packet-two"
    write_authoring_package(ledger, first)
    write_authoring_package(ledger, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == files
    assert {p.name: p.read_bytes() for p in second.iterdir()} == files
    assert verify_authoring_package(first) == ledger


def test_existing_outputs_are_never_overwritten(
    tmp_path: Path, ledger: AuthoringReviewLedger
) -> None:
    target = tmp_path / "packet"
    write_authoring_package(ledger, target)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(AuthoringReviewError, match="already exists"):
        write_authoring_package(ledger, target)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    existing = tmp_path / "keep.txt"
    existing.write_text("user-owned", encoding="utf-8")
    with pytest.raises(AuthoringReviewError, match="already exists"):
        write_authoring_package(ledger, existing)
    assert existing.read_text(encoding="utf-8") == "user-owned"


@pytest.mark.parametrize(
    "filename",
    [
        "core_candidates.jsonl",
        "gold_annotation_template.csv",
        "QUESTION_REVIEW.cn.md",
        "package_manifest.json",
        "ledger.json",
    ],
)
def test_tampered_projection_is_rejected(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
    filename: str,
) -> None:
    target = tmp_path / "packet"
    write_authoring_package(ledger, target)
    path = target / filename
    path.write_bytes(path.read_bytes() + b"unreviewed edit\n")
    with pytest.raises(AuthoringReviewError, match="integrity"):
        verify_authoring_package(target)


@pytest.mark.parametrize("change", ["missing", "extra", "directory"])
def test_incomplete_or_extra_package_members_are_rejected(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
    change: str,
) -> None:
    target = tmp_path / "packet"
    write_authoring_package(ledger, target)
    if change == "missing":
        (target / "package_manifest.json").unlink()
    elif change == "extra":
        (target / "extra.json").write_bytes(b"{}\n")
    else:
        (target / "core_candidates.jsonl").unlink()
        (target / "core_candidates.jsonl").mkdir()
    with pytest.raises(AuthoringReviewError, match="integrity"):
        verify_authoring_package(target)


@pytest.mark.parametrize("member", [None, "ledger.json", "gold_annotation_template.csv"])
def test_package_and_file_symlinks_are_rejected(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
    member: str | None,
) -> None:
    target = tmp_path / "packet"
    write_authoring_package(ledger, target)
    if member is None:
        link = tmp_path / "linked-packet"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(AuthoringReviewError, match="symlink"):
            verify_authoring_package(link)
        with pytest.raises(AuthoringReviewError, match="already exists"):
            write_authoring_package(ledger, link)
    else:
        member_path = target / member
        outside = tmp_path / "same-bytes"
        outside.write_bytes(member_path.read_bytes())
        member_path.unlink()
        member_path.symlink_to(outside)
        with pytest.raises(AuthoringReviewError, match="integrity"):
            verify_authoring_package(target)


def test_rehashed_forged_in_memory_ledger_fails_before_creating_output(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
) -> None:
    record_payload = ledger.records[0].model_dump(mode="python", exclude={"record_sha256"})
    record_payload["executable"] = True
    record_payload["record_sha256"] = canonical_json_sha256(record_payload)
    fake_record = AuthoringReviewRecord.model_construct(**record_payload)
    payload = ledger.model_dump(mode="python", exclude={"manifest_sha256"})
    payload["records"] = (fake_record, *ledger.records[1:])
    payload["manifest_sha256"] = canonical_json_sha256(payload)
    forged = ledger.model_copy(update=payload)
    target = tmp_path / "must-not-exist"
    with pytest.raises(AuthoringReviewError, match="revalidation"):
        write_authoring_package(forged, target)
    assert not target.exists()
    with pytest.raises(AuthoringReviewError, match="revalidation"):
        render_reviewed_questions(forged)


@pytest.mark.parametrize(
    "wording",
    [
        '=HYPERLINK("https://example.invalid","test")',
        "+1+1",
        "-1+1",
        "@SUM(1,2)",
        " \t=1+1",
        "\r\n=1+1",
    ],
)
def test_csv_formula_payloads_are_escaped_without_altering_exact_json_source(
    tmp_path: Path,
    wording: str,
) -> None:
    original, incremental = _synthetic_sheets()
    original["简版审批"]["C9"] = wording
    incremental["旧审阅已对照"]["C6"] = wording
    source = _load(tmp_path, original, incremental)
    # XML end-of-line normalization precedes the ledger; the exporter must retain
    # the exact imported text, not the test writer's pre-serialization CRLF form.
    imported_wording = source.records[0].original_question_zh
    files = authoring_package_files(source)
    for filename in ("gold_annotation_template.csv", "oracle_annotation_template.csv"):
        rows = list(csv.reader(io.StringIO(files[filename].decode("utf-8-sig"))))
        assert len(rows) == 54
        assert rows[1][0] == "HOST-S-01"
        assert rows[1][1] == "'" + imported_wording
        assert rows[1][2:] == [""] * 5
    assert _jsonl(files["core_candidates.jsonl"])[0]["original_question_zh"] == imported_wording


def test_chinese_report_is_derived_from_confirmed_wording_and_keeps_boundaries(
    ledger: AuthoringReviewLedger,
) -> None:
    text = render_reviewed_questions(ledger)
    assert authoring_package_files(ledger)["QUESTION_REVIEW.cn.md"] == text.encode("utf-8")
    assert "关联查询，分组待确定（1题）" in text
    assert "独立扩展（不进入核心 S0–S6）" in text
    assert "不用再审一遍" in text
    for record in ledger.records:
        if record.disposition == "excluded":
            assert record.template_id in text
            assert record.original_question_zh not in text
        else:
            assert (record.proposed_question_zh or record.original_question_zh) in text
    assert json.loads(authoring_package_files(ledger)["ledger.json"]) == json.loads(
        canonical_json_bytes(ledger),
    )


def test_missing_output_parent_fails_without_creating_directories(
    tmp_path: Path,
    ledger: AuthoringReviewLedger,
) -> None:
    parent = tmp_path / "missing-parent"
    with pytest.raises(AuthoringReviewError, match="parent does not exist"):
        write_authoring_package(ledger, parent / "packet")
    assert not parent.exists()
