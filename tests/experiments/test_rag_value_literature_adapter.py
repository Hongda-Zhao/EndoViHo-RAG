"""S2/S3 branch isolation and snapshot provenance; all identities below are tests-only."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from eve_relation_rag.experiments.rag_value_ablation import literature_adapter as adapter
from eve_relation_rag.experiments.rag_value_ablation.systems import (
    build_system_definitions,
    validate_evidence_for_system,
)
from tests.experiments.test_embedding_ablation_retrieval import KEY_A, KEY_B, _snapshot
from tests.experiments.test_rag_value_systems import _evidence_pack, _generation_identity
from tests.literature.test_local_bge import _write_artifact_manifest


def test_s2_constructs_only_original_fts_and_hydrates_in_rank_order(monkeypatch):
    calls = []
    published = SimpleNamespace(snapshot=_snapshot())
    monkeypatch.setattr(
        adapter,
        "PostgresFtsCandidateProvider",
        lambda *args: SimpleNamespace(
            rank=lambda *args, **kwargs: (KEY_B, KEY_A),
        ),
    )
    monkeypatch.setattr(adapter, "LiteratureRepository", lambda *args: calls.append("hybrid"))
    provider = adapter.LiteratureEvidenceAdapter(None, published)
    keys, citations = provider.retrieve("What does the source establish?")
    assert keys == (KEY_B, KEY_A)
    assert tuple(c.chunk_key for c in citations) == keys
    assert tuple(c.citation_id for c in citations) == ("D1", "D2")
    assert calls == []


def test_s3_uses_original_repository_and_checks_hydrated_snapshot(monkeypatch):
    bge = object.__new__(adapter.OfflineBgeQueryProvider)
    bge.artifact_manifest_sha256 = "a" * 64
    calls = []
    monkeypatch.setattr(bge, "embed_query", lambda question: calls.append("query") or (1.0,))
    published = SimpleNamespace(
        snapshot=_snapshot(),
        capability=SimpleNamespace(model_artifact_manifest_sha256="a" * 64),
    )
    chunk = published.snapshot.chunks[0]
    hit = SimpleNamespace(**chunk.model_dump(mode="python"))

    def retrieve(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(hits=(hit,))

    monkeypatch.setattr(
        adapter, "LiteratureRepository", lambda *args: SimpleNamespace(retrieve=retrieve)
    )
    provider = adapter.LiteratureEvidenceAdapter(None, published, bge=bge)
    keys, citations = provider.retrieve("What does the source establish?")
    assert keys == (chunk.chunk_key,)
    assert citations[0].text == chunk.text
    assert calls == [
        "query",
        {
            "question": "What does the source establish?",
            "query_vector": (1.0,),
            "anchors": (),
            "top_k": 100,
        },
    ]
    hit.text = "changed after published snapshot"
    with pytest.raises(adapter.LiteratureAdapterError, match="frozen snapshot"):
        provider.retrieve("What does the source establish?")


def test_scope_refusal_precedes_fts_or_bge(monkeypatch):
    calls = []
    monkeypatch.setattr(
        adapter,
        "PostgresFtsCandidateProvider",
        lambda *args: SimpleNamespace(
            rank=lambda *args, **kwargs: calls.append("retrieval"),
        ),
    )
    provider = adapter.LiteratureEvidenceAdapter(None, SimpleNamespace(snapshot=_snapshot()))
    with pytest.raises(adapter.LiteratureAdapterError, match="scope refusal"):
        provider.retrieve("Infer current infection from this record.")
    assert calls == []


def test_empty_retrieval_is_allowed_as_empty_evidence_and_never_replaced():
    systems = build_system_definitions(_generation_identity())
    empty = _evidence_pack(with_citation=False)
    for system in (systems[2], systems[3]):
        validate_evidence_for_system(system, empty)
    assert adapter.hydrate_citations(SimpleNamespace(snapshot=_snapshot()), ()) == ()


def test_bge_gate_reads_real_file_set_and_rejects_drift_before_subprocess(tmp_path, monkeypatch):
    model, manifest, digest = _write_artifact_manifest(tmp_path)
    runtime = tmp_path / "test-runtime"
    runtime.write_bytes(b"tests-only")
    files = adapter.BgeAssetFiles(
        model_root=str(model),
        artifact_manifest_path=str(manifest),
        python_executable=str(runtime),
        worker_script=str(runtime),
        python_executable_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
        worker_script_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
    )
    assert adapter.verify_bge_assets(files, digest) == digest
    provider = adapter.OfflineBgeQueryProvider(files, digest)
    (model / "config.json").write_bytes(b"changed")
    calls = []
    monkeypatch.setattr(adapter.subprocess, "run", lambda *args, **kwargs: calls.append(1))
    with pytest.raises(RuntimeError):
        provider.embed_query("What does the source say?")
    assert calls == []
