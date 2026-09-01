from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from eve_relation_rag.experiments.embedding_ablation.artifacts import (
    ArtifactVerificationError,
    is_verified_artifact,
    verify_model_artifact,
)
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    ArtifactFileRecord,
    ModelArtifactManifest,
    ModelRepresentationContract,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes


def test_artifact_verifier_binds_model_revision_dimension_and_every_file(tmp_path: Path) -> None:
    model, manifest_path, approved_sha = _artifact(tmp_path)

    verified = verify_model_artifact(
        model,
        manifest_path,
        approved_sha,
        expected_model_id="example/embedding-model",
        expected_revision="a" * 40,
        expected_task_kind="embedding",
        expected_dimension=384,
    )

    assert is_verified_artifact(verified)
    assert verified.artifact_manifest_sha256 == approved_sha
    assert verified.model_size_bytes == (model / "config.json").stat().st_size

    (model / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="enumerate every"):
        verify_model_artifact(model, manifest_path, approved_sha)


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ({"expected_model_id": "wrong/model"}, "model ID"),
        ({"expected_revision": "b" * 40}, "revision"),
        ({"expected_task_kind": "reranker"}, "task kind"),
        ({"expected_dimension": 768}, "dimension"),
    ],
)
def test_wrong_model_revision_task_or_dimension_is_rejected(
    tmp_path: Path,
    expected: dict[str, Any],
    message: str,
) -> None:
    model, manifest_path, approved_sha = _artifact(tmp_path)

    with pytest.raises(ArtifactVerificationError, match=message):
        verify_model_artifact(model, manifest_path, approved_sha, **expected)


def test_missing_and_checksum_mismatched_artifacts_are_rejected(tmp_path: Path) -> None:
    model, manifest_path, approved_sha = _artifact(tmp_path)
    (model / "config.json").unlink()

    with pytest.raises(ArtifactVerificationError, match="missing"):
        verify_model_artifact(model, manifest_path, approved_sha)

    model, manifest_path, _approved_sha = _artifact(tmp_path / "second")
    with pytest.raises(ArtifactVerificationError, match="not approved"):
        verify_model_artifact(model, manifest_path, "f" * 64)


def test_artifact_symlink_is_rejected(tmp_path: Path) -> None:
    model, manifest_path, approved_sha = _artifact(tmp_path)
    target = model / "config.json"
    target.rename(model / "real-config.json")
    target.symlink_to(model / "real-config.json")

    with pytest.raises(ArtifactVerificationError, match="symbolic link"):
        verify_model_artifact(model, manifest_path, approved_sha)


def test_noncanonical_artifact_manifest_is_rejected_even_when_checksum_is_approved(
    tmp_path: Path,
) -> None:
    model, manifest_path, _approved_sha = _artifact(tmp_path)
    noncanonical = b"\n" + manifest_path.read_bytes()
    manifest_path.write_bytes(noncanonical)

    with pytest.raises(ArtifactVerificationError, match="canonical"):
        verify_model_artifact(
            model,
            manifest_path,
            hashlib.sha256(noncanonical).hexdigest(),
        )


def _artifact(tmp_path: Path) -> tuple[Path, Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / "model"
    model.mkdir()
    artifact = model / "config.json"
    artifact.write_bytes(b'{"architectures":["BertModel"]}\n')
    representation = ModelRepresentationContract(
        task_kind="embedding",
        dimension=384,
        pooling="cls",
        normalization="l2",
        similarity="cosine",
        query_format="query: {query}",
        passage_format="{passage}",
        max_sequence_length=512,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )
    manifest = ModelArtifactManifest(
        manifest_schema_version="embedding-ablation-model-artifact-v1",
        model_key="embedding:test:example",
        model_id="example/embedding-model",
        exact_revision="a" * 40,
        license="MIT",
        license_review_status="approved",
        representation=representation,
        runtime_key="runtime:test:transformers",
        local_files_only=True,
        trust_remote_code=False,
        files=(
            ArtifactFileRecord(
                relative_path="config.json",
                byte_size=artifact.stat().st_size,
                sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            ),
        ),
    )
    raw = canonical_json_bytes(manifest)
    manifest_path = tmp_path / "artifact-manifest.json"
    manifest_path.write_bytes(raw)
    return model, manifest_path, hashlib.sha256(raw).hexdigest()
