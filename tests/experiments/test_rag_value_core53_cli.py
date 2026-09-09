"""Local CLI checks; all approved annotations in these tests are synthetic fixtures only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import sqlalchemy

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    require_trusted_question_set,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    HumanApproval,
    HybridGold,
    LiteratureGold,
    OracleEvidenceManifest,
    QuestionManifest,
    StructuredGold,
    build_oracle_entry,
    build_oracle_manifest,
)
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    Core53QuestionScope,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes
from scripts import check_rag_value_core53 as cli
from tests.experiments import test_rag_value_scoped_admission as synthetic_inputs

entities = synthetic_inputs.entities
scope = synthetic_inputs.scope
questions = synthetic_inputs.questions


def _write_json(path: Path, value: object) -> str:
    raw = canonical_json_bytes(value) + b"\n"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _question_arguments(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
) -> list[str]:
    entity_file = tmp_path / "tests-only-entities.json"
    question_file = tmp_path / "tests-only-questions.json"
    return [
        "--entities", str(entity_file), "--entities-sha256",
        _write_json(entity_file, scope.entities),
        "--questions", str(question_file), "--questions-sha256",
        _write_json(question_file, questions),
        "--revision", "classified-v1",
    ]


def _oracle(questions: QuestionManifest) -> OracleEvidenceManifest:
    entries = []
    for question in questions.questions:
        gold = question.gold
        structured = gold.structured if isinstance(gold, HybridGold) else (
            gold if isinstance(gold, StructuredGold) else None
        )
        literature = gold.literature if isinstance(gold, HybridGold) else (
            gold if isinstance(gold, LiteratureGold) else None
        )
        chunks = tuple(sorted(
            group.required_chunk_key for group in literature.evidence_groups
        )) if literature is not None else ()
        entries.append(build_oracle_entry(
            question_id=question.question_id,
            question_text_sha256=question.question_text_sha256,
            review_status="approved",
            approval=HumanApproval(
                reviewer_key="tests-only-synthetic-oracle-reviewer",
                reviewed_at="2099-01-01T00:00:00Z",
                attestation=(
                    "I independently reviewed this annotation and approve it for this benchmark."
                ),
            ),
            evidence_disposition=(
                "evidence_supplied" if structured is not None or chunks
                else "no_supporting_evidence"
            ),
            structured_facts=structured,
            literature_chunk_keys=chunks,
            dataset_release_key=questions.dataset_release_key,
            dataset_manifest_sha256=questions.dataset_manifest_sha256,
            corpus_release_key=questions.corpus_release_key,
            corpus_manifest_sha256=questions.corpus_manifest_sha256,
            source_attestation=(
                "Evidence was selected manually and not generated from model or retriever output."
            ),
        ))
    return build_oracle_manifest(entries)


def _assert_runtime_blocked(output: str) -> None:
    assert "Runtime ready: false" in output
    assert "phase3_gate_issued_execution_evidence_not_implemented" in output
    assert (
        "No database, retrieval, provider, model, network, or release execution occurred." in output
    )


def test_default_only_checks_authoring_and_preserves_package(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {p.name: p.read_bytes() for p in cli.DEFAULT_PACKAGE.iterdir()}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("default CLI must not construct or call runtime dependencies")

    monkeypatch.setattr(cli, "_load_pinned", forbidden)
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert "53 questions" in output
    assert "Required shared entity selections: 9" in output
    assert "approved entities, question Gold, and Oracle not supplied" in output
    _assert_runtime_blocked(output)
    assert before == {p.name: p.read_bytes() for p in cli.DEFAULT_PACKAGE.iterdir()}


@pytest.mark.parametrize("arguments", [
    ["--entities", "private-input"],
    ["--entities-sha256", "a" * 64],
    ["--questions", "private-input"],
    ["--questions-sha256", "a" * 64],
    ["--oracle", "private-input"],
    ["--oracle-sha256", "a" * 64],
    ["--entities", "private-input", "--entities-sha256", "a" * 64],
    ["--oracle", "private-input", "--oracle-sha256", "a" * 64],
    ["--unknown", "private-input"],
    ["--quest", "private-input"],
])
def test_partial_or_unknown_arguments_fail_without_echoing_inputs(
    arguments: list[str], capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(arguments) == 2
    captured = capsys.readouterr()
    assert "private-input" not in captured.out + captured.err
    assert "BLOCKED" in captured.out
    _assert_runtime_blocked(captured.out)


def test_help_is_read_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    assert "--questions-sha256" in capsys.readouterr().out


def test_existing_pending_worksheet_is_not_an_approved_manifest(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _question_arguments(tmp_path, scope, questions)
    pending = cli.DEFAULT_PACKAGE / "gold_annotation_template.jsonl"
    arguments[5] = str(pending)
    arguments[7] = hashlib.sha256(pending.read_bytes()).hexdigest()
    assert cli.main(arguments) == 2
    output = capsys.readouterr().out
    assert "approval validation failed" in output
    assert "Annotations: READY" not in output


def test_approved_questions_still_need_separately_approved_oracle(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(_question_arguments(tmp_path, scope, questions)) == 2
    output = capsys.readouterr().out
    assert "question/Gold contracts validated: 53 questions" in output
    assert "separately approved Oracle not supplied" in output
    _assert_runtime_blocked(output)


def test_complete_synthetic_annotations_do_not_grant_execution_authority(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _question_arguments(tmp_path, scope, questions)
    oracle_file = tmp_path / "tests-only-oracle.json"
    arguments += ["--oracle", str(oracle_file), "--oracle-sha256",
                  _write_json(oracle_file, _oracle(questions))]

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("complete annotations must not construct runtime dependencies")

    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert cli.main(arguments) == 0
    output = capsys.readouterr().out
    assert "Annotations: READY" in output
    assert "do not independently verify human identities or scientific truth" in output
    _assert_runtime_blocked(output)
    # The amended CLI did not loosen the default global 60-80 question gate.
    with pytest.raises(AnnotationError, match="60-80"):
        require_trusted_question_set(questions)


def test_oracle_coverage_is_checked_not_just_its_file_hash(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _question_arguments(tmp_path, scope, questions)
    incomplete = build_oracle_manifest(_oracle(questions).entries[:-1])
    oracle_file = tmp_path / "tests-only-incomplete-oracle.json"
    arguments += ["--oracle", str(oracle_file), "--oracle-sha256",
                  _write_json(oracle_file, incomplete)]
    assert cli.main(arguments) == 2
    assert "Annotations: READY" not in capsys.readouterr().out


@pytest.mark.parametrize("corruption", ["checksum", "noncanonical", "malformed", "entity"])
def test_pinned_manifest_rejects_corruption_without_exposing_contents(
    tmp_path: Path, scope: Core53QuestionScope, questions: QuestionManifest,
    corruption: str, capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _question_arguments(tmp_path, scope, questions)
    path = Path(arguments[5])
    if corruption == "checksum":
        arguments[7] = "0" * 64
    elif corruption == "noncanonical":
        path.write_text(json.dumps(questions.model_dump(mode="json"), indent=2))
        arguments[7] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif corruption == "malformed":
        path.write_text('{"secret-marker":"never-print-this"}')
        arguments[7] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        entity_path = Path(arguments[1])
        entity_path.write_text('{"review_status":"pending","secret":"never-print-this"}')
        arguments[3] = hashlib.sha256(entity_path.read_bytes()).hexdigest()
    assert cli.main(arguments) == 2
    captured = capsys.readouterr()
    assert "never-print-this" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    _assert_runtime_blocked(captured.out)


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "oversized"])
def test_bounded_reader_rejects_nonregular_or_oversized_input(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "input.json"
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(b"{}\n")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        with path.open("wb") as handle:
            handle.truncate(cli.MAX_INPUT_BYTES + 1)
    with pytest.raises((cli.InputCheckError, OSError)):
        cli._read_bounded(path)


def test_bounded_reader_accepts_exact_limit_and_rejects_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"abc")
    monkeypatch.setattr(cli, "MAX_INPUT_BYTES", 3)
    assert cli._read_bounded(path) == b"abc"
    original_fstat = os.fstat

    def grow_after_stat(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        path.write_bytes(b"abcd")
        return metadata

    monkeypatch.setattr(cli.os, "fstat", grow_after_stat)
    with pytest.raises(cli.InputCheckError, match="byte limit"):
        cli._read_bounded(path)


def test_tampered_package_fails_before_annotation_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "package"
    shutil.copytree(cli.DEFAULT_PACKAGE, package)
    (package / "README.md").write_text("Changed, tests only.")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid package must fail before annotation loading")

    monkeypatch.setattr(cli, "_load_pinned", forbidden)
    assert cli.main(["--package", str(package)]) == 2
    _assert_runtime_blocked(capsys.readouterr().out)


def test_package_verification_uses_bounded_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("authoring verification must not use an unbounded read_bytes call")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert len(cli._verify_package(cli.DEFAULT_PACKAGE)) == 53
