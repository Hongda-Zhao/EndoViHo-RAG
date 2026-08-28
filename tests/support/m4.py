"""Tests-only builders and deterministic provider for Milestone 4 benchmarks."""

from __future__ import annotations

import hashlib
from typing import Literal

from eve_relation_rag.generation.context import APPROVED_ANSWER_INSTRUCTIONS
from eve_relation_rag.hybrid.contracts import (
    GENERATED_DRAFT_VERSION,
    EvidenceSpan,
    GeneratedAnswerDraft,
    LiteratureClaim,
    ProviderIdentity,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    RETRIEVAL_POLICY_KEY,
    RETRIEVED_CHUNKS_VERSION,
    LiteratureRetrievalRequest,
    PlainTextLocator,
    RetrievedChunk,
    RetrievedChunks,
)
from eve_relation_rag.literature.hashing import canonical_query_sha256
from eve_relation_rag.planning.query_plans import canonical_plan_sha256
from eve_relation_rag.retrieval.structured.results import QuerySuccess
from tests.retrieval.hybrid.test_anchors import (
    _aggregate_success as _anchor_aggregate_success,
)
from tests.retrieval.hybrid.test_anchors import (
    _assembly_detail_success as _anchor_assembly_detail_success,
)
from tests.retrieval.hybrid.test_anchors import (
    _assembly_page_success as _anchor_assembly_page_success,
)
from tests.retrieval.hybrid.test_anchors import (
    _locus_detail_success as _anchor_locus_detail_success,
)
from tests.retrieval.hybrid.test_anchors import (
    _locus_page_success as _anchor_locus_page_success,
)
from tests.retrieval.hybrid.test_anchors import (
    _source_taxon_page_success as _anchor_source_taxon_page_success,
)

type StructuredVariant = Literal[
    "aggregate",
    "assembly_detail",
    "assembly_page",
    "locus_detail",
    "locus_page",
    "source_taxon_page",
]

TEST_CORPUS_RELEASE_KEY = "corpus:endoviho-rag:v0:20991231:999"
TEST_DOCUMENT_KEY = f"document:sha256:{'c' * 64}"
TEST_CHUNK_KEY = f"chunk:sha256:{'d' * 64}"


def make_structured_success(
    variant: StructuredVariant,
    *,
    structured_question: str,
) -> QuerySuccess:
    """Return one fully validated value for each of the six M2 data variants."""

    builders = {
        "aggregate": lambda: _anchor_aggregate_success(filtered=True),
        "assembly_detail": _anchor_assembly_detail_success,
        "assembly_page": _anchor_assembly_page_success,
        "locus_detail": _anchor_locus_detail_success,
        "locus_page": _anchor_locus_page_success,
        "source_taxon_page": _anchor_source_taxon_page_success,
    }
    success = builders[variant]()
    query_plan = success.query_plan.model_copy(update={"original_question": structured_question})
    structured_result = success.structured_result.model_copy(
        update={"plan_sha256": canonical_plan_sha256(query_plan)}
    )
    return QuerySuccess.model_validate(
        success.model_dump(mode="python")
        | {
            "query_plan": query_plan,
            "structured_result": structured_result,
        }
    )


def make_retrieved_chunks(*, question: str, text: str) -> RetrievedChunks:
    """Build one response-local D1 chunk with all supported document identifiers."""

    chunk = RetrievedChunk(
        citation_id="D1",
        chunk_key=TEST_CHUNK_KEY,
        document_key=TEST_DOCUMENT_KEY,
        title="Synthetic M4 benchmark document",
        doi="10.1234/synthetic.1",
        pmid="12345678",
        pmcid="PMC123456",
        section="Methods",
        locator=PlainTextLocator(
            locator_type="plain_text",
            paragraph_ordinal=1,
            line_start=1,
            line_end=1,
            token_start=None,
            token_end=None,
        ),
        locator_text="paragraph 1, line 1",
        text=text,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        retrieval_tier="corpus_fill",
        fts_rank=1,
        vector_rank=1,
        summary_vector_rank=1,
        rrf_score="0.049180327869",
        matched_anchors=(),
    )
    return RetrievedChunks(
        result_schema_version=RETRIEVED_CHUNKS_VERSION,
        status="ok",
        corpus_release_key=TEST_CORPUS_RELEASE_KEY,
        corpus_manifest_sha256="e" * 64,
        retrieval_policy_key=RETRIEVAL_POLICY_KEY,
        embedding_model_key=EMBEDDING_MODEL_KEY,
        query_sha256=canonical_query_sha256(
            LiteratureRetrievalRequest(
                request_schema_version="literature-retrieval-request-v1",
                corpus_release_key=TEST_CORPUS_RELEASE_KEY,
                question=question,
                top_k=8,
            ),
            (),
        ),
        requested_top_k=8,
        returned_count=1,
        retrieval_executed=True,
        anchor_mode="none",
        anchors_applied=(),
        warnings=(),
        chunks=(chunk,),
    )


def make_provider_identity(*, model_revision: str = "revision:tests:m4-v1") -> ProviderIdentity:
    """Return the exact tests-only provider and prompt-policy identity."""

    return ProviderIdentity(
        provider_key="provider:tests:m4-deterministic-v1",
        model_key="model:tests:m4-deterministic-v1",
        model_revision=model_revision,
        provider_artifact_sha256=None,
        generation_policy_key="generation:tests:m4-json-v1",
        prompt_policy_key=APPROVED_ANSWER_INSTRUCTIONS.instruction_policy_key,
        prompt_policy_sha256=APPROVED_ANSWER_INSTRUCTIONS.source_text_sha256,
        temperature=0,
        max_output_bytes=32768,
        timeout_seconds=5,
        retry_count=0,
    )


def make_generated_draft(
    *,
    context_sha256: str,
    claim_text: str,
    citation_id: str,
    evidence_quote: str,
) -> GeneratedAnswerDraft:
    """Build a strict one-claim draft; callers choose valid or adversarial values."""

    return GeneratedAnswerDraft.model_validate(
        {
            "draft_schema_version": GENERATED_DRAFT_VERSION,
            "context_sha256": context_sha256,
            "claims": (
                LiteratureClaim.model_validate(
                    {
                        "claim_id": "C1",
                        "claim_text": claim_text,
                        "citation_ids": (citation_id,),
                        "evidence_spans": (
                            EvidenceSpan.model_validate(
                                {"citation_id": citation_id, "quote": evidence_quote}
                            ),
                        ),
                    }
                ),
            ),
            "selected_limitation_codes": (
                "literature_evidence_is_explanatory",
                "mechanical_validation_is_not_semantic_entailment",
            ),
        }
    )


class DeterministicGenerationProvider:
    """Recording fake that returns exactly one caller-pinned string without I/O."""

    def __init__(self, *, identity: ProviderIdentity, output: str) -> None:
        self._identity = identity
        self.output = output
        self.calls: list[str] = []

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    def generate(self, context_json: str) -> str:
        self.calls.append(context_json)
        return self.output


__all__ = [
    "TEST_CORPUS_RELEASE_KEY",
    "DeterministicGenerationProvider",
    "StructuredVariant",
    "make_generated_draft",
    "make_provider_identity",
    "make_retrieved_chunks",
    "make_structured_success",
]
