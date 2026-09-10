"""Versioned lexical retrieval for source-report experiments, without answer hints."""

from __future__ import annotations

import hashlib
import re
from collections import deque
from collections.abc import Sequence
from typing import Any

from sqlalchemy import case, desc, func, literal, literal_column, or_, select
from sqlalchemy.orm import Session

from eve_relation_rag.db.models import Document, DocumentChunk
from eve_relation_rag.experiments.rag_value_ablation.lexical_context import (
    LexicalContextPlan,
    build_lexical_context,
)
from eve_relation_rag.experiments.rag_value_ablation.lexical_query import LexicalQueryPlan
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.hashing import canonical_json_sha256

LEXICAL_CANDIDATE_LIMIT = 100
LEXICAL_SELECTION_POLICY = "object-round-robin-body-context-document-balanced-v1"


def merge_query_candidates(
    rankings: Sequence[Sequence[str]], *, limit: int = LEXICAL_CANDIDATE_LIMIT
) -> tuple[str, ...]:
    """Give each query an evidence slot before filling more slots from the same query."""
    if limit < 1:
        raise ValueError("candidate limit must be positive")
    queues = [deque(ranking) for ranking in rankings]
    selected: list[str] = []
    seen: set[str] = set()
    while any(queues) and len(selected) < limit:
        for queue in queues:
            while queue and queue[0] in seen:
                queue.popleft()
            if queue:
                key = queue.popleft()
                selected.append(key)
                seen.add(key)
            if len(selected) == limit:
                break
    return tuple(selected)


class PlannedFtsCandidateProvider:
    """One request's plan and audit trail; never changes the dense query or corpus scope."""

    def __init__(self, plan: LexicalQueryPlan) -> None:
        self.plan = plan
        self.calls: list[dict[str, Any]] = []
        self.context: LexicalContextPlan | None = None

    def _check_question(self, question: str) -> None:
        if hashlib.sha256(question.encode()).hexdigest() != self.plan.question_text_sha256:
            raise ValueError("lexical plan belongs to a different question")
        if self.context is None:
            self.context = build_lexical_context(question, self.plan)

    def has_indexable_terms(self, session: Session, question: str) -> bool:
        self._check_question(question)
        if self.plan.status != "ready" or self.context is None or self.context.status != "ready":
            return False
        english: Any = literal_column("'english'::regconfig")
        return any(
            session.scalar(select(func.numnode(func.plainto_tsquery(english, g.query_text))))
            for g in self.plan.groups
        )

    def candidates(
        self,
        session: Session,
        capability: CorpusCapability,
        *,
        question: str,
        document_ids: tuple[int, ...] | None,
    ) -> tuple[str, ...]:
        self._check_question(question)
        if self.plan.status != "ready" or self.context is None or self.context.status != "ready":
            return ()
        english: Any = literal_column("'english'::regconfig")
        group_results: list[dict[str, Any]] = []
        object_results: list[dict[str, Any]] = []
        rankings: list[tuple[str, ...]] = []
        scoped: list[Any] = [DocumentChunk.release_id == capability.release_id]
        if document_ids is not None:
            scoped.append(DocumentChunk.document_id.in_(document_ids))
        group_predicates: dict[str, Any] = {}
        group_scores: dict[str, Any] = {}
        groups = {g.group_key: g for g in self.plan.groups}
        for group in self.plan.groups:
            tsquery = func.plainto_tsquery(english, group.query_text)
            normalized, nodes = session.execute(
                select(func.text(tsquery), func.numnode(tsquery))
            ).one()
            if group.identifier is not None and group.kind in {"accession", "doi"}:
                # Versioned identifiers are exact lexical matches, not a bag of digit fragments.
                boundary = (
                    r"(?<![A-Za-z0-9_])" + re.escape(group.identifier)
                    + r"(?![A-Za-z0-9_]|[.][0-9])"
                )
                exact: Any = DocumentChunk.text.op("~*")(boundary)
                if group.kind == "doi":
                    exact = or_(exact, func.lower(Document.doi) == group.identifier.casefold())
                predicate = exact
            else:
                predicate = DocumentChunk.fts_document.op("@@")(tsquery)
            group_predicates[group.group_key] = predicate
            group_scores[group.group_key] = func.ts_rank_cd(
                DocumentChunk.fts_document, tsquery, 32,
            )
            total = session.scalar(
                select(func.count()).select_from(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(*scoped, predicate)
            )
            group_results.append({
                "group_key": group.group_key,
                "query_text": group.query_text,
                "normalized_tsquery": normalized,
                "tsquery_nodes": nodes,
                "identifier_exact_match": group.identifier,
                "total_matching_chunks": total,
            })
        for obj in self.context.objects:
            # An explicitly named accession remains mandatory within its object. A nearby
            # broad taxon must not turn an unknown accession into apparently valid evidence.
            accession_keys = [k for k in obj.group_keys if groups[k].kind == "accession"]
            doi_keys = [k for k in obj.group_keys if groups[k].kind == "doi"]
            admission_keys = accession_keys or list(obj.group_keys)
            predicates = [*scoped, or_(*(group_predicates[k] for k in admission_keys))]
            if doi_keys:
                predicates.append(or_(*(group_predicates[k] for k in doi_keys)))
            score = sum((group_scores[k] for k in obj.group_keys), literal(0.0))
            body = func.to_tsvector(english, DocumentChunk.text)
            # Count distinct requested concepts in passage bodies. Repeating an entity in
            # a document title cannot overwhelm a requested region/position/length here.
            context_score = sum((
                case((body.op("@@")(func.plainto_tsquery(english, term)), 1), else_=0)
                for term in obj.context_terms
            ), literal(0))
            order = (desc(context_score), desc(score), DocumentChunk.chunk_key)
            ranked = (
                select(
                    DocumentChunk.chunk_key,
                    score.label("score"),
                    context_score.label("context_score"),
                    func.row_number().over(
                        partition_by=DocumentChunk.document_id,
                        order_by=order,
                    ).label("document_rank"),
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(*predicates)
                .subquery()
            )
            keys = tuple(session.scalars(
                select(ranked.c.chunk_key)
                .order_by(desc(ranked.c.context_score), ranked.c.document_rank,
                          desc(ranked.c.score), ranked.c.chunk_key)
                .limit(LEXICAL_CANDIDATE_LIMIT)
            ))
            rankings.append(keys)
            object_results.append({
                "object_key": obj.object_key,
                "group_keys": obj.group_keys,
                "admission_group_keys": admission_keys,
                "context_terms": obj.context_terms,
                "returned_candidate_count": len(keys),
                "candidate_keys": keys,
            })
        keys = merge_query_candidates(rankings)
        self.calls.append({
            "release_id": capability.release_id,
            "corpus_release_key": capability.corpus_release_key,
            "document_ids": document_ids,
            "groups": group_results,
            "objects": object_results,
            "merged_candidate_count": len(keys),
            "merged_candidate_keys_sha256": canonical_json_sha256(keys),
        })
        return keys

    def diagnostics(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan": self.plan.to_dict(),
            "context": None if self.context is None else self.context.to_dict(),
            "candidate_limit": LEXICAL_CANDIDATE_LIMIT,
            "selection_policy": LEXICAL_SELECTION_POLICY,
            "calls": self.calls,
        }
        return {**result, "diagnostics_sha256": canonical_json_sha256(result)}
