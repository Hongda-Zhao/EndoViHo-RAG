from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from eve_relation_rag.experiments.embedding_ablation.annotations import (
    AnnotationIOError,
    load_annotation_manifest,
    load_legacy_benchmark,
    migrate_legacy_benchmark_to_pending,
    write_new_annotation_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "tests" / "fixtures" / "literature" / "synthetic_benchmark.json"


def test_legacy_questions_are_preserved_but_forced_back_to_pending_review(tmp_path: Path) -> None:
    legacy_sha256 = hashlib.sha256(LEGACY.read_bytes()).hexdigest()
    legacy = load_legacy_benchmark(LEGACY, legacy_sha256)

    migrated = migrate_legacy_benchmark_to_pending(legacy)

    assert migrated.question_count == legacy.question_count
    assert migrated.approved_question_count == 0
    migrated_by_id = {question.question_id: question for question in migrated.questions}
    for old in legacy.questions:
        new = migrated_by_id[old.question_key]
        assert new.question_id == old.question_key
        assert new.question == old.question
        assert new.anchors == old.anchors
        assert new.required_chunk_keys == old.relevant_chunk_keys
        assert new.category is None
        assert new.review_status == "pending"
        assert new.acceptable_alternative_chunk_keys == new.excluded_chunk_keys == ()

    output = tmp_path / "pending-annotations.json"
    file_sha256 = write_new_annotation_manifest(output, migrated)
    assert load_annotation_manifest(output, file_sha256) == migrated

    noncanonical = tmp_path / "noncanonical-annotations.json"
    noncanonical.write_bytes(b"\n" + output.read_bytes())
    with pytest.raises(AnnotationIOError, match="canonical"):
        load_annotation_manifest(
            noncanonical,
            hashlib.sha256(noncanonical.read_bytes()).hexdigest(),
        )


def test_standalone_cli_cold_help_does_not_import_model_runtimes() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; from eve_relation_rag.experiments.embedding_ablation.cli import app; "
            "assert app; assert 'transformers' not in sys.modules; "
            "assert 'sentence_transformers' not in sys.modules; assert 'torch' not in sys.modules",
        ),
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == completed.stderr == ""
