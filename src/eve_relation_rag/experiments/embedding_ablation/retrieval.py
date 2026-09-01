"""Exact hybrid retrieval over a read-only FTS branch and sidecar dense index."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import Engine, desc, func, literal_column, select, text
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Document, DocumentChunk
from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    RankedCandidate,
    RetrievalTier,
)
from eve_relation_rag.experiments.embedding_ablation.corpus_snapshot import (
    CorpusSnapshot,
    PublishedCorpusSnapshot,
)
from eve_relation_rag.experiments.embedding_ablation.sidecar import ExactVectorIndex
from eve_relation_rag.retrieval.literature.fusion import FusedCandidate, fuse_ranked_candidates


class AblationRetrievalError(RuntimeError):
    """Raised when an experimental branch diverges from the frozen retrieval contract."""


class FtsCandidateProvider(Protocol):
    """Read-only PostgreSQL FTS rank provider used unchanged by every system."""

    def rank(
        self,
        question: str,
        *,
        allowed_document_keys: frozenset[str] | None,
        limit: int,
    ) -> tuple[str, ...]: ...


class PostgresFtsCandidateProvider:
    """Exact production-equivalent FTS query in a read-only transaction."""

    def __init__(self, engine: Engine, published: PublishedCorpusSnapshot) -> None:
        self._engine = engine
        self._release_id = published.capability.release_id
        self._known_document_keys = frozenset(
            document.document_key for document in published.snapshot.documents
        )
        self._known_chunk_keys = frozenset(published.snapshot.chunk_keys)

    def rank(
        self,
        question: str,
        *,
        allowed_document_keys: frozenset[str] | None,
        limit: int,
    ) -> tuple[str, ...]:
        if not question.strip():
            raise AblationRetrievalError("FTS question must not be empty")
        if limit != 100:
            raise AblationRetrievalError("FTS branch depth must remain 100")
        if (
            allowed_document_keys is not None
            and not allowed_document_keys <= self._known_document_keys
        ):
            raise AblationRetrievalError("FTS filter contains an unknown document key")
        english: Any = literal_column("'english'::regconfig")
        tsquery = func.websearch_to_tsquery(english, question)
        rank = func.ts_rank_cd(DocumentChunk.fts_document, tsquery, 32)
        statement = (
            select(DocumentChunk.chunk_key)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.release_id == self._release_id,
                DocumentChunk.fts_document.op("@@")(tsquery),
            )
            .order_by(desc(rank), DocumentChunk.chunk_key)
            .limit(limit)
        )
        if allowed_document_keys is not None:
            statement = statement.where(Document.document_key.in_(allowed_document_keys))
        try:
            with self._engine.connect().execution_options(postgresql_readonly=True) as connection:
                with connection.begin(), Session(bind=connection) as session:
                    if connection.scalar(text("SHOW transaction_read_only")) != "on":
                        raise AblationRetrievalError("FTS transaction is not read-only")
                    if not session.scalar(select(func.numnode(tsquery))):
                        return ()
                    keys = tuple(session.scalars(statement))
        except AblationRetrievalError:
            raise
        except Exception as exc:
            raise AblationRetrievalError("read-only PostgreSQL FTS failed") from exc
        if len(keys) != len(set(keys)) or any(key not in self._known_chunk_keys for key in keys):
            raise AblationRetrievalError("FTS branch returned invalid candidate keys")
        return keys


@dataclass(frozen=True, slots=True)
class CandidatePassage:
    """A ranked candidate plus memory-only text used by a reranker."""

    candidate: RankedCandidate
    passage: str


@dataclass(frozen=True, slots=True)
class AblationRetrievalResult:
    """Candidate pool before optional reranking."""

    candidates: tuple[CandidatePassage, ...]
    warnings: tuple[str, ...]


class AblationRetriever:
    """Preserve FTS/anchors/RRF while swapping only the sidecar dense representation."""

    def __init__(
        self,
        *,
        system: AblationSystem,
        snapshot: CorpusSnapshot,
        dense_index: ExactVectorIndex,
        fts_provider: FtsCandidateProvider,
    ) -> None:
        if (
            dense_index.model_key != system.embedding_model_key
            or dense_index.artifact_manifest_sha256
            != system.embedding_artifact_manifest_sha256
            or dense_index.representation.dimension != system.embedding_dimension
        ):
            raise AblationRetrievalError("dense sidecar identity does not match the system")
        if dense_index.chunk_keys != snapshot.chunk_keys:
            raise AblationRetrievalError("dense sidecar rows do not match the frozen chunks")
        self._system = system
        self._snapshot = snapshot
        self._dense_index = dense_index
        self._fts_provider = fts_provider
        self._chunk_by_key = {chunk.chunk_key: chunk for chunk in snapshot.chunks}

    def retrieve(
        self,
        *,
        question: str,
        query_vector: Sequence[float],
        anchor_keys: tuple[str, ...] = (),
    ) -> AblationRetrievalResult:
        """Build the system's fixed top-10 or top-20/50 reranking pool."""

        pool_depth = self._system.rerank_candidate_depth or self._system.top_k
        warnings: list[str] = []
        selected: list[tuple[FusedCandidate, RetrievalTier]] = []
        if anchor_keys:
            try:
                anchored_documents = self._snapshot.documents_for_anchor_keys(anchor_keys)
            except Exception as exc:
                raise AblationRetrievalError(
                    "one or more experiment anchors are unresolved"
                ) from exc
            anchored = self._fused_candidates(
                question=question,
                query_vector=query_vector,
                allowed_document_keys=anchored_documents,
            )
            if not anchored:
                warnings.append("anchor_miss")
            selected.extend((candidate, "anchored") for candidate in anchored[:pool_depth])

        if len(selected) < pool_depth:
            corpus_wide = self._fused_candidates(
                question=question,
                query_vector=query_vector,
                allowed_document_keys=None,
            )
            already_selected = {candidate.chunk_key for candidate, _tier in selected}
            for candidate in corpus_wide:
                if candidate.chunk_key in already_selected:
                    continue
                selected.append((candidate, "corpus_fill"))
                already_selected.add(candidate.chunk_key)
                if len(selected) == pool_depth:
                    break
        if not selected:
            warnings.append("no_chunks_retrieved")

        candidates = tuple(
            CandidatePassage(
                candidate=RankedCandidate(
                    chunk_key=fused.chunk_key,
                    retrieval_tier=tier,
                    pre_rerank_rank=rank,
                    fts_rank=fused.fts_rank,
                    vector_rank=fused.vector_rank,
                    summary_vector_rank=fused.summary_vector_rank,
                    rrf_score=fused.rrf_score,
                    reranker_score=None,
                    final_rank=(rank if self._system.rerank_candidate_depth is None else None),
                ),
                passage=self._chunk_by_key[fused.chunk_key].text,
            )
            for rank, (fused, tier) in enumerate(selected, start=1)
        )
        return AblationRetrievalResult(candidates=candidates, warnings=tuple(warnings))

    def _fused_candidates(
        self,
        *,
        question: str,
        query_vector: Sequence[float],
        allowed_document_keys: frozenset[str] | None,
    ) -> tuple[FusedCandidate, ...]:
        allowed_chunk_keys = (
            None
            if allowed_document_keys is None
            else self._snapshot.chunk_keys_for_documents(allowed_document_keys)
        )
        fts_keys = self._fts_provider.rank(
            question,
            allowed_document_keys=allowed_document_keys,
            limit=self._system.branch_candidate_depth,
        )
        full_hits = self._dense_index.rank(
            query_vector,
            allowed_chunk_keys=allowed_chunk_keys,
            limit=self._system.branch_candidate_depth,
        )
        summary_filter = self._snapshot.summary_chunk_keys
        if allowed_chunk_keys is not None:
            summary_filter = summary_filter & allowed_chunk_keys
        summary_hits = self._dense_index.rank(
            query_vector,
            allowed_chunk_keys=summary_filter,
            limit=self._system.branch_candidate_depth,
        )
        return fuse_ranked_candidates(
            fts_chunk_keys=fts_keys,
            vector_chunk_keys=tuple(hit.chunk_key for hit in full_hits),
            summary_vector_chunk_keys=tuple(hit.chunk_key for hit in summary_hits),
        )
