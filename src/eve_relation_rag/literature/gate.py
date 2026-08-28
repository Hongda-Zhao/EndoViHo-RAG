"""Fail-closed authorization for one exact published literature corpus."""

from __future__ import annotations

import re
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, aliased

from eve_relation_rag.db.models import (
    CorpusDocumentMembership,
    CorpusRelease,
    CorpusValidationReceipt,
    Document,
    DocumentAnchor,
    DocumentChunk,
    DocumentEmbedding,
    EmbeddingModel,
    LiteraturePolicy,
)
from eve_relation_rag.literature.capability import CorpusCapability, _issue_queryable_corpus
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    EMBEDDING_MODEL_KEY,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_REPOSITORY_ID,
    EMBEDDING_REVISION,
    FTS_POLICY_KEY,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    CorpusReleaseKey,
)
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.literature.receipt_integrity import (
    TrustedReceiptEvidence,
    validate_persisted_receipt,
)

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_POLICY_NAMES: Final = ("parser", "chunking", "fts", "retrieval", "anchor")
_EXPECTED_POLICY_KEYS: Final = {
    "parser": PARSER_POLICY_KEY,
    "chunking": CHUNKING_POLICY_KEY,
    "fts": FTS_POLICY_KEY,
    "retrieval": RETRIEVAL_POLICY_KEY,
    "anchor": ANCHOR_POLICY_KEY,
}


def _policy_integrity_columns(policy: Any, name: str) -> tuple[Any, ...]:
    """Return one consistently labelled policy identity projection."""

    return (
        policy.id.label(f"{name}_policy_id"),
        policy.policy_key.label(f"{name}_policy_key"),
        policy.policy_kind.label(f"{name}_policy_kind"),
        policy.schema_version.label(f"{name}_policy_schema_version"),
        policy.policy_json.label(f"{name}_policy_json"),
        policy.policy_sha256.label(f"{name}_policy_sha256"),
        policy.code_sha256.label(f"{name}_policy_code_sha256"),
    )


def _embedding_integrity_columns() -> tuple[Any, ...]:
    """Return the exact embedding-model identity projection used by both gates."""

    return (
        EmbeddingModel.id.label("embedding_model_id"),
        EmbeddingModel.model_key.label("embedding_model_key"),
        EmbeddingModel.provider_kind.label("embedding_provider_kind"),
        EmbeddingModel.repository_id.label("embedding_repository_id"),
        EmbeddingModel.revision.label("embedding_revision"),
        EmbeddingModel.dimension.label("embedding_dimension"),
        EmbeddingModel.max_sequence_tokens.label("embedding_max_sequence_tokens"),
        EmbeddingModel.pooling.label("embedding_pooling"),
        EmbeddingModel.l2_normalized.label("embedding_l2_normalized"),
        EmbeddingModel.passage_prefix.label("embedding_passage_prefix"),
        EmbeddingModel.query_prefix.label("embedding_query_prefix"),
        EmbeddingModel.similarity.label("embedding_similarity"),
        EmbeddingModel.license_key.label("embedding_license_key"),
        EmbeddingModel.artifact_manifest_sha256.label("model_artifact_manifest_sha256"),
        EmbeddingModel.model_metadata.label("embedding_model_metadata"),
    )


def _validate_policy_graph_identity(row: Any) -> None:
    """Recompute every immutable policy checksum and the release policy graph."""

    graph: dict[str, dict[str, str]] = {}
    for name in _POLICY_NAMES:
        policy_key = getattr(row, f"{name}_policy_key")
        policy_kind = getattr(row, f"{name}_policy_kind")
        schema_version = getattr(row, f"{name}_policy_schema_version")
        policy_json = getattr(row, f"{name}_policy_json")
        policy_sha256 = getattr(row, f"{name}_policy_sha256")
        code_sha256 = getattr(row, f"{name}_policy_code_sha256")
        expected_key = _EXPECTED_POLICY_KEYS[name]
        try:
            observed_policy_sha256 = canonical_json_sha256(policy_json)
        except Exception as exc:
            raise LiteratureRetrievalRefusal(
                "corpus_manifest_invalid", "corpus policy JSON is not canonical-hashable"
            ) from exc
        if (
            policy_kind != name
            or (expected_key is not None and policy_key != expected_key)
            or schema_version != "literature-policy-v1"
            or not isinstance(policy_sha256, str)
            or not _SHA256_RE.fullmatch(policy_sha256)
            or observed_policy_sha256 != policy_sha256
            or not isinstance(code_sha256, str)
            or not _SHA256_RE.fullmatch(code_sha256)
        ):
            raise LiteratureRetrievalRefusal(
                "corpus_manifest_invalid", "corpus policy identity is inconsistent"
            )
        graph[f"{name}_policy"] = {
            "policy_key": policy_key,
            "policy_sha256": policy_sha256,
        }

    model_expected = {
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "embedding_provider_kind": "local_hf",
        "embedding_repository_id": EMBEDDING_REPOSITORY_ID,
        "embedding_revision": EMBEDDING_REVISION,
        "embedding_dimension": 384,
        "embedding_max_sequence_tokens": 512,
        "embedding_pooling": "cls",
        "embedding_l2_normalized": True,
        "embedding_passage_prefix": "",
        "embedding_query_prefix": EMBEDDING_QUERY_PREFIX,
        "embedding_similarity": "cosine",
        "embedding_license_key": "MIT",
        "embedding_model_metadata": {"dtype": "float32", "offline_required": True},
    }
    if any(getattr(row, field) != expected for field, expected in model_expected.items()):
        raise LiteratureRetrievalRefusal(
            "embedding_model_mismatch", "corpus embedding model is inconsistent"
        )
    artifact_sha256 = row.model_artifact_manifest_sha256
    if not isinstance(artifact_sha256, str) or not _SHA256_RE.fullmatch(artifact_sha256):
        raise LiteratureRetrievalRefusal(
            "embedding_model_mismatch", "corpus embedding model is inconsistent"
        )

    graph["embedding_model"] = {
        "artifact_manifest_sha256": artifact_sha256,
        "model_key": row.embedding_model_key,
    }
    expected_graph_sha256 = canonical_json_sha256(
        {
            "anchor_policy": graph["anchor_policy"],
            "chunking_policy": graph["chunking_policy"],
            "embedding_model": graph["embedding_model"],
            "fts_policy": graph["fts_policy"],
            "parser_policy": graph["parser_policy"],
            "retrieval_policy": graph["retrieval_policy"],
        }
    )
    if row.policy_graph_sha256 != expected_graph_sha256:
        raise LiteratureRetrievalRefusal(
            "corpus_manifest_invalid", "corpus policy graph checksum is inconsistent"
        )


class PublishedCorpusGate:
    """Issue a capability only after exact publication and integrity checks."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def authorize(self, corpus_release_key: str) -> CorpusCapability:
        """Authorize one exact corpus before any FTS or vector retrieval."""

        try:
            exact_key: str = TypeAdapter(CorpusReleaseKey).validate_python(
                corpus_release_key, strict=True
            )
        except ValidationError as exc:
            raise LiteratureRetrievalRefusal(
                "unsupported_request",
                "corpus_release_key must be an exact EndoViHo-RAG V0 corpus key",
            ) from exc

        parser_policy = aliased(LiteraturePolicy)
        chunking_policy = aliased(LiteraturePolicy)
        fts_policy = aliased(LiteraturePolicy)
        retrieval_policy = aliased(LiteraturePolicy)
        anchor_policy = aliased(LiteraturePolicy)

        try:
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection) as session, session.begin():
                    row = session.execute(
                        select(
                            CorpusRelease.id.label("release_id"),
                            CorpusRelease.corpus_release_key,
                            CorpusRelease.status,
                            CorpusRelease.published_at,
                            CorpusRelease.manifest_sha256,
                            CorpusRelease.policy_graph_sha256,
                            CorpusRelease.manifest_document_count,
                            CorpusRelease.expected_chunk_count_min,
                            CorpusRelease.expected_chunk_count_max,
                            *_policy_integrity_columns(parser_policy, "parser"),
                            *_policy_integrity_columns(chunking_policy, "chunking"),
                            *_policy_integrity_columns(fts_policy, "fts"),
                            *_policy_integrity_columns(retrieval_policy, "retrieval"),
                            *_policy_integrity_columns(anchor_policy, "anchor"),
                            *_embedding_integrity_columns(),
                            CorpusValidationReceipt.receipt_key,
                            CorpusValidationReceipt.receipt_sha256,
                            CorpusValidationReceipt.status.label("receipt_status"),
                            CorpusValidationReceipt.trusted.label("receipt_trusted"),
                            CorpusValidationReceipt.manifest_sha256.label(
                                "receipt_manifest_sha256"
                            ),
                            CorpusValidationReceipt.policy_graph_sha256.label(
                                "receipt_policy_graph_sha256"
                            ),
                            CorpusValidationReceipt.rebuild_sha256.label(
                                "receipt_rebuild_sha256"
                            ),
                            CorpusValidationReceipt.benchmark_sha256.label(
                                "receipt_benchmark_sha256"
                            ),
                            CorpusValidationReceipt.validation_report.label(
                                "receipt_validation_report"
                            ),
                        )
                        .select_from(CorpusRelease)
                        .join(parser_policy, parser_policy.id == CorpusRelease.parser_policy_id)
                        .join(
                            chunking_policy,
                            chunking_policy.id == CorpusRelease.chunking_policy_id,
                        )
                        .join(fts_policy, fts_policy.id == CorpusRelease.fts_policy_id)
                        .join(
                            retrieval_policy,
                            retrieval_policy.id == CorpusRelease.retrieval_policy_id,
                        )
                        .join(anchor_policy, anchor_policy.id == CorpusRelease.anchor_policy_id)
                        .join(
                            EmbeddingModel,
                            EmbeddingModel.id == CorpusRelease.embedding_model_id,
                        )
                        .outerjoin(
                            CorpusValidationReceipt,
                            (CorpusValidationReceipt.release_id == CorpusRelease.id)
                            & (CorpusValidationReceipt.status == "passed")
                            & CorpusValidationReceipt.trusted,
                        )
                        .where(CorpusRelease.corpus_release_key == exact_key)
                    ).one_or_none()

                    if row is None:
                        raise LiteratureRetrievalRefusal(
                            "corpus_not_found", "corpus release was not found"
                        )
                    if row.status != "published" or row.published_at is None:
                        raise LiteratureRetrievalRefusal(
                            "corpus_not_published",
                            "corpus release is not published and cannot be queried",
                        )
                    evidence = self._validate_dependencies(row)
                    self._validate_completeness(session, row, evidence)
        except LiteratureRetrievalRefusal:
            raise
        except Exception as exc:
            raise LiteratureRetrievalRefusal(
                "retrieval_failed",
                "corpus authorization failed",
                retrieval_executed=False,
            ) from exc

        return _issue_queryable_corpus(
            release_id=row.release_id,
            corpus_release_key=row.corpus_release_key,
            status="published",
            published_at=row.published_at,
            manifest_sha256=row.manifest_sha256,
            policy_graph_sha256=row.policy_graph_sha256,
            validation_receipt_key=row.receipt_key,
            validation_receipt_sha256=row.receipt_sha256,
            parser_policy_id=row.parser_policy_id,
            parser_policy_key=row.parser_policy_key,
            chunking_policy_id=row.chunking_policy_id,
            chunking_policy_key=row.chunking_policy_key,
            fts_policy_id=row.fts_policy_id,
            fts_policy_key=row.fts_policy_key,
            retrieval_policy_id=row.retrieval_policy_id,
            retrieval_policy_key=row.retrieval_policy_key,
            anchor_policy_id=row.anchor_policy_id,
            anchor_policy_key=row.anchor_policy_key,
            embedding_model_id=row.embedding_model_id,
            embedding_model_key=row.embedding_model_key,
            embedding_dimension=row.embedding_dimension,
            model_artifact_manifest_sha256=row.model_artifact_manifest_sha256,
        )

    @staticmethod
    def _validate_dependencies(row: Any) -> TrustedReceiptEvidence:
        _validate_policy_graph_identity(row)
        if (
            row.receipt_key is None
            or row.receipt_sha256 is None
            or row.receipt_manifest_sha256 != row.manifest_sha256
            or row.receipt_policy_graph_sha256 != row.policy_graph_sha256
        ):
            raise LiteratureRetrievalRefusal(
                "corpus_receipt_invalid", "trusted corpus validation receipt is invalid"
            )
        try:
            return validate_persisted_receipt(
                release_corpus_key=row.corpus_release_key,
                release_manifest_sha256=row.manifest_sha256,
                release_policy_graph_sha256=row.policy_graph_sha256,
                release_embedding_model_key=row.embedding_model_key,
                release_model_artifact_manifest_sha256=(
                    row.model_artifact_manifest_sha256
                ),
                receipt_key=row.receipt_key,
                receipt_status=row.receipt_status,
                receipt_trusted=row.receipt_trusted,
                receipt_manifest_sha256=row.receipt_manifest_sha256,
                receipt_policy_graph_sha256=row.receipt_policy_graph_sha256,
                receipt_rebuild_sha256=row.receipt_rebuild_sha256,
                receipt_benchmark_sha256=row.receipt_benchmark_sha256,
                receipt_sha256=row.receipt_sha256,
                validation_report=row.receipt_validation_report,
            )
        except Exception as exc:
            raise LiteratureRetrievalRefusal(
                "corpus_receipt_invalid", "trusted corpus validation receipt is invalid"
            ) from exc

    @staticmethod
    def _validate_completeness(
        session: Session,
        row: Any,
        evidence: TrustedReceiptEvidence,
    ) -> None:
        rebuild = evidence.rebuild_report
        membership_count = session.scalar(
            select(func.count())
            .select_from(CorpusDocumentMembership)
            .where(CorpusDocumentMembership.release_id == row.release_id)
        )
        if (
            membership_count != row.manifest_document_count
            or membership_count != rebuild.document_count
        ):
            raise LiteratureRetrievalRefusal(
                "corpus_incomplete", "corpus document membership is incomplete"
            )

        invalid_license_count = session.scalar(
            select(func.count())
            .select_from(CorpusDocumentMembership)
            .join(Document, Document.id == CorpusDocumentMembership.document_id)
            .where(
                CorpusDocumentMembership.release_id == row.release_id,
                (Document.license_review_status != "approved") | ~Document.retrieval_text_allowed,
            )
        )
        if invalid_license_count:
            raise LiteratureRetrievalRefusal(
                "document_license_not_approved",
                "published corpus contains a document without approved retrieval-text rights",
            )

        chunk_count = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.release_id == row.release_id)
        )
        if (
            chunk_count is None
            or chunk_count < row.expected_chunk_count_min
            or chunk_count > row.expected_chunk_count_max
            or chunk_count != rebuild.chunk_count
        ):
            raise LiteratureRetrievalRefusal(
                "corpus_incomplete", "corpus chunk count is outside the approved manifest range"
            )

        missing_embedding_count = session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .outerjoin(
                DocumentEmbedding,
                (DocumentEmbedding.chunk_id == DocumentChunk.id)
                & (DocumentEmbedding.release_id == DocumentChunk.release_id)
                & (DocumentEmbedding.embedding_model_id == row.embedding_model_id),
            )
            .where(
                DocumentChunk.release_id == row.release_id,
                DocumentEmbedding.id.is_(None),
            )
        )
        if missing_embedding_count:
            raise LiteratureRetrievalRefusal(
                "embedding_incomplete", "published corpus has missing chunk embeddings"
            )

        embedding_count = session.scalar(
            select(func.count())
            .select_from(DocumentEmbedding)
            .where(
                DocumentEmbedding.release_id == row.release_id,
                DocumentEmbedding.embedding_model_id == row.embedding_model_id,
            )
        )
        if embedding_count != rebuild.embedding_count or embedding_count != chunk_count:
            raise LiteratureRetrievalRefusal(
                "embedding_incomplete", "published corpus embedding count is inconsistent"
            )

        anchor_count = session.scalar(
            select(func.count())
            .select_from(DocumentAnchor)
            .where(DocumentAnchor.release_id == row.release_id)
        )
        if anchor_count != rebuild.anchor_count:
            raise LiteratureRetrievalRefusal(
                "corpus_incomplete", "published corpus anchor count is inconsistent"
            )
