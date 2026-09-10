"""Synthetic source-snapshot checks; no database, model, or replay imports."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import audit_rag_value_source_experiment as audit


@pytest.fixture
def snapshot_tree(tmp_path: Path):
    run = tmp_path.resolve() / "run"
    snapshot = run / "code_snapshot" / "src"
    original = tmp_path.resolve() / "retired-original-repository"
    relative_paths = (
        "src/eve_relation_rag/__init__.py",
        "src/eve_relation_rag/experiments/rag_value_ablation/source_report_queries.py",
        "src/eve_relation_rag/literature/hashing.py",
    )
    implementation, manifest = [], []
    for index, relative in enumerate(relative_paths):
        path = run / "code_snapshot" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Validation must never execute these files, including the package initializer.
        content = f"raise RuntimeError('snapshot validation executed source {index}')\n".encode()
        path.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        implementation.append({"path": str(original / relative), "sha256": sha})
        manifest.append({"path": str(path), "sha256": sha})
    return run, snapshot, {"implementation_files": implementation}, manifest, original


def manifest_receipt(run: Path, entries: object) -> dict[str, object]:
    path = run / "code_snapshot_manifest.json"
    raw = json.dumps(entries, sort_keys=True).encode()
    path.write_bytes(raw)
    return {"source_snapshot": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}}


def test_old_absolute_implementation_paths_validate_without_original_repository(snapshot_tree):
    run, snapshot, config, _, original = snapshot_tree
    previous_path = list(audit.sys.path)
    previous_modules = dict(audit.sys.modules)
    assert not original.exists()
    assert audit.verify_source_snapshot(run, config, {}) == snapshot
    assert audit.sys.path == previous_path
    assert dict(audit.sys.modules) == previous_modules
    assert not original.exists()


def test_new_snapshot_manifest_binds_to_configuration_and_bytes(snapshot_tree):
    run, snapshot, config, manifest, _ = snapshot_tree
    receipt = manifest_receipt(run, manifest)
    assert audit.verify_source_snapshot(run, config, receipt) == snapshot


@pytest.mark.parametrize("new_manifest", [False, True])
def test_changed_snapshot_source_is_rejected(snapshot_tree, new_manifest):
    run, _, config, manifest, _ = snapshot_tree
    receipt = manifest_receipt(run, manifest) if new_manifest else {}
    Path(manifest[1]["path"]).write_text("raise RuntimeError('tampered')\n")
    with pytest.raises(ValueError, match="snapshot_file_sha256_mismatch"):
        audit.verify_source_snapshot(run, config, receipt)


def test_missing_snapshot_source_is_rejected(snapshot_tree):
    run, _, config, manifest, _ = snapshot_tree
    Path(manifest[1]["path"]).unlink()
    with pytest.raises(ValueError, match="snapshot_file_missing"):
        audit.verify_source_snapshot(run, config, {})


@pytest.mark.parametrize("missing_index", [0, 1])
def test_required_replay_files_cannot_be_omitted_from_both_pins_and_tree(
    snapshot_tree, missing_index,
):
    run, _, config, manifest, _ = snapshot_tree
    Path(manifest[missing_index]["path"]).unlink()
    config["implementation_files"].pop(missing_index)
    with pytest.raises(ValueError):
        audit.verify_source_snapshot(run, config, {})


@pytest.mark.parametrize("relative", [
    "eve_relation_rag/unpinned.py",
    "eve_relation_rag/unpinned.pyc",
    "eve_relation_rag/__pycache__/unpinned.cpython-312.pyc",
    "eve_relation_rag/unpinned.cpython-312-darwin.so",
    "shadowing_module.py",
])
def test_extra_importable_snapshot_file_is_rejected(snapshot_tree, relative):
    run, snapshot, config, _, _ = snapshot_tree
    extra = snapshot / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"untrusted import artifact")
    with pytest.raises(ValueError, match="unexpected_snapshot_import_file"):
        audit.verify_source_snapshot(run, config, {})


@pytest.mark.parametrize("kind", ["file", "package_directory", "snapshot_directory"])
def test_snapshot_symlinks_are_rejected_even_when_bytes_match(snapshot_tree, kind):
    run, snapshot, config, manifest, _ = snapshot_tree
    if kind == "file":
        source = Path(manifest[0]["path"])
    elif kind == "package_directory":
        source = snapshot / "eve_relation_rag"
    else:
        source = snapshot.parent
    target = run.parent / "external-target"
    source.rename(target)
    source.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ValueError, match="snapshot_symlink_not_allowed"):
        audit.verify_source_snapshot(run, config, {})


@pytest.mark.parametrize("entries", [{}, [{"path": "missing-sha"}], ["not-an-entry"]])
def test_invalid_new_manifest_shape_is_rejected(snapshot_tree, entries):
    run, _, config, _, _ = snapshot_tree
    with pytest.raises(ValueError, match="source_snapshot_manifest_invalid"):
        audit.verify_source_snapshot(run, config, manifest_receipt(run, entries))


@pytest.mark.parametrize("change", ["missing_entry", "different_hash"])
def test_manifest_must_match_config_not_only_its_own_receipt(snapshot_tree, change):
    run, _, config, manifest, _ = snapshot_tree
    if change == "missing_entry":
        manifest = manifest[:-1]
    else:
        manifest[0] = {**manifest[0], "sha256": "0" * 64}
    receipt = manifest_receipt(run, manifest)
    with pytest.raises(ValueError, match="snapshot_manifest_implementation_mismatch"):
        audit.verify_source_snapshot(run, config, receipt)


def test_manifest_byte_hash_is_checked_before_using_entries(snapshot_tree):
    run, _, config, manifest, _ = snapshot_tree
    receipt = manifest_receipt(run, manifest)
    with (run / "code_snapshot_manifest.json").open("ab") as handle:
        handle.write(b"\n")
    with pytest.raises(ValueError, match="pinned_file_mismatch"):
        audit.verify_source_snapshot(run, config, receipt)


@pytest.mark.parametrize("module_name", ["eve_relation_rag", "eve_relation_rag.cached_child"])
def test_preexisting_replay_modules_reject_without_clearing_cache(
    snapshot_tree, monkeypatch, module_name,
):
    _, snapshot, _, _, _ = snapshot_tree
    cached, unrelated = object(), object()
    modules = {module_name: cached, "unrelated": unrelated}
    isolated_sys = SimpleNamespace(modules=modules, path=["old-path"], dont_write_bytecode=False)
    monkeypatch.setattr(audit, "sys", isolated_sys)
    invalidate = Mock()
    monkeypatch.setattr(importlib, "invalidate_caches", invalidate)
    with pytest.raises(ValueError, match="replay_modules_already_loaded_use_fresh_process"):
        audit.prepare_replay_import(snapshot)
    assert modules == {module_name: cached, "unrelated": unrelated}
    assert isolated_sys.path == ["old-path"]
    assert isolated_sys.dont_write_bytecode is False
    invalidate.assert_not_called()


def test_clean_import_preparation_sets_verified_path_without_importing_snapshot(
    snapshot_tree, monkeypatch,
):
    run, snapshot, config, _, _ = snapshot_tree
    assert audit.verify_source_snapshot(run, config, {}) == snapshot
    unrelated = object()
    modules = {"eve_relation_rag_unrelated": unrelated}
    isolated_sys = SimpleNamespace(modules=modules, path=["old-path"], dont_write_bytecode=False)
    monkeypatch.setattr(audit, "sys", isolated_sys)
    invalidate = Mock()
    monkeypatch.setattr(importlib, "invalidate_caches", invalidate)
    assert audit.prepare_replay_import(snapshot) is None
    assert isolated_sys.path == [str(snapshot), "old-path"]
    assert isolated_sys.dont_write_bytecode is True
    assert modules == {"eve_relation_rag_unrelated": unrelated}
    invalidate.assert_called_once_with()
