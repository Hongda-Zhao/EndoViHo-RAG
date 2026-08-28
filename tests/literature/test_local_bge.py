from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_REPOSITORY_ID,
    EMBEDDING_REVISION,
)
from eve_relation_rag.literature.local_bge import (
    LocalBgeConfigurationError,
    LocalBgeProvider,
    verify_model_artifact_manifest,
)
from eve_relation_rag.literature.validation import _provider_kind


def _write_artifact_manifest(
    tmp_path: Path,
    *,
    revision: str = EMBEDDING_REVISION,
) -> tuple[Path, Path, str]:
    model = tmp_path / "model"
    model.mkdir()
    artifact = model / "config.json"
    artifact.write_bytes(b'{"architectures":["BertModel"]}\n')
    payload = {
        "dimension": 384,
        "files": [
            {
                "byte_size": artifact.stat().st_size,
                "relative_path": "config.json",
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
        "l2_normalized": True,
        "license_key": "MIT",
        "manifest_schema_version": "embedding-artifact-manifest-v1",
        "max_sequence_tokens": 512,
        "model_key": EMBEDDING_MODEL_KEY,
        "passage_prefix": "",
        "pooling": "cls",
        "query_prefix": EMBEDDING_QUERY_PREFIX,
        "repository_id": EMBEDDING_REPOSITORY_ID,
        "revision": revision,
        "similarity": "cosine",
    }
    manifest = tmp_path / "artifact-manifest.json"
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    manifest.write_bytes(raw)
    return model, manifest, hashlib.sha256(raw).hexdigest()


def test_local_provider_refuses_missing_or_symlinked_model_directory(tmp_path: Path) -> None:
    with pytest.raises(LocalBgeConfigurationError, match="directory"):
        LocalBgeProvider(tmp_path / "missing")

    target = tmp_path / "model"
    target.mkdir()
    link = tmp_path / "model-link"
    link.symlink_to(target)
    with pytest.raises(LocalBgeConfigurationError, match="symbolic link"):
        LocalBgeProvider(link)


def test_local_provider_module_cold_import_does_not_load_model_runtime() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import eve_relation_rag.literature.local_bge; "
            "assert 'sentence_transformers' not in sys.modules; "
            "assert 'transformers' not in sys.modules",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""


def test_model_artifact_manifest_binds_exact_model_identity_and_every_file(
    tmp_path: Path,
) -> None:
    model, manifest, approved_sha256 = _write_artifact_manifest(tmp_path)

    assert verify_model_artifact_manifest(model, manifest, approved_sha256) == approved_sha256

    (model / "unlisted.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LocalBgeConfigurationError, match="enumerate every file"):
        verify_model_artifact_manifest(model, manifest, approved_sha256)


def test_local_provider_exposes_the_artifact_manifest_it_actually_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, manifest, approved_sha256 = _write_artifact_manifest(tmp_path)

    class FakeSentenceTransformer:
        max_seq_length = 512

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_embedding_dimension(self) -> int:
            return 384

    monkeypatch.setattr(
        "eve_relation_rag.literature.local_bge.importlib.import_module",
        lambda _name: SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    provider = LocalBgeProvider(
        model,
        artifact_manifest_path=manifest,
        approved_artifact_manifest_sha256=approved_sha256,
    )

    assert provider.artifact_manifest_sha256 == approved_sha256
    assert _provider_kind(provider) == "local_bge"


def test_model_artifact_manifest_rejects_wrong_revision_even_when_checksum_is_approved(
    tmp_path: Path,
) -> None:
    model, manifest, approved_sha256 = _write_artifact_manifest(
        tmp_path,
        revision="0" * 40,
    )

    with pytest.raises(LocalBgeConfigurationError, match="invalid revision"):
        verify_model_artifact_manifest(model, manifest, approved_sha256)


def test_model_artifact_manifest_rejects_unapproved_or_malformed_checksum(tmp_path: Path) -> None:
    model, manifest, _ = _write_artifact_manifest(tmp_path)

    with pytest.raises(LocalBgeConfigurationError, match="checksum is invalid"):
        verify_model_artifact_manifest(model, manifest, "not-a-sha256")
    with pytest.raises(LocalBgeConfigurationError, match="not approved"):
        verify_model_artifact_manifest(model, manifest, "0" * 64)
