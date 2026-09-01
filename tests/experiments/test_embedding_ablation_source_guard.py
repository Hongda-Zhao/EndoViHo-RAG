from __future__ import annotations

from pathlib import Path

import pytest

from eve_relation_rag.experiments.embedding_ablation.source_guard import (
    ProductionSourceGuardError,
    assert_production_sources_unchanged,
    capture_production_source_fingerprint,
)


def test_production_source_guard_detects_default_changes(tmp_path: Path) -> None:
    protected = tmp_path / "production-default.txt"
    protected.write_text("baseline\n", encoding="utf-8")
    before = capture_production_source_fingerprint(
        tmp_path,
        relative_paths=("production-default.txt",),
    )

    protected.write_text("changed\n", encoding="utf-8")
    after = capture_production_source_fingerprint(
        tmp_path,
        relative_paths=("production-default.txt",),
    )

    with pytest.raises(ProductionSourceGuardError, match="changed"):
        assert_production_sources_unchanged(before, after)


def test_current_experiment_sources_are_outside_protected_production_paths() -> None:
    root = Path(__file__).resolve().parents[2]

    before = capture_production_source_fingerprint(root)
    after = capture_production_source_fingerprint(root)

    assert before == after
    assert all("experiments/embedding_ablation" not in path for path in before.file_sha256)
