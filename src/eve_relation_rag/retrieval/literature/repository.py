"""Capability-scoped PostgreSQL FTS, pgvector, and anchor-first retrieval."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine, desc, func, literal_column, select, text
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Document, DocumentAnchor, DocumentChunk, DocumentEmbedding
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.contracts import (
    AssemblyAnchor,
    CanonicalLocator,
    KeywordAnchor,
    LineageAnchor,
    LocusAnchor,
    MethodAnchor,
    RetrievalAnchor,
    RetrievalTier,
    RetrievalWarning,
)
from eve_relation_rag.literature.contracts import (
    DocumentAnchor as DocumentAnchorContract,
)
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.hashing import canonical_json_sha256
from eve_relation_rag.retrieval.literature.fusion import FusedCandidate, fuse_ranked_candidates

_CANDIDATE_DEPTH = 100
_LOCATOR_ADAPTER: TypeAdapter[CanonicalLocator] = TypeAdapter(CanonicalLocator)


@dataclass(frozen=True, slots=True)
class RepositoryHit:
    """Fully hydrated fused chunk before public citation numbering."""

    chunk_key: str
    document_key: str
    title: str
    doi: str | None
    pmid: str | None
    pmcid: str | None
    section: str | None
    locator: CanonicalLocator
    locator_text: str
    text: str
    text_sha256: str
    retrieval_tier: RetrievalTier
    fts_rank: int | None
    vector_rank: int | None
    summary_vector_rank: int | None
    rrf_score: str
    matched_anchors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryResult:
    """Ordered retrieval hits plus contract warnings."""

    hits: tuple[RepositoryHit, ...]
    warnings: tuple[RetrievalWarning, ...]


class LiteratureRepository:
    """Run the approved retrieval plan only with a gate-issued corpus capability."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def retrieve(
        self,
        capability: CorpusCapability,
        *,
        question: str,
        query_vector: tuple[float, ...],
        anchors: tuple[RetrievalAnchor, ...],
        top_k: int,
    ) -> RepositoryResult:
        """Retrieve anchored tier first, then fill corpus-wide without duplicates."""

        try:
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with Session(bind=connection) as session, session.begin():
                    session.execute(text("SET LOCAL hnsw.ef_search = 100"))
                    session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
                    session.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))

                    document_anchor_keys = self._resolve_anchor_documents(
                        session, capability, anchors
                    )
                    warnings: list[RetrievalWarning] = []
                    selected: list[tuple[FusedCandidate, RetrievalTier]] = []
                    fts_indexable = self._fts_has_nodes(session, question)
                    if not fts_indexable:
                        warnings.append("fts_no_indexable_terms")

                    if anchors:
                        anchored_ids = tuple(sorted(document_anchor_keys))
                        anchored = self._retrieve_branch_fusion(
                            session,
                            capability,
                            question=question,
                            query_vector=query_vector,
                            document_ids=anchored_ids,
                            fts_indexable=fts_indexable,
                        )
                        if not anchored:
                            warnings.append("anchor_miss")
                        selected.extend((candidate, "anchored") for candidate in anchored[:top_k])

                    if len(selected) < top_k:
                        corpus_wide = self._retrieve_branch_fusion(
                            session,
                            capability,
                            question=question,
                            query_vector=query_vector,
                            document_ids=None,
                            fts_indexable=fts_indexable,
                        )
                        already_selected = {candidate.chunk_key for candidate, _tier in selected}
                        for candidate in corpus_wide:
                            if candidate.chunk_key in already_selected:
                                continue
                            selected.append((candidate, "corpus_fill"))
                            already_selected.add(candidate.chunk_key)
                            if len(selected) == top_k:
                                break

                    if not selected:
                        warnings.append("no_chunks_retrieved")
                    hits = self._hydrate(
                        session,
                        selected=tuple(selected),
                        document_anchor_keys=document_anchor_keys,
                    )
                    return RepositoryResult(hits=hits, warnings=tuple(warnings))
        except LiteratureRetrievalRefusal:
            raise
        except Exception as exc:
            raise LiteratureRetrievalRefusal(
                "retrieval_failed",
                "PostgreSQL literature retrieval failed",
                retrieval_executed=True,
            ) from exc

    @staticmethod
    def _fts_has_nodes(session: Session, question: str) -> bool:
        english: Any = literal_column("'english'::regconfig")
        tsquery = func.websearch_to_tsquery(english, question)
        return bool(session.scalar(select(func.numnode(tsquery))))

    def _retrieve_branch_fusion(
        self,
        session: Session,
        capability: CorpusCapability,
        *,
        question: str,
        query_vector: tuple[float, ...],
        document_ids: tuple[int, ...] | None,
        fts_indexable: bool,
    ) -> tuple[FusedCandidate, ...]:
        if document_ids == ():
            return ()
        fts_keys = (
            self._fts_candidates(
                session,
                capability,
                question=question,
                document_ids=document_ids,
            )
            if fts_indexable
            else ()
        )
        vector_keys = self._vector_candidates(
            session,
            capability,
            query_vector=query_vector,
            document_ids=document_ids,
        )
        summary_vector_keys = self._summary_vector_candidates(
            session,
            capability,
            query_vector=query_vector,
            document_ids=document_ids,
        )
        return fuse_ranked_candidates(
            fts_chunk_keys=fts_keys,
            vector_chunk_keys=vector_keys,
            summary_vector_chunk_keys=summary_vector_keys,
        )

    @staticmethod
    def _fts_candidates(
        session: Session,
        capability: CorpusCapability,
        *,
        question: str,
        document_ids: tuple[int, ...] | None,
    ) -> tuple[str, ...]:
        english: Any = literal_column("'english'::regconfig")
        tsquery = func.websearch_to_tsquery(english, question)
        rank = func.ts_rank_cd(DocumentChunk.fts_document, tsquery, 32)
        statement = (
            select(DocumentChunk.chunk_key)
            .where(
                DocumentChunk.release_id == capability.release_id,
                DocumentChunk.fts_document.op("@@")(tsquery),
            )
            .order_by(desc(rank), DocumentChunk.chunk_key)
            .limit(_CANDIDATE_DEPTH)
        )
        if document_ids is not None:
            statement = statement.where(DocumentChunk.document_id.in_(document_ids))
        return tuple(session.scalars(statement))

    @staticmethod
    def _vector_candidates(
        session: Session,
        capability: CorpusCapability,
        *,
        query_vector: tuple[float, ...],
        document_ids: tuple[int, ...] | None,
    ) -> tuple[str, ...]:
        distance = DocumentEmbedding.embedding.cosine_distance(list(query_vector))
        statement = (
            select(DocumentChunk.chunk_key)
            .join(
                DocumentEmbedding,
                (DocumentEmbedding.release_id == DocumentChunk.release_id)
                & (DocumentEmbedding.chunk_id == DocumentChunk.id),
            )
            .where(
                DocumentChunk.release_id == capability.release_id,
                DocumentEmbedding.embedding_model_id == capability.embedding_model_id,
            )
            .order_by(distance, DocumentChunk.chunk_key)
            .limit(_CANDIDATE_DEPTH)
        )
        if document_ids is not None:
            statement = statement.where(DocumentChunk.document_id.in_(document_ids))
        return tuple(session.scalars(statement))

    @staticmethod
    def _summary_vector_candidates(
        session: Session,
        capability: CorpusCapability,
        *,
        query_vector: tuple[float, ...],
        document_ids: tuple[int, ...] | None,
    ) -> tuple[str, ...]:
        """Rank only title/abstract chunks as the v2 summary-recall branch."""

        distance = DocumentEmbedding.embedding.cosine_distance(list(query_vector))
        statement = (
            select(DocumentChunk.chunk_key)
            .join(
                DocumentEmbedding,
                (DocumentEmbedding.release_id == DocumentChunk.release_id)
                & (DocumentEmbedding.chunk_id == DocumentChunk.id),
            )
            .where(
                DocumentChunk.release_id == capability.release_id,
                DocumentEmbedding.embedding_model_id == capability.embedding_model_id,
                DocumentChunk.block_type.in_(("title", "abstract")),
            )
            .order_by(distance, DocumentChunk.chunk_key)
            .limit(_CANDIDATE_DEPTH)
        )
        if document_ids is not None:
            statement = statement.where(DocumentChunk.document_id.in_(document_ids))
        return tuple(session.scalars(statement))

    @staticmethod
    def _resolve_anchor_documents(
        session: Session,
        capability: CorpusCapability,
        anchors: tuple[RetrievalAnchor, ...],
    ) -> dict[int, tuple[str, ...]]:
        if not anchors:
            return {}
        keys = tuple(anchor.anchor_key for anchor in anchors)
        rows = tuple(
            session.scalars(
                select(DocumentAnchor).where(
                    DocumentAnchor.release_id == capability.release_id,
                    DocumentAnchor.anchor_key.in_(keys),
                )
            )
        )
        by_key = {row.anchor_key: row for row in rows}
        matched: defaultdict[int, list[str]] = defaultdict(list)
        for anchor in anchors:
            row = by_key.get(anchor.anchor_key)
            if row is None or not _anchor_matches(anchor, row):
                raise LiteratureRetrievalRefusal(
                    "anchor_invalid",
                    f"curated anchor did not resolve exactly: {anchor.anchor_key}",
                )
            matched[row.document_id].append(anchor.anchor_key)
        return {
            document_id: tuple(sorted(anchor_keys)) for document_id, anchor_keys in matched.items()
        }

    @staticmethod
    def _hydrate(
        session: Session,
        *,
        selected: tuple[tuple[FusedCandidate, RetrievalTier], ...],
        document_anchor_keys: dict[int, tuple[str, ...]],
    ) -> tuple[RepositoryHit, ...]:
        if not selected:
            return ()
        keys = tuple(candidate.chunk_key for candidate, _tier in selected)
        rows = session.execute(
            select(
                DocumentChunk.chunk_key,
                DocumentChunk.document_id,
                Document.document_key,
                Document.title,
                Document.doi,
                Document.pmid,
                Document.pmcid,
                DocumentChunk.section_path,
                DocumentChunk.locator,
                DocumentChunk.locator_text,
                DocumentChunk.text,
                DocumentChunk.text_sha256,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.chunk_key.in_(keys))
        ).all()
        by_key = {row.chunk_key: row for row in rows}
        if len(by_key) != len(keys):
            raise LiteratureRetrievalRefusal(
                "corpus_incomplete", "a fused chunk could not be hydrated"
            )

        hits: list[RepositoryHit] = []
        for candidate, tier in selected:
            row = by_key[candidate.chunk_key]
            try:
                locator_payload = dict(cast(dict[str, Any], row.locator))
                # JSON arrays are the canonical persistence form for tuple-valued locator paths.
                for path_field in ("heading_path", "section_path"):
                    if path_field in locator_payload:
                        locator_payload[path_field] = tuple(locator_payload[path_field])
                locator = _LOCATOR_ADAPTER.validate_python(locator_payload)
            except ValidationError as exc:
                raise LiteratureRetrievalRefusal(
                    "chunk_locator_invalid", "stored chunk locator is invalid"
                ) from exc
            section_path = cast(list[str], row.section_path)
            hits.append(
                RepositoryHit(
                    chunk_key=row.chunk_key,
                    document_key=row.document_key,
                    title=row.title,
                    doi=row.doi,
                    pmid=row.pmid,
                    pmcid=row.pmcid,
                    section=" > ".join(section_path) or None,
                    locator=locator,
                    locator_text=row.locator_text,
                    text=row.text,
                    text_sha256=row.text_sha256,
                    retrieval_tier=tier,
                    fts_rank=candidate.fts_rank,
                    vector_rank=candidate.vector_rank,
                    summary_vector_rank=candidate.summary_vector_rank,
                    rrf_score=candidate.rrf_score,
                    matched_anchors=document_anchor_keys.get(row.document_id, ()),
                )
            )
        return tuple(hits)


def _anchor_matches(anchor: RetrievalAnchor, row: DocumentAnchor) -> bool:
    if row.anchor_type != anchor.anchor_type or row.anchor_sha256 != canonical_json_sha256(anchor):
        return False
    if isinstance(anchor, LocusAnchor):
        return row.locus_key == anchor.locus_key
    if isinstance(anchor, AssemblyAnchor):
        return row.assembly_key == anchor.assembly_key
    if isinstance(anchor, LineageAnchor):
        return (
            row.lineage_snapshot_key == anchor.snapshot_key
            and row.lineage_term_key == anchor.term_key
        )
    if isinstance(anchor, MethodAnchor):
        return row.method_definition_key == anchor.method_definition_key
    if isinstance(anchor, DocumentAnchorContract):
        return (
            row.target_document_key == anchor.document_key
            and row.doi == anchor.doi
            and row.pmid == anchor.pmid
            and row.pmcid == anchor.pmcid
        )
    if isinstance(anchor, KeywordAnchor):
        return row.keyword_phrase == anchor.phrase
