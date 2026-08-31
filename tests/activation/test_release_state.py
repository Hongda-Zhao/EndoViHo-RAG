from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from eve_relation_rag.activation.release_state import (
    _RUNTIME_IDENTITY_PATHS,
    V0_CORPUS_IMPORTER_CODE_SHA256,
    V0_CORPUS_POLICY_CODE_SHA256,
    ActivationStateError,
    CorpusPublicationEvidence,
    CorpusValidationExport,
    DatasetPublicationEvidence,
    V0ActivationArtifacts,
    V0ActivationStateManifest,
    V0RouteBenchmarkReport,
    _require_runtime_identity_unchanged,
    _require_strict_ancestor,
    build_v0_activation_state_manifest,
    validate_v0_activation_state,
)
from eve_relation_rag.hybrid.contracts import canonical_self_sha256
from eve_relation_rag.literature.hashing import canonical_json_sha256
from tests.support.m3 import build_trusted_receipt_fixture


def _seal(payload: dict[str, Any], field: str) -> dict[str, Any]:
    payload[field] = "0" * 64
    payload[field] = canonical_self_sha256(payload, field)
    return payload


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.name", "V0 Test")
    _git(root, "config", "user.email", "v0@example.invalid")


def _write_placeholder_artifacts(root: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for index, field_name in enumerate(V0ActivationArtifacts.model_fields, start=1):
        relative = Path("release/evidence") / f"{index:02d}-{field_name}.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        artifacts[field_name] = relative
    return artifacts


def test_state_rejects_raw_hash_bound_but_untyped_evidence(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    artifact_paths = _write_placeholder_artifacts(tmp_path)
    artifacts: dict[str, dict[str, object]] = {}
    for name, relative_path in artifact_paths.items():
        relative = relative_path.as_posix()
        path = tmp_path / relative_path
        raw = path.read_bytes()
        artifacts[name] = {
            "path": relative,
            "byte_size": len(raw),
            "file_sha256": hashlib.sha256(raw).hexdigest(),
        }
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "activation evidence")
    evidence_commit = _git(tmp_path, "rev-parse", "HEAD")
    state_payload = _seal(
        {
            "activation_state_schema_version": "v0-activation-state-v2",
            "product_version": "V0",
            "package_version": "0.1.0",
            "activation_evidence_commit": evidence_commit,
            "release_key": "release:endoviho-rag:v0:20260826:001",
            "corpus_release_key": "corpus:endoviho-rag:v0:20260828:001",
            "artifacts": artifacts,
        },
        "state_sha256",
    )
    state = V0ActivationStateManifest.model_validate(state_payload)
    state_path = tmp_path / "release" / "v0_activation_state.json"
    state_path.write_text(state.model_dump_json(), encoding="utf-8")
    with pytest.raises(ActivationStateError, match="must be distinct"):
        validate_v0_activation_state(
            tmp_path,
            publication_commit=evidence_commit,
        )
    publication_marker = tmp_path / "release" / "publication-marker.txt"
    publication_marker.write_text("metadata\n", encoding="utf-8")
    _git(tmp_path, "add", publication_marker.relative_to(tmp_path).as_posix())
    _git(tmp_path, "commit", "-m", "publication metadata without state")
    state_missing_commit = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ActivationStateError, match="tracked regular blob"):
        validate_v0_activation_state(
            tmp_path,
            publication_commit=state_missing_commit,
        )
    _git(tmp_path, "add", state_path.relative_to(tmp_path).as_posix())
    _git(tmp_path, "commit", "-m", "activation state")
    publication_commit = _git(tmp_path, "rev-parse", "HEAD")

    with pytest.raises(ActivationStateError, match="not valid typed evidence"):
        validate_v0_activation_state(
            tmp_path,
            publication_commit=publication_commit,
        )


def test_activation_state_builder_binds_exact_committed_evidence(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    artifact_paths = _write_placeholder_artifacts(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "activation evidence")
    evidence_commit = _git(tmp_path, "rev-parse", "HEAD")

    state = build_v0_activation_state_manifest(
        tmp_path,
        activation_evidence_commit=evidence_commit,
        release_key="release:endoviho-rag:v0:20260826:001",
        corpus_release_key="corpus:endoviho-rag:v0:20260828:001",
        artifact_paths=artifact_paths,
    )

    assert state.activation_state_schema_version == "v0-activation-state-v2"
    assert state.activation_evidence_commit == evidence_commit
    assert {
        getattr(state.artifacts, field_name).path
        for field_name in V0ActivationArtifacts.model_fields
    } == {path.as_posix() for path in artifact_paths.values()}
    assert all(
        ref.path != "release/v0_activation_state.json"
        for field_name in V0ActivationArtifacts.model_fields
        for ref in (getattr(state.artifacts, field_name),)
    )

    first_path = next(iter(artifact_paths.values()))
    (tmp_path / first_path).write_text('{"resealed":true}\n', encoding="utf-8")
    with pytest.raises(ActivationStateError, match="evidence commit blob"):
        build_v0_activation_state_manifest(
            tmp_path,
            activation_evidence_commit=evidence_commit,
            release_key="release:endoviho-rag:v0:20260826:001",
            corpus_release_key="corpus:endoviho-rag:v0:20260828:001",
            artifact_paths=artifact_paths,
        )


def test_activation_commit_boundaries_require_strict_ancestry(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("runtime\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "runtime")
    runtime_commit = _git(tmp_path, "rev-parse", "HEAD")

    marker.write_text("evidence\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "evidence")
    evidence_commit = _git(tmp_path, "rev-parse", "HEAD")
    marker.write_text("publication\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "publication")
    publication_commit = _git(tmp_path, "rev-parse", "HEAD")

    _require_strict_ancestor(
        tmp_path,
        runtime_commit,
        evidence_commit,
        label="runtime/evidence",
    )
    _require_strict_ancestor(
        tmp_path,
        evidence_commit,
        publication_commit,
        label="evidence/publication",
    )
    with pytest.raises(ActivationStateError, match="must be distinct"):
        _require_strict_ancestor(
            tmp_path,
            evidence_commit,
            evidence_commit,
            label="evidence/publication",
        )

    _git(tmp_path, "switch", "--detach", runtime_commit)
    marker.write_text("unrelated\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "unrelated")
    unrelated_commit = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ActivationStateError, match="required Git ancestry"):
        _require_strict_ancestor(
            tmp_path,
            evidence_commit,
            unrelated_commit,
            label="evidence/publication",
        )


def test_runtime_and_lock_blobs_must_match_rebuild_and_evidence_commits(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    for relative in _RUNTIME_IDENTITY_PATHS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"runtime identity: {relative}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "runtime")
    runtime_commit = _git(tmp_path, "rev-parse", "HEAD")

    evidence = tmp_path / "release" / "evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "evidence")
    evidence_commit = _git(tmp_path, "rev-parse", "HEAD")
    _require_runtime_identity_unchanged(tmp_path, runtime_commit, evidence_commit)

    (tmp_path / "uv.lock").write_text("dirty lock\n", encoding="utf-8")
    with pytest.raises(ActivationStateError, match="runtime code or lock drifted"):
        _require_runtime_identity_unchanged(tmp_path, runtime_commit, evidence_commit)

    _git(tmp_path, "add", "uv.lock")
    _git(tmp_path, "commit", "-m", "drifted publication lock")
    publication_commit = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ActivationStateError, match="runtime code or lock drifted"):
        _require_runtime_identity_unchanged(
            tmp_path,
            runtime_commit,
            evidence_commit,
            publication_commit,
        )


def test_route_report_rejects_receipt_self_reference_and_short_cohort() -> None:
    report = {
        "benchmark_report_schema_version": "v0-route-benchmark-report-v1",
        "route": "structured",
        "release_key": "release:endoviho-rag:v0:20260826:001",
        "release_manifest_sha256": "1" * 64,
        "candidate_validation_input_sha256": "9" * 64,
        "dataset_validation_request_sha256": "2" * 64,
        "dependency_graph_sha256": "3" * 64,
        "candidate_capability_sha256": "4" * 64,
        "dataset_receipt_sha256": "5" * 64,
        "cases": [
            {
                "case_ordinal": 1,
                "case_key": "benchmark:v0:one",
                "question_sha256": "6" * 64,
                "response_sha256": "7" * 64,
                "result": "passed",
            }
        ],
        "report_sha256": "8" * 64,
    }

    with pytest.raises(ValidationError):
        V0RouteBenchmarkReport.model_validate(report)


def test_publication_evidence_requires_published_status_and_self_checksum() -> None:
    dataset_payload = _seal(
        {
            "publication_evidence_schema_version": ("v0-dataset-publication-evidence-v1"),
            "release_key": "release:endoviho-rag:v0:20260826:001",
            "manifest_sha256": "1" * 64,
            "receipt_key": f"dataset-receipt:sha256:{'2' * 64}",
            "receipt_sha256": "2" * 64,
            "status": "published",
            "published_at": "2026-08-30T10:00:00Z",
            "replayed": False,
        },
        "publication_sha256",
    )
    corpus_payload = _seal(
        {
            "publication_evidence_schema_version": ("v0-corpus-publication-evidence-v1"),
            "corpus_release_key": "corpus:endoviho-rag:v0:20260829:001",
            "manifest_sha256": "3" * 64,
            "receipt_key": f"corpus-receipt:sha256:{'4' * 64}",
            "receipt_sha256": "4" * 64,
            "status": "published",
            "published_at": "2026-08-30T10:01:00Z",
            "replayed": True,
        },
        "publication_sha256",
    )

    assert (
        DatasetPublicationEvidence.model_validate_json(json.dumps(dataset_payload)).status
        == "published"
    )
    assert (
        CorpusPublicationEvidence.model_validate_json(json.dumps(corpus_payload)).status
        == "published"
    )

    dataset_payload["status"] = "validated"
    dataset_payload = _seal(dataset_payload, "publication_sha256")
    with pytest.raises(ValidationError):
        DatasetPublicationEvidence.model_validate_json(json.dumps(dataset_payload))

    corpus_payload["receipt_sha256"] = "5" * 64
    with pytest.raises(ValidationError, match="checksum"):
        CorpusPublicationEvidence.model_validate_json(json.dumps(corpus_payload))


def test_corpus_validation_export_binds_distinct_importer_and_policy_code() -> None:
    corpus_release_key = "corpus:endoviho-rag:v0:20260829:001"
    manifest_sha256 = "1" * 64
    policy_graph_sha256 = "2" * 64
    receipt = build_trusted_receipt_fixture(
        corpus_release_key=corpus_release_key,
        manifest_sha256=manifest_sha256,
        policy_graph_sha256=policy_graph_sha256,
        model_artifact_manifest_sha256="3" * 64,
        document_count=1,
        chunk_count=1,
        embedding_count=1,
        anchor_count=1,
        relevant_chunk_key=f"chunk:sha256:{'4' * 64}",
        seed="v0-corpus-export",
    )
    parameters = {
        "chunking_policy_key": "chunking:bge-small-en-v1.5:384-64-448-v2",
        "embedding_model_key": (
            "embedding:hf:BAAI-bge-small-en-v1.5@"
            "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
        ),
        "fts_policy_key": "fts:postgres16:english-weighted-v2",
        "model_artifact_manifest_sha256": "3" * 64,
        "parser_policy_key": "parser:endoviho-documents-v2",
        "retrieval_policy_key": ("retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"),
        "tokenizer_model_key": (
            "embedding:hf:BAAI-bge-small-en-v1.5@"
            "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
        ),
        "embedding_build": True,
        "policy_code_sha256": V0_CORPUS_POLICY_CODE_SHA256,
    }
    payload = _seal(
        {
            "export_schema_version": "v0-corpus-validation-export-v1",
            "exported_at": "2026-08-29T07:35:20Z",
            "corpus_release": {
                "corpus_release_key": corpus_release_key,
                "status": "validated",
                "manifest_sha256": manifest_sha256,
                "policy_graph_sha256": policy_graph_sha256,
                "manifest_document_count": 1,
            },
            "import_run": {
                "run_key": f"corpus-import:sha256:{'6' * 64}",
                "status": "succeeded",
                "manifest_sha256": manifest_sha256,
                "importer_version": "eve-literature-importer-v1",
                "code_sha256": V0_CORPUS_IMPORTER_CODE_SHA256,
                "parameters": parameters,
                "parameters_sha256": canonical_json_sha256(parameters),
                "terminal_counts": {
                    "chunk_count": 1,
                    "chunk_keys_sha256": "8" * 64,
                    "document_count": 1,
                    "document_keys_sha256": "9" * 64,
                    "imported_documents": 0,
                    "reused_documents": 1,
                    "embedding_count": 1,
                    "embeddings_sha256": "a" * 64,
                },
            },
            "receipt": receipt,
        },
        "manifest_sha256",
    )

    export = CorpusValidationExport.model_validate_json(json.dumps(payload), strict=True)
    assert export.import_run.code_sha256 == V0_CORPUS_IMPORTER_CODE_SHA256
    assert export.import_run.parameters.policy_code_sha256 == V0_CORPUS_POLICY_CODE_SHA256

    wrong_importer = json.loads(json.dumps(payload))
    wrong_importer["import_run"]["code_sha256"] = "b" * 64
    wrong_importer = _seal(wrong_importer, "manifest_sha256")
    with pytest.raises(ValidationError, match="identities do not form one receipt"):
        CorpusValidationExport.model_validate_json(json.dumps(wrong_importer), strict=True)

    wrong_policy = json.loads(json.dumps(payload))
    wrong_policy["import_run"]["parameters"]["policy_code_sha256"] = "b" * 64
    wrong_policy["import_run"]["parameters_sha256"] = canonical_json_sha256(
        wrong_policy["import_run"]["parameters"]
    )
    wrong_policy = _seal(wrong_policy, "manifest_sha256")
    with pytest.raises(ValidationError, match="identities do not form one receipt"):
        CorpusValidationExport.model_validate_json(json.dumps(wrong_policy), strict=True)

    payload["import_run"]["parameters"]["policy_code_sha256"] = "b" * 64
    payload = _seal(payload, "manifest_sha256")
    with pytest.raises(ValidationError, match="parameters checksum"):
        CorpusValidationExport.model_validate_json(json.dumps(payload), strict=True)
