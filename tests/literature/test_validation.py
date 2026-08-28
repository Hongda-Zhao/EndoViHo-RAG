from __future__ import annotations

import pytest
from pydantic import ValidationError

from eve_relation_rag.literature.contracts import EMBEDDING_MODEL_KEY
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.providers import (
    DeterministicFakeEmbeddingProvider,
)
from eve_relation_rag.literature.validation import (
    RebuildValidationReport,
    _provider_kind,
)


def _report_payload() -> dict[str, object]:
    return {
        "validation_schema_version": "corpus-rebuild-validation-v2",
        "corpus_release_key": "corpus:endoviho-rag:v0:20990101:001",
        "manifest_sha256": "a" * 64,
        "policy_graph_sha256": "b" * 64,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "model_artifact_manifest_sha256": "c" * 64,
        "anchor_manifest_sha256": "d" * 64,
        "provider_kind": "unverified",
        "passed": True,
        "findings": (),
        "document_count": 1,
        "chunk_count": 1,
        "embedding_count": 1,
        "anchor_count": 1,
        "document_keys_sha256": "e" * 64,
        "document_rebuild_sha256": "1" * 64,
        "chunk_rebuild_sha256": "2" * 64,
        "embedding_rebuild_sha256": "3" * 64,
        "anchor_rebuild_sha256": "4" * 64,
    }


def test_rebuild_report_hash_covers_every_field_except_itself() -> None:
    payload = _report_payload()
    report = RebuildValidationReport(
        **payload,
        rebuild_sha256=canonical_json_sha256(payload),
    )
    assert report.model_artifact_manifest_sha256 == "c" * 64
    assert report.anchor_manifest_sha256 == "d" * 64
    assert report.document_rebuild_sha256 == "1" * 64

    tampered = {**payload, "provider_kind": "local_bge"}
    with pytest.raises(ValidationError, match="complete report"):
        RebuildValidationReport(
            **tampered,
            rebuild_sha256=report.rebuild_sha256,
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"passed": False}, "absence of validation findings"),
        ({"embedding_count": 0}, "embedding_count must equal chunk_count"),
    ],
)
def test_rebuild_report_rejects_internally_inconsistent_state(
    updates: dict[str, object], message: str
) -> None:
    payload = {**_report_payload(), **updates}
    with pytest.raises(ValidationError, match=message):
        RebuildValidationReport(
            **payload,
            rebuild_sha256=canonical_json_sha256(payload),
        )


def test_only_concrete_provider_types_receive_privileged_kind_labels() -> None:
    class LookalikeProvider(DeterministicFakeEmbeddingProvider):
        pass

    assert _provider_kind(DeterministicFakeEmbeddingProvider()) == "deterministic_fake"
    assert _provider_kind(LookalikeProvider()) == "unverified"

    class DelegatingProvider:
        @property
        def model_key(self) -> str:
            return DeterministicFakeEmbeddingProvider().model_key

        @property
        def dimension(self) -> int:
            return 384

        @property
        def artifact_manifest_sha256(self) -> str:
            return "f" * 64

        def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return DeterministicFakeEmbeddingProvider().embed_documents(texts)

        def embed_query(self, text: str) -> tuple[float, ...]:
            return DeterministicFakeEmbeddingProvider().embed_query(text)

    assert _provider_kind(DelegatingProvider()) == "unverified"
