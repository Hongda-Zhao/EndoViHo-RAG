"""Whole-source S1 verification against synthetic material and corpus manifests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_relation_rag.experiments.rag_value_ablation import raw_context as raw
from eve_relation_rag.experiments.rag_value_ablation.contracts import build_raw_context_policy
from eve_relation_rag.literature.contracts import CorpusManifest
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256

FIXTURES = Path(__file__).parents[1] / "fixtures/literature"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    corpus_path = FIXTURES / "synthetic_corpus_manifest.json"
    corpus = CorpusManifest.model_validate_json(corpus_path.read_bytes())
    root = tmp_path / "materials"
    root.mkdir()
    export = b'{"artifact_kind":"tests_only","loci":[]}\n'
    (root / "structured.json").write_bytes(export)
    sources = [
        {
            "source_kind": "structured_export",
            "source_key": "export:tests-only",
            "relative_path": "structured.json",
            "source_sha256": hashlib.sha256(export).hexdigest(),
            "byte_size": len(export),
        }
    ]
    for doc in corpus.documents:
        destination = root / doc.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / doc.relative_path, destination)
        sources.append(
            {
                "source_kind": "document",
                "source_key": doc.expected_document_key,
                "relative_path": str(doc.relative_path),
                "source_sha256": doc.source_sha256,
                "byte_size": doc.byte_size,
            }
        )
    payload = {
        "schema_version": "rag-value-raw-material-manifest-v1",
        "dataset_release_key": "release:tests-only",
        "dataset_manifest_sha256": "d" * 64,
        "corpus_release_key": corpus.corpus_release_key,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "sources": sources,
    }
    manifest = raw.RawMaterialManifest.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "manifest_sha256": canonical_json_sha256(payload),
            }
        )
    )
    policy = build_raw_context_policy(
        source_manifest_sha256=manifest.manifest_sha256,
        structured_export_sha256=sources[0]["source_sha256"],
        document_manifest_sha256=corpus.manifest_sha256,
        final_partial_segment_allowed=False,
        separator_sha256=hashlib.sha256(b",").hexdigest(),
        tokenizer_key=raw.TOKENIZER_KEY,
        tokenizer_id=raw.MODEL_ID,
        tokenizer_revision=raw.MODEL_REVISION,
        tokenizer_artifact_manifest_sha256=b"t".hex() * 32,
        model_context_limit_tokens=32768,
        reserved_output_tokens=8192,
    )
    manifest_path, policy_path = tmp_path / "materials.json", tmp_path / "policy.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    policy_path.write_bytes(canonical_json_bytes(policy))
    files = raw.RawContextFiles(
        material_root=str(root),
        material_manifest_path=str(manifest_path),
        corpus_manifest_path=str(corpus_path),
        construction_policy_path=str(policy_path),
        model_root="tests-only",
        model_policy_path="tests-only",
        model_policy_file_sha256="a" * 64,
    )
    monkeypatch.setattr(raw, "verify_generation_assets", lambda config: "tests-only")
    monkeypatch.setattr(
        raw,
        "generation_identity",
        lambda manifest: SimpleNamespace(
            tokenizer_artifact_manifest_sha256=policy.tokenizer_artifact_manifest_sha256,
        ),
    )
    return files, manifest, policy


def load(bundle):
    files, manifest, policy = bundle
    return raw.load_raw_context(
        files,
        approved_material_sha256=manifest.manifest_sha256,
        approved_policy_sha256=policy.policy_sha256,
        approved_tokenizer_sha256=policy.tokenizer_artifact_manifest_sha256,
    )


def test_raw_context_has_complete_original_bytes_in_corpus_order(bundle):
    files, expected, _ = bundle
    manifest, _, segments = load(bundle)
    assert manifest == expected
    assert tuple(s.source_key for s in segments) == tuple(s.source_key for s in expected.sources)
    assert [s.segment_id for s in segments] == ["R1", "R2", "R3", "R4"]
    for source, segment in zip(expected.sources, segments, strict=True):
        assert (
            segment.text.encode() == (Path(files.material_root) / source.relative_path).read_bytes()
        )
        assert segment.byte_start == 0
        assert segment.byte_end == source.byte_size


@pytest.mark.parametrize("mutation", ["bytes", "missing", "symlink", "order", "policy"])
def test_raw_context_never_returns_partial_or_substituted_material(bundle, mutation):
    files, manifest, policy = bundle
    source = Path(files.material_root) / manifest.sources[1].relative_path
    if mutation == "bytes":
        source.write_text("wrong")
    elif mutation == "missing":
        source.unlink()
    elif mutation == "symlink":
        source.unlink()
        source.symlink_to(Path(files.material_root) / "structured.json")
    elif mutation == "order":
        payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
        payload["sources"][1], payload["sources"][2] = payload["sources"][2], payload["sources"][1]
        Path(files.material_manifest_path).write_bytes(
            canonical_json_bytes(
                {
                    **payload,
                    "manifest_sha256": canonical_json_sha256(payload),
                }
            )
        )
    else:
        payload = policy.model_dump(mode="json", exclude={"policy_sha256"})
        payload["final_partial_segment_allowed"] = True
        Path(files.construction_policy_path).write_bytes(
            canonical_json_bytes(
                {
                    **payload,
                    "policy_sha256": canonical_json_sha256(payload),
                }
            )
        )
    with pytest.raises((ValueError, OSError)):
        load(bundle)


def test_valid_checksums_do_not_authorize_a_different_document_order(bundle):
    files, manifest, policy = bundle
    payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    payload["sources"][1], payload["sources"][2] = payload["sources"][2], payload["sources"][1]
    changed = raw.RawMaterialManifest.model_validate_json(
        canonical_json_bytes(
            {
                **payload,
                "manifest_sha256": canonical_json_sha256(payload),
            }
        )
    )
    new_policy = build_raw_context_policy(
        **{
            **policy.model_dump(mode="python"),
            "source_manifest_sha256": changed.manifest_sha256,
        }
    )
    Path(files.material_manifest_path).write_bytes(canonical_json_bytes(changed))
    Path(files.construction_policy_path).write_bytes(canonical_json_bytes(new_policy))
    with pytest.raises(raw.RawContextError, match="corpus manifest order"):
        load((files, changed, new_policy))
