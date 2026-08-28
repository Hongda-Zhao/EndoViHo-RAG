"""Validation-only capability issuance for pre-publication pilot benchmarking."""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, aliased

from eve_relation_rag.db.models import CorpusRelease, EmbeddingModel, LiteraturePolicy
from eve_relation_rag.literature.capability import CorpusCapability, _issue_queryable_corpus
from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    CHUNKING_POLICY_KEY,
    EMBEDDING_MODEL_KEY,
    FTS_POLICY_KEY,
    PARSER_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
)
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.gate import (
    _embedding_integrity_columns,
    _policy_integrity_columns,
    _validate_policy_graph_identity,
)
from eve_relation_rag.literature.validation import RebuildValidationReport


class ValidatedCandidateGate:
    """Issue a non-public retrieval capability only from a passing exact rebuild report."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def authorize(self, report: RebuildValidationReport) -> CorpusCapability:
        try:
            report = RebuildValidationReport.model_validate(
                report.model_dump(mode="python")
            )
        except Exception as exc:
            raise LiteratureRetrievalRefusal(
                "corpus_manifest_invalid", "candidate rebuild report is invalid"
            ) from exc
        if not report.passed:
            raise LiteratureRetrievalRefusal(
                "corpus_incomplete", "candidate rebuild validation did not pass"
            )
        parser = aliased(LiteraturePolicy)
        chunking = aliased(LiteraturePolicy)
        fts = aliased(LiteraturePolicy)
        retrieval = aliased(LiteraturePolicy)
        anchor = aliased(LiteraturePolicy)
        with Session(self._engine) as session:
            row = session.execute(
                select(
                    CorpusRelease.id.label("release_id"),
                    CorpusRelease.corpus_release_key,
                    CorpusRelease.status,
                    CorpusRelease.created_at,
                    CorpusRelease.manifest_sha256,
                    CorpusRelease.policy_graph_sha256,
                    *_policy_integrity_columns(parser, "parser"),
                    *_policy_integrity_columns(chunking, "chunking"),
                    *_policy_integrity_columns(fts, "fts"),
                    *_policy_integrity_columns(retrieval, "retrieval"),
                    *_policy_integrity_columns(anchor, "anchor"),
                    *_embedding_integrity_columns(),
                )
                .select_from(CorpusRelease)
                .join(parser, parser.id == CorpusRelease.parser_policy_id)
                .join(chunking, chunking.id == CorpusRelease.chunking_policy_id)
                .join(fts, fts.id == CorpusRelease.fts_policy_id)
                .join(retrieval, retrieval.id == CorpusRelease.retrieval_policy_id)
                .join(anchor, anchor.id == CorpusRelease.anchor_policy_id)
                .join(EmbeddingModel, EmbeddingModel.id == CorpusRelease.embedding_model_id)
                .where(CorpusRelease.corpus_release_key == report.corpus_release_key)
            ).one_or_none()
        if row is None or row.status not in {"candidate", "validated"}:
            raise LiteratureRetrievalRefusal(
                "corpus_not_published", "validation target is not a candidate corpus"
            )
        if (
            row.manifest_sha256 != report.manifest_sha256
            or row.policy_graph_sha256 != report.policy_graph_sha256
            or row.model_artifact_manifest_sha256
            != report.model_artifact_manifest_sha256
        ):
            raise LiteratureRetrievalRefusal(
                "corpus_manifest_invalid", "candidate changed after rebuild validation"
            )
        _validate_policy_graph_identity(row)
        expected = (
            (row.parser_policy_key, PARSER_POLICY_KEY),
            (row.chunking_policy_key, CHUNKING_POLICY_KEY),
            (row.fts_policy_key, FTS_POLICY_KEY),
            (row.retrieval_policy_key, RETRIEVAL_POLICY_KEY),
            (row.anchor_policy_key, ANCHOR_POLICY_KEY),
            (row.embedding_model_key, EMBEDDING_MODEL_KEY),
            (row.embedding_dimension, 384),
        )
        if any(observed != required for observed, required in expected):
            raise LiteratureRetrievalRefusal(
                "corpus_manifest_invalid", "candidate policy graph is inconsistent"
            )
        return _issue_queryable_corpus(
            release_id=row.release_id,
            corpus_release_key=row.corpus_release_key,
            status="validation_candidate",
            published_at=row.created_at,
            manifest_sha256=row.manifest_sha256,
            policy_graph_sha256=row.policy_graph_sha256,
            validation_receipt_key="validation-candidate:no-receipt",
            validation_receipt_sha256="0" * 64,
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
