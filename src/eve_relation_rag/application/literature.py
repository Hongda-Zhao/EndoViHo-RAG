"""Fail-closed application service for one exact published literature corpus."""

from __future__ import annotations

from sqlalchemy import Engine

from eve_relation_rag.literature.candidate_gate import ValidatedCandidateGate
from eve_relation_rag.literature.capability import CorpusCapability
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    LiteratureRetrievalError,
    LiteratureRetrievalInvocation,
    RetrievedChunk,
    RetrievedChunks,
)
from eve_relation_rag.literature.embeddings import EmbeddingValidationError, validate_embedding
from eve_relation_rag.literature.errors import LiteratureRetrievalRefusal
from eve_relation_rag.literature.gate import PublishedCorpusGate
from eve_relation_rag.literature.hashing import canonical_query_sha256
from eve_relation_rag.literature.providers import EmbeddingProvider
from eve_relation_rag.literature.validation import RebuildValidationReport
from eve_relation_rag.retrieval.literature.repository import LiteratureRepository


class LiteratureRetrievalService:
    """Authorize, embed, retrieve, and render typed checksum-bound chunks."""

    def __init__(self, engine: Engine, provider: EmbeddingProvider) -> None:
        self._gate = PublishedCorpusGate(engine)
        self._repository = LiteratureRepository(engine)
        self._provider = provider

    def retrieve(
        self, invocation: LiteratureRetrievalInvocation
    ) -> RetrievedChunks | LiteratureRetrievalError:
        request = invocation.request
        try:
            capability = self._gate.authorize(request.corpus_release_key)
        except LiteratureRetrievalRefusal as exc:
            return _error_response(request.corpus_release_key, exc)
        return self.retrieve_authorized(invocation, capability)

    def retrieve_authorized(
        self,
        invocation: LiteratureRetrievalInvocation,
        capability: CorpusCapability,
    ) -> RetrievedChunks | LiteratureRetrievalError:
        """Retrieve with a gate-issued published or validation-only capability."""

        request = invocation.request
        query_sha256 = canonical_query_sha256(request, invocation.system_anchors)
        try:
            if request.corpus_release_key != capability.corpus_release_key:
                raise LiteratureRetrievalRefusal(
                    "corpus_manifest_invalid",
                    "request corpus does not match the issued retrieval capability",
                )
            if (
                self._provider.model_key != capability.embedding_model_key
                or self._provider.model_key != EMBEDDING_MODEL_KEY
                or self._provider.dimension != capability.embedding_dimension
                or self._provider.artifact_manifest_sha256
                != capability.model_artifact_manifest_sha256
            ):
                raise LiteratureRetrievalRefusal(
                    "embedding_model_mismatch",
                    "query provider does not match the corpus model",
                )
            try:
                raw_vector = self._provider.embed_query(request.question)
                query_embedding = validate_embedding(
                    raw_vector,
                    expected_dimension=capability.embedding_dimension,
                    model_key=capability.embedding_model_key,
                    subject_key=f"query:sha256:{query_sha256}",
                    mode="query",
                )
            except EmbeddingValidationError as exc:
                raise LiteratureRetrievalRefusal(
                    "embedding_provider_failed", "query embedding is invalid"
                ) from exc
            except LiteratureRetrievalRefusal:
                raise
            except Exception as exc:
                if "512 tokens" in str(exc):
                    raise LiteratureRetrievalRefusal(
                        "query_too_long", "query plus prefix exceeds the model token limit"
                    ) from exc
                raise LiteratureRetrievalRefusal(
                    "embedding_provider_failed", "query embedding provider failed"
                ) from exc

            repository_result = self._repository.retrieve(
                capability,
                question=request.question,
                query_vector=query_embedding.vector,
                anchors=invocation.system_anchors,
                top_k=request.top_k,
            )
            chunks = tuple(
                RetrievedChunk(
                    citation_id=f"D{index}",
                    chunk_key=hit.chunk_key,
                    document_key=hit.document_key,
                    title=hit.title,
                    doi=hit.doi,
                    pmid=hit.pmid,
                    pmcid=hit.pmcid,
                    section=hit.section,
                    locator=hit.locator,
                    locator_text=hit.locator_text,
                    text=hit.text,
                    text_sha256=hit.text_sha256,
                    retrieval_tier=hit.retrieval_tier,
                    fts_rank=hit.fts_rank,
                    vector_rank=hit.vector_rank,
                    summary_vector_rank=hit.summary_vector_rank,
                    rrf_score=hit.rrf_score,
                    matched_anchors=hit.matched_anchors,
                )
                for index, hit in enumerate(repository_result.hits, start=1)
            )
            return RetrievedChunks(
                result_schema_version="retrieved-chunks-v2",
                status="ok",
                corpus_release_key=capability.corpus_release_key,
                corpus_manifest_sha256=capability.manifest_sha256,
                retrieval_policy_key=RETRIEVAL_POLICY_KEY,
                embedding_model_key=EMBEDDING_MODEL_KEY,
                query_sha256=query_sha256,
                requested_top_k=request.top_k,
                returned_count=len(chunks),
                retrieval_executed=True,
                anchor_mode=("anchored_then_corpus_fill" if invocation.system_anchors else "none"),
                anchors_applied=invocation.system_anchors,
                warnings=repository_result.warnings,
                chunks=chunks,
            )
        except LiteratureRetrievalRefusal as exc:
            return _error_response(request.corpus_release_key, exc)


class CandidateBenchmarkService:
    """Pre-publication retriever scoped to one exact passing rebuild report."""

    def __init__(
        self,
        engine: Engine,
        provider: EmbeddingProvider,
        rebuild_report: RebuildValidationReport,
    ) -> None:
        self._capability = ValidatedCandidateGate(engine).authorize(rebuild_report)
        self._service = LiteratureRetrievalService(engine, provider)

    def retrieve(
        self, invocation: LiteratureRetrievalInvocation
    ) -> RetrievedChunks | LiteratureRetrievalError:
        return self._service.retrieve_authorized(invocation, self._capability)


def _error_response(
    corpus_release_key: str, refusal: LiteratureRetrievalRefusal
) -> LiteratureRetrievalError:
    return LiteratureRetrievalError(
        error_schema_version="literature-retrieval-error-v1",
        status="error",
        code=refusal.code,
        message=refusal.message,
        requested_corpus_release_key=corpus_release_key,
        retrieval_executed=refusal.retrieval_executed,
    )
