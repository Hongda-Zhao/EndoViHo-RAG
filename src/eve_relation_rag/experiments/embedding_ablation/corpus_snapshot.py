"""Read-only, checksum-bound access to one published production corpus."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    Document,
    DocumentAnchor,
    DocumentChunk,
)
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    FTS_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    AnchorKey,
    BlockType,
    ChunkKey,
    CorpusReleaseKey,
    DocumentKey,
    Sha256,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import canonical_json_sha256


class CorpusSnapshotError(RuntimeError):
    """Raised when a corpus cannot be proven published, immutable, and read-only."""


class SnapshotDocument(StrictFrozenSchema):
    """One manifest-ordered source identity; title remains memory-only input data."""

    document_key: DocumentKey
    manifest_row: int = Field(ge=1)
    source_artifact_sha256: Sha256
    normalized_document_sha256: Sha256
    byte_size: int = Field(ge=1)
    title: str = Field(min_length=1)
    title_sha256: Sha256

    @model_validator(mode="after")
    def validate_title_hash(self) -> Self:
        if hashlib.sha256(self.title.encode("utf-8")).hexdigest() != self.title_sha256:
            raise ValueError("snapshot document title hash does not match")
        return self


class SnapshotChunk(StrictFrozenSchema):
    """One existing chunk; text is never included in the snapshot fingerprint payload."""

    chunk_key: ChunkKey
    document_key: DocumentKey
    block_type: BlockType
    section_path: tuple[str, ...]
    locator_sha256: Sha256
    locator_text: str = Field(min_length=1)
    locator_text_sha256: Sha256
    text: str = Field(min_length=1)
    text_sha256: Sha256

    @model_validator(mode="after")
    def validate_text_hashes(self) -> Self:
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("snapshot chunk text hash does not match")
        if (
            hashlib.sha256(self.locator_text.encode("utf-8")).hexdigest()
            != self.locator_text_sha256
        ):
            raise ValueError("snapshot locator text hash does not match")
        return self


class SnapshotAnchor(StrictFrozenSchema):
    """One existing curated anchor mapped to its immutable document."""

    anchor_key: AnchorKey
    document_key: DocumentKey
    anchor_sha256: Sha256


class CorpusSnapshot(StrictFrozenSchema):
    """In-memory content plus a text-free, deterministic corpus fingerprint."""

    snapshot_schema_version: Literal["embedding-ablation-corpus-snapshot-v1"]
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    policy_graph_sha256: Sha256
    fts_policy_key: Literal["fts:postgres16:english-weighted-v2"] = FTS_POLICY_KEY
    retrieval_policy_key: Literal[
        "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
    ] = RETRIEVAL_POLICY_KEY
    anchor_policy_key: Literal["anchor:endoviho-curated-retrieval-v2"] = ANCHOR_POLICY_KEY
    validation_receipt_key: str = Field(min_length=1, max_length=255)
    validation_receipt_sha256: Sha256
    document_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    anchor_count: int = Field(ge=0)
    documents: tuple[SnapshotDocument, ...] = Field(min_length=1)
    chunks: tuple[SnapshotChunk, ...] = Field(min_length=1)
    anchors: tuple[SnapshotAnchor, ...] = ()
    corpus_fingerprint_sha256: Sha256

    @field_validator("documents")
    @classmethod
    def ordered_documents(
        cls, documents: tuple[SnapshotDocument, ...]
    ) -> tuple[SnapshotDocument, ...]:
        rows = tuple(document.manifest_row for document in documents)
        if rows != tuple(range(1, len(documents) + 1)):
            raise ValueError("snapshot documents must follow contiguous manifest order")
        keys = tuple(document.document_key for document in documents)
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot document keys must be unique")
        return documents

    @field_validator("chunks")
    @classmethod
    def ordered_chunks(cls, chunks: tuple[SnapshotChunk, ...]) -> tuple[SnapshotChunk, ...]:
        keys = tuple(chunk.chunk_key for chunk in chunks)
        if keys != tuple(sorted(keys)):
            raise ValueError("snapshot chunks must be ordered by chunk_key")
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot chunk keys must be unique")
        return chunks

    @field_validator("anchors")
    @classmethod
    def ordered_anchors(
        cls, anchors: tuple[SnapshotAnchor, ...]
    ) -> tuple[SnapshotAnchor, ...]:
        keys = tuple(anchor.anchor_key for anchor in anchors)
        if keys != tuple(sorted(keys)):
            raise ValueError("snapshot anchors must be ordered by anchor_key")
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot anchor keys must be unique")
        return anchors

    @model_validator(mode="after")
    def validate_graph_and_fingerprint(self) -> Self:
        if self.document_count != len(self.documents):
            raise ValueError("snapshot document_count does not match")
        if self.chunk_count != len(self.chunks):
            raise ValueError("snapshot chunk_count does not match")
        if self.anchor_count != len(self.anchors):
            raise ValueError("snapshot anchor_count does not match")
        document_keys = {document.document_key for document in self.documents}
        if any(chunk.document_key not in document_keys for chunk in self.chunks):
            raise ValueError("snapshot chunk refers to an unknown document")
        if any(anchor.document_key not in document_keys for anchor in self.anchors):
            raise ValueError("snapshot anchor refers to an unknown document")
        if self.corpus_fingerprint_sha256 != canonical_json_sha256(
            _fingerprint_payload(self)
        ):
            raise ValueError("corpus fingerprint does not match snapshot identities")
        return self

    @property
    def chunk_keys(self) -> tuple[str, ...]:
        return tuple(chunk.chunk_key for chunk in self.chunks)

    @property
    def summary_chunk_keys(self) -> frozenset[str]:
        return frozenset(
            chunk.chunk_key for chunk in self.chunks if chunk.block_type in {"title", "abstract"}
        )

    def chunk_keys_for_documents(self, document_keys: frozenset[str]) -> frozenset[str]:
        known = {document.document_key for document in self.documents}
        if not document_keys <= known:
            raise CorpusSnapshotError("document filter contains an unknown document key")
        return frozenset(
            chunk.chunk_key for chunk in self.chunks if chunk.document_key in document_keys
        )

    def documents_for_anchor_keys(self, anchor_keys: tuple[str, ...]) -> frozenset[str]:
        anchor_map = {anchor.anchor_key: anchor.document_key for anchor in self.anchors}
        if any(anchor_key not in anchor_map for anchor_key in anchor_keys):
            raise CorpusSnapshotError("anchor filter contains an unknown anchor key")
        return frozenset(anchor_map[anchor_key] for anchor_key in anchor_keys)


@dataclass(frozen=True, slots=True)
class PublishedCorpusSnapshot:
    """Snapshot paired with the gate-issued capability required for read-only FTS."""

    capability: CorpusCapability
    snapshot: CorpusSnapshot


def read_published_corpus_snapshot(
    engine: Engine,
    corpus_release_key: str,
) -> PublishedCorpusSnapshot:
    """Authorize and read one published corpus using a read-only transaction."""

    capability = PublishedCorpusGate(engine).authorize(corpus_release_key)
    if capability.status != "published":
        raise CorpusSnapshotError("ablation requires a published corpus release")
    try:
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            with connection.begin(), Session(bind=connection) as session:
                if connection.scalar(text("SHOW transaction_read_only")) != "on":
                    raise CorpusSnapshotError("corpus snapshot transaction is not read-only")
                document_rows = tuple(
                    session.execute(
                        select(Document, CorpusDocumentMembership.manifest_row)
                        .join(
                            CorpusDocumentMembership,
                            CorpusDocumentMembership.document_id == Document.id,
                        )
                        .where(CorpusDocumentMembership.release_id == capability.release_id)
                        .order_by(CorpusDocumentMembership.manifest_row)
                    )
                )
                chunk_rows = tuple(
                    session.execute(
                        select(DocumentChunk, Document.document_key)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(DocumentChunk.release_id == capability.release_id)
                        .order_by(DocumentChunk.chunk_key)
                    )
                )
                anchor_rows = tuple(
                    session.execute(
                        select(DocumentAnchor, Document.document_key)
                        .join(Document, Document.id == DocumentAnchor.document_id)
                        .where(DocumentAnchor.release_id == capability.release_id)
                        .order_by(DocumentAnchor.anchor_key)
                    )
                )
    except CorpusSnapshotError:
        raise
    except Exception as exc:
        raise CorpusSnapshotError("failed to read the published corpus snapshot") from exc

    documents = tuple(
        SnapshotDocument(
            document_key=document.document_key,
            manifest_row=int(manifest_row),
            source_artifact_sha256=document.source_artifact_sha256,
            normalized_document_sha256=document.normalized_document_sha256,
            byte_size=document.byte_size,
            title=document.title,
            title_sha256=hashlib.sha256(document.title.encode("utf-8")).hexdigest(),
        )
        for document, manifest_row in document_rows
    )
    chunks = tuple(
        SnapshotChunk(
            chunk_key=chunk.chunk_key,
            document_key=document_key,
            block_type=chunk.block_type,
            section_path=tuple(chunk.section_path),
            locator_sha256=canonical_json_sha256(chunk.locator),
            locator_text=chunk.locator_text,
            locator_text_sha256=hashlib.sha256(chunk.locator_text.encode("utf-8")).hexdigest(),
            text=chunk.text,
            text_sha256=chunk.text_sha256,
        )
        for chunk, document_key in chunk_rows
    )
    anchors = tuple(
        SnapshotAnchor(
            anchor_key=anchor.anchor_key,
            document_key=document_key,
            anchor_sha256=anchor.anchor_sha256,
        )
        for anchor, document_key in anchor_rows
    )
    snapshot = build_corpus_snapshot(
        corpus_release_key=capability.corpus_release_key,
        corpus_manifest_sha256=capability.manifest_sha256,
        policy_graph_sha256=capability.policy_graph_sha256,
        fts_policy_key=capability.fts_policy_key,
        retrieval_policy_key=capability.retrieval_policy_key,
        anchor_policy_key=capability.anchor_policy_key,
        validation_receipt_key=capability.validation_receipt_key,
        validation_receipt_sha256=capability.validation_receipt_sha256,
        documents=documents,
        chunks=chunks,
        anchors=anchors,
    )
    return PublishedCorpusSnapshot(capability=capability, snapshot=snapshot)


def build_corpus_snapshot(
    *,
    corpus_release_key: str,
    corpus_manifest_sha256: str,
    policy_graph_sha256: str,
    fts_policy_key: str,
    retrieval_policy_key: str,
    anchor_policy_key: str,
    validation_receipt_key: str,
    validation_receipt_sha256: str,
    documents: tuple[SnapshotDocument, ...],
    chunks: tuple[SnapshotChunk, ...],
    anchors: tuple[SnapshotAnchor, ...],
) -> CorpusSnapshot:
    """Build and self-checksum a snapshot from already ordered read-only rows."""

    payload: dict[str, object] = {
        "snapshot_schema_version": "embedding-ablation-corpus-snapshot-v1",
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "policy_graph_sha256": policy_graph_sha256,
        "fts_policy_key": fts_policy_key,
        "retrieval_policy_key": retrieval_policy_key,
        "anchor_policy_key": anchor_policy_key,
        "validation_receipt_key": validation_receipt_key,
        "validation_receipt_sha256": validation_receipt_sha256,
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "anchor_count": len(anchors),
        "documents": documents,
        "chunks": chunks,
        "anchors": anchors,
    }
    untyped_payload: dict[str, Any] = payload
    provisional = CorpusSnapshot.model_construct(
        **untyped_payload,
        corpus_fingerprint_sha256="0" * 64,
    )
    snapshot = CorpusSnapshot.model_validate(
        {
            **payload,
            "corpus_fingerprint_sha256": canonical_json_sha256(
                _fingerprint_payload(provisional)
            ),
        }
    )
    return snapshot


def assert_corpus_unchanged(before: CorpusSnapshot, after: CorpusSnapshot) -> None:
    """Fail a run if any checksum-bound production corpus identity changed."""

    if (
        before.corpus_release_key != after.corpus_release_key
        or before.corpus_fingerprint_sha256 != after.corpus_fingerprint_sha256
    ):
        raise CorpusSnapshotError("published corpus changed during the ablation")


def _fingerprint_payload(snapshot: CorpusSnapshot) -> dict[str, object]:
    return {
        "snapshot_schema_version": snapshot.snapshot_schema_version,
        "corpus_release_key": snapshot.corpus_release_key,
        "corpus_manifest_sha256": snapshot.corpus_manifest_sha256,
        "policy_graph_sha256": snapshot.policy_graph_sha256,
        "fts_policy_key": snapshot.fts_policy_key,
        "retrieval_policy_key": snapshot.retrieval_policy_key,
        "anchor_policy_key": snapshot.anchor_policy_key,
        "validation_receipt_key": snapshot.validation_receipt_key,
        "validation_receipt_sha256": snapshot.validation_receipt_sha256,
        "documents": [
            {
                "document_key": document.document_key,
                "manifest_row": document.manifest_row,
                "source_artifact_sha256": document.source_artifact_sha256,
                "normalized_document_sha256": document.normalized_document_sha256,
                "byte_size": document.byte_size,
                "title_sha256": document.title_sha256,
            }
            for document in snapshot.documents
        ],
        "chunks": [
            {
                "chunk_key": chunk.chunk_key,
                "document_key": chunk.document_key,
                "block_type": chunk.block_type,
                "section_path": chunk.section_path,
                "locator_sha256": chunk.locator_sha256,
                "locator_text_sha256": chunk.locator_text_sha256,
                "text_sha256": chunk.text_sha256,
            }
            for chunk in snapshot.chunks
        ],
        "anchors": snapshot.anchors,
    }
