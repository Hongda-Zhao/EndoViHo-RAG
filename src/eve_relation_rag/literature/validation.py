"""Exact rebuild validation for candidate and published literature corpora."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self, cast

from pydantic import Field, TypeAdapter, model_validator
from sqlalchemy import Engine, func, literal_column, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusRelease,
    Document,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
    EmbeddingModel,
)
from eve_relation_rag.literature.anchors import AnchorManifestEntry, CorpusAnchorManifest
from eve_relation_rag.literature.chunking import OffsetTokenizer, chunk_document
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    CorpusManifest,
    CorpusReleaseKey,
    RetrievalAnchor,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.embeddings import validate_embedding
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.ingestion import verify_source_artifact
from eve_relation_rag.literature.local_bge import LocalBgeProvider
from eve_relation_rag.literature.parsing import parse_document
from eve_relation_rag.literature.providers import (
    DeterministicFakeEmbeddingProvider,
    EmbeddingProvider,
)


class CorpusValidationError(RuntimeError):
    """Raised when validation cannot establish an auditable comparison boundary."""


_ANCHOR_ADAPTER: TypeAdapter[RetrievalAnchor] = TypeAdapter(RetrievalAnchor)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RebuildValidationReport(StrictFrozenSchema):
    """Immutable canonical result of rebuilding every derived corpus object."""

    validation_schema_version: Literal["corpus-rebuild-validation-v2"]
    corpus_release_key: CorpusReleaseKey
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ]
    model_artifact_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_kind: Literal["deterministic_fake", "local_bge", "unverified"]
    passed: bool
    findings: tuple[str, ...]
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    embedding_count: int = Field(ge=0)
    anchor_count: int = Field(ge=0)
    document_keys_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_rebuild_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_rebuild_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_rebuild_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_rebuild_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebuild_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_report_identity(self) -> Self:
        if self.passed != (not self.findings):
            raise ValueError("passed must equal the absence of validation findings")
        if self.embedding_count != self.chunk_count:
            raise ValueError("embedding_count must equal chunk_count")
        payload = self.model_dump(mode="python", exclude={"rebuild_sha256"})
        if self.rebuild_sha256 != canonical_json_sha256(payload):
            raise ValueError("rebuild_sha256 does not match the complete report")
        return self


def validate_corpus_rebuild(
    engine: Engine,
    *,
    manifest: CorpusManifest,
    import_root: Path,
    tokenizer: OffsetTokenizer,
    provider: EmbeddingProvider,
    anchor_manifest: CorpusAnchorManifest,
    batch_size: int = 500,
) -> RebuildValidationReport:
    """Rebuild source-derived identities in memory and compare every stored object."""

    if not 1 <= batch_size <= 500:
        raise CorpusValidationError("batch_size must be in 1..500")
    if (
        provider.model_key != manifest.embedding_model_key
        or provider.dimension != 384
        or not _SHA256_RE.fullmatch(provider.artifact_manifest_sha256)
    ):
        raise CorpusValidationError("validation provider does not match the manifest model")
    if (
        anchor_manifest.corpus_release_key != manifest.corpus_release_key
        or anchor_manifest.corpus_manifest_sha256 != manifest.manifest_sha256
        or anchor_manifest.anchor_policy_key != manifest.anchor_policy_key
    ):
        raise CorpusValidationError("anchor manifest does not bind the exact corpus manifest")

    expected_documents: list[dict[str, Any]] = []
    expected_chunks: list[dict[str, Any]] = []
    for spec in manifest.documents:
        payload = verify_source_artifact(spec, import_root)
        parsed = parse_document(spec.document_format, payload)
        if parsed.title != spec.title:
            raise CorpusValidationError(f"rebuilt title mismatch: {spec.relative_path}")
        chunks = chunk_document(
            parsed,
            corpus_release_key=manifest.corpus_release_key,
            document_key=spec.expected_document_key,
            tokenizer=tokenizer,
        )
        expected_documents.append(
            {
                "document_key": spec.expected_document_key,
                "manifest_row": spec.manifest_row,
                "normalized_document_sha256": parsed.normalized_document_sha256,
                "source_artifact_sha256": spec.source_sha256,
            }
        )
        expected_chunks.extend(
            {
                "block_type": chunk.block_type,
                "chunk_index": chunk.chunk_index,
                "chunk_key": chunk.chunk_key,
                "document_key": chunk.document_key,
                "locator": chunk.locator.model_dump(mode="json"),
                "locator_text": chunk.locator_text,
                "section_path": list(chunk.section_path),
                "text": chunk.text,
                "text_sha256": chunk.text_sha256,
                "token_count": chunk.token_count,
            }
            for chunk in chunks
        )

    expected_chunks.sort(key=lambda item: cast(str, item["chunk_key"]))
    expected_embeddings = _rebuild_embeddings(expected_chunks, provider, batch_size=batch_size)
    findings: list[str] = []

    try:
        with engine.connect().execution_options(postgresql_readonly=True) as connection:
            with Session(bind=connection) as session, session.begin():
                release_row = session.execute(
                    select(
                        CorpusRelease,
                        EmbeddingModel.artifact_manifest_sha256.label(
                            "model_artifact_manifest_sha256"
                        ),
                    )
                    .join(EmbeddingModel, EmbeddingModel.id == CorpusRelease.embedding_model_id)
                    .where(CorpusRelease.corpus_release_key == manifest.corpus_release_key)
                ).one_or_none()
                if release_row is None:
                    raise CorpusValidationError("corpus release was not found")
                release = release_row.CorpusRelease
                if release.manifest_sha256 != manifest.manifest_sha256:
                    findings.append("release_manifest_sha256_mismatch")
                if release.manifest_document_count != manifest.document_count:
                    findings.append("release_document_count_mismatch")
                if (
                    release_row.model_artifact_manifest_sha256
                    != provider.artifact_manifest_sha256
                ):
                    findings.append("model_artifact_manifest_sha256_mismatch")

                stored_documents = _stored_documents(session, release.id)
                if stored_documents != sorted(
                    expected_documents, key=lambda item: cast(str, item["document_key"])
                ):
                    findings.append("document_rebuild_mismatch")

                stored_chunks = _stored_chunks(session, release.id)
                if stored_chunks != expected_chunks:
                    findings.append("chunk_rebuild_mismatch")
                if not _fts_vectors_match(session, release.id, expected_chunks, manifest):
                    findings.append("fts_rebuild_mismatch")

                stored_embeddings = _stored_embeddings(
                    session,
                    release_id=release.id,
                    model_id=release.embedding_model_id,
                    provider=provider,
                )
                if stored_embeddings != expected_embeddings:
                    findings.append("embedding_rebuild_mismatch")

                stored_anchors, anchor_findings = _stored_anchors(session, release.id)
                findings.extend(anchor_findings)
                expected_anchors = [
                    entry.model_dump(mode="json") for entry in anchor_manifest.anchors
                ]
                if stored_anchors != expected_anchors:
                    findings.append("anchor_rebuild_mismatch")
                if not (
                    manifest.expected_chunk_count_min
                    <= len(stored_chunks)
                    <= manifest.expected_chunk_count_max
                ):
                    findings.append("chunk_count_outside_manifest_range")
                if len(stored_embeddings) != len(stored_chunks):
                    findings.append("embedding_count_mismatch")

                policy_graph_sha256 = release.policy_graph_sha256
    except CorpusValidationError:
        raise
    except Exception as exc:
        raise CorpusValidationError("rebuild validation query failed") from exc

    document_keys_sha256 = canonical_json_sha256(
        tuple(sorted(item["document_key"] for item in expected_documents))
    )
    sorted_expected_documents = sorted(
        expected_documents, key=lambda item: cast(str, item["document_key"])
    )
    document_rebuild_sha256 = canonical_json_sha256(sorted_expected_documents)
    chunk_rebuild_sha256 = canonical_json_sha256(expected_chunks)
    embedding_rebuild_sha256 = canonical_json_sha256(expected_embeddings)
    anchor_rebuild_sha256 = canonical_json_sha256(stored_anchors)
    unique_findings = tuple(sorted(set(findings)))
    report_payload: dict[str, Any] = {
        "validation_schema_version": "corpus-rebuild-validation-v2",
        "corpus_release_key": manifest.corpus_release_key,
        "manifest_sha256": manifest.manifest_sha256,
        "policy_graph_sha256": policy_graph_sha256,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "model_artifact_manifest_sha256": provider.artifact_manifest_sha256,
        "anchor_manifest_sha256": anchor_manifest.anchor_manifest_sha256,
        "provider_kind": _provider_kind(provider),
        "passed": not unique_findings,
        "findings": unique_findings,
        "document_count": len(expected_documents),
        "chunk_count": len(expected_chunks),
        "embedding_count": len(expected_embeddings),
        "anchor_count": len(stored_anchors),
        "document_keys_sha256": document_keys_sha256,
        "document_rebuild_sha256": document_rebuild_sha256,
        "anchor_rebuild_sha256": anchor_rebuild_sha256,
        "chunk_rebuild_sha256": chunk_rebuild_sha256,
        "embedding_rebuild_sha256": embedding_rebuild_sha256,
    }
    return RebuildValidationReport(
        **report_payload,
        rebuild_sha256=canonical_json_sha256(report_payload),
    )


def _provider_kind(provider: EmbeddingProvider) -> str:
    """Classify only the concrete verified adapter as receipt-eligible local BGE."""

    if type(provider) is LocalBgeProvider:
        return "local_bge"
    if type(provider) is DeterministicFakeEmbeddingProvider:
        return "deterministic_fake"
    return "unverified"


def _rebuild_embeddings(
    chunks: list[dict[str, Any]],
    provider: EmbeddingProvider,
    *,
    batch_size: int,
) -> list[dict[str, str]]:
    rebuilt: list[dict[str, str]] = []
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        vectors = provider.embed_documents(tuple(cast(str, item["text"]) for item in batch))
        if len(vectors) != len(batch):
            raise CorpusValidationError("provider did not return one rebuild vector per chunk")
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk_key = cast(str, chunk["chunk_key"])
            embedding = validate_embedding(
                vector,
                expected_dimension=384,
                model_key=provider.model_key,
                subject_key=chunk_key,
                mode="passage",
            )
            rebuilt.append({"chunk_key": chunk_key, "embedding_sha256": embedding.embedding_sha256})
    return rebuilt


def _stored_documents(session: Session, release_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Document.document_key,
            CorpusDocumentMembership.manifest_row,
            Document.normalized_document_sha256,
            Document.source_artifact_sha256,
        )
        .join(
            CorpusDocumentMembership,
            CorpusDocumentMembership.document_id == Document.id,
        )
        .where(CorpusDocumentMembership.release_id == release_id)
        .order_by(Document.document_key)
    ).all()
    return [
        {
            "document_key": row.document_key,
            "manifest_row": row.manifest_row,
            "normalized_document_sha256": row.normalized_document_sha256,
            "source_artifact_sha256": row.source_artifact_sha256,
        }
        for row in rows
    ]


def _stored_chunks(session: Session, release_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(DocumentChunk, Document.document_key)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.release_id == release_id)
        .order_by(DocumentChunk.chunk_key)
    ).all()
    return [
        {
            "block_type": row.DocumentChunk.block_type,
            "chunk_index": row.DocumentChunk.chunk_index,
            "chunk_key": row.DocumentChunk.chunk_key,
            "document_key": row.document_key,
            "locator": row.DocumentChunk.locator,
            "locator_text": row.DocumentChunk.locator_text,
            "section_path": row.DocumentChunk.section_path,
            "text": row.DocumentChunk.text,
            "text_sha256": row.DocumentChunk.text_sha256,
            "token_count": row.DocumentChunk.token_count,
        }
        for row in rows
    ]


def _stored_embeddings(
    session: Session,
    *,
    release_id: int,
    model_id: int,
    provider: EmbeddingProvider,
) -> list[dict[str, str]]:
    rows = session.execute(
        select(DocumentChunk.chunk_key, DocumentEmbedding)
        .join(
            DocumentEmbedding,
            (DocumentEmbedding.release_id == DocumentChunk.release_id)
            & (DocumentEmbedding.chunk_id == DocumentChunk.id),
        )
        .where(
            DocumentChunk.release_id == release_id,
            DocumentEmbedding.embedding_model_id == model_id,
        )
        .order_by(DocumentChunk.chunk_key)
    ).all()
    stored: list[dict[str, str]] = []
    for row in rows:
        validated = validate_embedding(
            tuple(float(value) for value in row.DocumentEmbedding.embedding),
            expected_dimension=384,
            model_key=provider.model_key,
            subject_key=row.chunk_key,
            mode="passage",
        )
        if validated.embedding_sha256 != row.DocumentEmbedding.embedding_sha256:
            raise CorpusValidationError(f"stored embedding checksum mismatch: {row.chunk_key}")
        stored.append({"chunk_key": row.chunk_key, "embedding_sha256": validated.embedding_sha256})
    return stored


def _fts_vectors_match(
    session: Session,
    release_id: int,
    chunks: Sequence[dict[str, Any]],
    manifest: CorpusManifest,
) -> bool:
    title_by_document = {spec.expected_document_key: spec.title for spec in manifest.documents}
    english: Any = literal_column("'english'::regconfig")
    for chunk in chunks:
        title = title_by_document[cast(str, chunk["document_key"])]
        section_label = " ".join(
            (*cast(list[str], chunk["section_path"]), cast(str, chunk["block_type"]))
        )
        expected = (
            func.setweight(func.to_tsvector(english, title), literal_column("'A'"))
            .op("||")(
                func.setweight(func.to_tsvector(english, section_label), literal_column("'B'"))
            )
            .op("||")(
                func.setweight(
                    func.to_tsvector(english, cast(str, chunk["text"])),
                    literal_column("'D'"),
                )
            )
        )
        matches = session.scalar(
            select(DocumentChunk.fts_document == expected).where(
                DocumentChunk.release_id == release_id,
                DocumentChunk.chunk_key == chunk["chunk_key"],
            )
        )
        if matches is not True:
            return False
    return True


def _stored_anchors(session: Session, release_id: int) -> tuple[list[dict[str, Any]], list[str]]:
    rows = session.execute(
        select(DocumentAnchor, Document.document_key)
        .join(Document, Document.id == DocumentAnchor.document_id)
        .where(DocumentAnchor.release_id == release_id)
        .order_by(DocumentAnchor.anchor_key)
    ).all()
    rebuilt: list[dict[str, Any]] = []
    findings: list[str] = []
    for result in rows:
        row = result.DocumentAnchor
        raw_entry: dict[str, Any] | None = None
        try:
            anchor = _anchor_contract(row)
            raw_entry = {
                "manifest_row": row.manifest_row,
                "document_key": result.document_key,
                "anchor": anchor.model_dump(mode="json"),
                "curation_method": row.curation_method,
                "source_locator": row.source_locator,
                "expected_anchor_sha256": row.anchor_sha256,
            }
            entry = AnchorManifestEntry.model_validate(raw_entry)
            rebuilt.append(entry.model_dump(mode="json"))
        except Exception:
            if raw_entry is not None:
                rebuilt.append(raw_entry)
            findings.append(f"anchor_manifest_entry_invalid:{row.anchor_key}")
    return rebuilt, findings


def _anchor_contract(row: DocumentAnchor) -> RetrievalAnchor:
    payload: dict[str, Any] = {"anchor_key": row.anchor_key, "anchor_type": row.anchor_type}
    if row.anchor_type == "locus":
        payload["locus_key"] = row.locus_key
    elif row.anchor_type == "assembly":
        payload["assembly_key"] = row.assembly_key
    elif row.anchor_type == "lineage":
        payload["snapshot_key"] = row.lineage_snapshot_key
        payload["term_key"] = row.lineage_term_key
    elif row.anchor_type == "method":
        payload["method_definition_key"] = row.method_definition_key
    elif row.anchor_type == "document":
        payload.update(
            document_key=row.target_document_key,
            doi=row.doi,
            pmid=row.pmid,
            pmcid=row.pmcid,
        )
    elif row.anchor_type == "keyword":
        payload["phrase"] = row.keyword_phrase
    else:
        raise CorpusValidationError("unknown stored anchor type")
    return _ANCHOR_ADAPTER.validate_python(payload)
